"""HubSpot adapter.

push_lead creates a contact via the CRM v3 API; update_lead patches an
existing contact. HubSpot is offered for clients who already run it or
want enterprise-grade reporting (PRD §16 CRM matrix, 20–30% Solutions
Partner affiliate).

Auth is a per-client Private App access token stored in
client_configs.crm_credentials as {access_token}. The token is scoped to
a single HubSpot portal, so no OAuth flow is needed.

Field mappings (Layer 2) translate canonical Lead fields to HubSpot
contact properties. HubSpot has no GHL-style "custom field array" — both
standard and custom fields are just keys in the flat `properties` object,
so external_field_type 'standard' and 'custom_property' are handled the
same way. When a client has not configured a mapping for a canonical
field, a small default map covers the HubSpot standard properties
(firstname, phone, email, …) so a zero-config tenant still pushes; any
explicit mapping overrides the default per field.

The adapter holds no state — safe as a shared registry singleton.
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.models.client_config import ClientConfig
from app.models.crm_contact import ContactType, CRMContact
from app.models.lead import Lead, LeadCreate
from app.services.field_mappings import FieldMapping, apply_transform, resolve_mappings

logger = logging.getLogger(__name__)

HUBSPOT_API_BASE = "https://api.hubapi.com"
DEFAULT_TIMEOUT = 30.0
# Phone lookup runs on the missed-call hot path and must never delay the
# greeting SMS — a tight timeout caps it well under the 30s default.
LOOKUP_TIMEOUT = 2.0
# Revenue readback runs in the background revenue_sync job; bounded so a stalled
# CRM can't hang a sweep across many leads.
READBACK_TIMEOUT = 10.0
# Contact-level "total spent": HubSpot rolls associated closed-won deal amounts
# into this default property. The right unit here because recovered leads are
# new by construction (existing customers are filtered upstream).
TOTAL_REVENUE_PROPERTY = "total_revenue"

# Canonical field → HubSpot standard property, used only for canonical fields
# the client has NOT explicitly mapped. service_type / sqft / budget_range /
# timeframe have no HubSpot standard property, so they reach the CRM only via
# an explicit custom_property mapping — never invented here.
_DEFAULT_PROPERTY_MAP: dict[str, str] = {
    "contact_name": "firstname",  # whole name → firstname; split via an explicit mapping if needed
    "contact_company": "company",
    "phone": "phone",
    "email": "email",
    "address": "address",
}

# Properties fetched on a phone lookup so the result can be confirmed + classified.
_LOOKUP_PROPERTIES = [
    "firstname",
    "lastname",
    "phone",
    "mobilephone",
    "lifecyclestage",
    "type",
    "hs_lead_status",
]

# lifecyclestage / type substrings that mark a HubSpot contact as a
# vendor/partner. The authoritative vendor signal is the per-client
# vendor_allowlist (phone-based); this is a secondary, property-based hint.
_VENDOR_HINTS = ("vendor", "supplier", "subcontractor", "partner")

# HubSpot lifecyclestage values that read as a sales lead (re-engagement → still
# a potential lead). 'customer' is handled separately as an existing customer.
_LEAD_STAGES = frozenset(
    {
        "lead",
        "subscriber",
        "opportunity",
        "marketingqualifiedlead",
        "salesqualifiedlead",
        "evangelist",
    }
)


class HubSpotAdapter:
    name = "hubspot"

    # ------------------------------------------------------------------
    # CRMAdapter interface
    # ------------------------------------------------------------------

    async def push_lead(self, lead: Lead, config: ClientConfig) -> str:
        token = self._creds(config)["access_token"]

        mappings = await resolve_mappings(lead.client_id, "hubspot")
        properties = self._build_properties(self._canonical_dict(lead), mappings)

        result = await self._request(
            token=token,
            method="POST",
            path="/crm/v3/objects/contacts",
            json_body={"properties": properties},
        )
        if not result:
            raise RuntimeError("hubspot create contact failed")
        contact_id = result.get("id")
        if not contact_id:
            raise RuntimeError(f"hubspot create contact: no id in response: {result}")
        return str(contact_id)

    async def update_lead(
        self,
        external_id: str,
        updates: dict[str, Any],
        config: ClientConfig,
    ) -> None:
        token = self._creds(config)["access_token"]

        mappings = await resolve_mappings(config.client_id, "hubspot")
        properties = self._build_properties(updates, mappings)
        if not properties:
            logger.info(
                "hubspot update_lead: nothing mapped to update",
                extra={"contact_id": external_id},
            )
            return

        await self._request(
            token=token,
            method="PATCH",
            path=f"/crm/v3/objects/contacts/{external_id}",
            json_body={"properties": properties},
        )

    async def parse_webhook(
        self,
        payload: dict[str, Any],
        config: ClientConfig,
    ) -> LeadCreate:
        # HubSpot fires webhooks for many subscription types (contact.creation,
        # deal.propertyChange, …). TraceFlow doesn't subscribe to any yet —
        # bidirectional CRM sync is Phase 2. When a use case appears, branch on
        # payload's subscriptionType here.
        return LeadCreate(
            client_id=config.client_id,
            source_system="hubspot",
            raw_payload=payload,
        )

    async def health_check(self, config: ClientConfig) -> bool:
        try:
            token = self._creds(config)["access_token"]
        except ValueError:
            return False
        try:
            result = await self._request(
                token=token,
                method="GET",
                path="/crm/v3/objects/contacts",
                params={"limit": "1"},
            )
            return result is not None
        except Exception as e:
            logger.warning("hubspot health_check failed", exc_info=e)
            return False

    async def lookup_by_phone(
        self,
        phone: str,
        config: ClientConfig,
    ) -> CRMContact | None:
        """Best-effort: find a HubSpot contact by phone. None on miss/error.

        Returns None (never raises) so the caller can treat 'no answer' as
        'no match, proceed as potential_lead'. Bounded by LOOKUP_TIMEOUT so a
        slow HubSpot never delays the missed-call SMS.
        """
        try:
            return await asyncio.wait_for(
                self._lookup_impl(phone, config), timeout=LOOKUP_TIMEOUT
            )
        except Exception as e:
            logger.warning("hubspot lookup_by_phone failed/timed out", exc_info=e)
            return None

    async def _lookup_impl(self, phone: str, config: ClientConfig) -> CRMContact | None:
        try:
            token = self._creds(config)["access_token"]
        except ValueError:
            return None

        # HubSpot's search `query` does a broad contains-style match across
        # searchable properties (incl. the calculated phone). Formatting varies,
        # so we over-fetch and confirm on the last 10 digits below — a miss just
        # degrades to potential_lead, never a wrong disposition.
        result = await self._request(
            token=token,
            method="POST",
            path="/crm/v3/objects/contacts/search",
            json_body={"query": phone, "properties": _LOOKUP_PROPERTIES, "limit": 10},
            request_timeout=LOOKUP_TIMEOUT,
        )
        if not result:
            return None

        match = self._match_contact_by_phone(result.get("results") or [], phone)
        if match is None:
            return None
        return self._to_crm_contact(match)

    async def fetch_recovered_value(
        self,
        external_id: str,
        config: ClientConfig,
    ) -> Decimal | None:
        """Read the contact's HubSpot `total_revenue` (closed-won rollup).

        Best-effort: None on missing creds, unknown contact, no booked revenue
        yet, or any error — the revenue_sync job treats None as 'nothing
        confirmed yet' and leaves the lead's outcome untouched.
        """
        try:
            token = self._creds(config)["access_token"]
        except ValueError:
            return None

        result = await self._request(
            token=token,
            method="GET",
            path=f"/crm/v3/objects/contacts/{external_id}",
            params={"properties": TOTAL_REVENUE_PROPERTY},
            request_timeout=READBACK_TIMEOUT,
        )
        if not result:
            return None
        raw = (result.get("properties") or {}).get(TOTAL_REVENUE_PROPERTY)
        return self._parse_money(raw)

    @staticmethod
    def _parse_money(raw: Any) -> Decimal | None:
        """Coerce a HubSpot money property to a positive Decimal, else None."""
        if raw in (None, ""):
            return None
        try:
            value = Decimal(str(raw))
        except (InvalidOperation, ValueError):
            return None
        return value if value > 0 else None

    # ------------------------------------------------------------------
    # Internals — phone lookup
    # ------------------------------------------------------------------

    @staticmethod
    def _digits(value: str | None) -> str:
        return "".join(c for c in (value or "") if c.isdigit())

    def _match_contact_by_phone(
        self,
        results: list[dict[str, Any]],
        phone: str,
    ) -> dict[str, Any] | None:
        """Pick the contact whose phone really matches the query.

        HubSpot's broad search matches name/email too, so we confirm on the
        last 10 digits (checking both `phone` and `mobilephone`) to avoid a
        false positive routing a real lead to the wrong disposition.
        """
        want = self._digits(phone)
        if not want:
            return None
        tail = want[-10:]
        for result in results:
            if not result.get("id"):
                continue
            props = result.get("properties") or {}
            for candidate in (props.get("phone"), props.get("mobilephone")):
                cand = self._digits(candidate)
                if cand and (cand == want or cand.endswith(tail) or want.endswith(cand[-10:])):
                    return result
        return None

    def _to_crm_contact(self, result: dict[str, Any]) -> CRMContact:
        props = result.get("properties") or {}
        name = " ".join(
            p for p in (props.get("firstname"), props.get("lastname")) if p
        )
        return CRMContact(
            external_id=str(result["id"]),
            name=name or None,
            tags=[],
            contact_type=self._classify_contact(props),
        )

    @staticmethod
    def _classify_contact(props: dict[str, Any]) -> ContactType:
        lifecycle = str(props.get("lifecyclestage") or "").lower()
        hay = f"{lifecycle} {str(props.get('type') or '').lower()}"
        if any(hint in hay for hint in _VENDOR_HINTS):
            return ContactType.vendor
        if lifecycle == "customer":
            return ContactType.customer
        if lifecycle in _LEAD_STAGES:
            return ContactType.lead
        return ContactType.unknown

    # ------------------------------------------------------------------
    # Internals — properties composition
    # ------------------------------------------------------------------

    def _build_properties(
        self,
        canonical_values: dict[str, Any],
        mappings: dict[str, FieldMapping],
    ) -> dict[str, Any]:
        """Translate canonical field values into a flat HubSpot properties dict.

        Per field: an explicit mapping wins (its external_field is the property
        name, its transform is applied); otherwise the default standard-property
        map applies. A canonical field with neither a mapping nor a default is
        skipped, as is any field with no value.
        """
        properties: dict[str, Any] = {}
        for canonical_field, value in canonical_values.items():
            if value is None or value == "":
                continue
            mapping = mappings.get(canonical_field)
            if mapping is not None:
                prop_name: str | None = mapping.external_field
                translated = apply_transform(value, mapping.transform)
            else:
                prop_name = _DEFAULT_PROPERTY_MAP.get(canonical_field)
                translated = value
            if not prop_name:
                continue
            properties[prop_name] = translated
        return properties

    @staticmethod
    def _canonical_dict(lead: Lead) -> dict[str, Any]:
        return {
            "contact_name": lead.contact_name,
            "contact_company": lead.contact_company,
            "phone": lead.phone,
            "email": lead.email,
            "address": lead.address,
            "service_type": lead.service_type,
            "sqft": lead.sqft,
            "budget_range": lead.budget_range,
            "timeframe": lead.timeframe,
            "notes": lead.notes,
        }

    # ------------------------------------------------------------------
    # Internals — HTTP
    # ------------------------------------------------------------------

    async def _request(
        self,
        *,
        token: str,
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        request_timeout: float = DEFAULT_TIMEOUT,
    ) -> dict[str, Any] | None:
        """Call the HubSpot CRM v3 API. Returns parsed JSON, or None on failure."""
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        url = f"{HUBSPOT_API_BASE}{path}"
        try:
            async with httpx.AsyncClient(timeout=request_timeout) as client:
                resp = await client.request(
                    method, url, headers=headers, json=json_body, params=params
                )
        except httpx.HTTPError as e:
            logger.error("hubspot request HTTP error: %s", e)
            return None

        if resp.status_code >= 400:
            logger.error(
                "hubspot API error",
                extra={
                    "method": method,
                    "path": path,
                    "status": resp.status_code,
                    "body": resp.text[:500],
                },
            )
            return None

        if resp.status_code == 204 or not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            logger.error("hubspot request: non-JSON response (status=%d)", resp.status_code)
            return None

    # ------------------------------------------------------------------
    # Internals — credentials
    # ------------------------------------------------------------------

    @staticmethod
    def _creds(config: ClientConfig) -> dict[str, Any]:
        creds = config.crm_credentials or {}
        if "access_token" not in creds:
            raise ValueError(
                "hubspot adapter requires crm_credentials with 'access_token'"
            )
        return creds
