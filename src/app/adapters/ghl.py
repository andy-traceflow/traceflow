"""GoHighLevel adapter.

push_lead creates a contact in the client's GHL location; update_lead
patches an existing contact. GHL is TraceFlow's default CRM
recommendation for new clients (40% recurring affiliate).

Auth is a per-client Private Integration Token stored in
client_configs.crm_credentials as {api_key, location_id}. The token is
scoped to a single GHL location, so no OAuth flow is needed.

Field mappings (Layer 2) translate canonical Lead fields to GHL fields.
A mapping's external_field_type decides placement on the contact body:
  - 'standard'      -> a top-level key (firstName, phone, address1, ...)
  - 'custom_field'  -> an entry in the customFields array, keyed by the
                       GHL custom field id

The adapter holds no state — safe as a shared registry singleton.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.models.client_config import ClientConfig
from app.models.crm_contact import ContactType, CRMContact
from app.models.lead import Lead, LeadCreate
from app.services.field_mappings import (
    FieldMapping,
    apply_transform,
    dotted_qualification_data,
    resolve_mappings,
)

logger = logging.getLogger(__name__)

GHL_API_BASE = "https://services.leadconnectorhq.com"
# Required Version header for the v2 (LeadConnector) API. Confirm against
# the client's GHL when the first GHL client onboards.
GHL_API_VERSION = "2021-07-28"
DEFAULT_TIMEOUT = 30.0
# Phone lookup runs on the missed-call hot path and must never delay the
# greeting SMS — a tight timeout caps it well under the 30s default.
LOOKUP_TIMEOUT = 2.0
# Revenue readback runs in the background revenue_sync job; bounded so a stalled
# CRM can't hang a sweep across many leads.
READBACK_TIMEOUT = 10.0
# Tag substrings that mark a GHL contact as a vendor/partner rather than a
# customer or lead. The authoritative vendor signal is the per-client
# vendor_allowlist (phone-based); this is a secondary, tag-based hint.
_VENDOR_TAG_HINTS = ("vendor", "supplier", "subcontractor", "partner")


class GoHighLevelAdapter:
    name = "ghl"

    # ------------------------------------------------------------------
    # CRMAdapter interface
    # ------------------------------------------------------------------

    async def push_lead(self, lead: Lead, config: ClientConfig) -> str:
        creds = self._creds(config)
        api_key = creds["api_key"]

        mappings = await resolve_mappings(lead.client_id, "ghl")
        body = self._build_contact_body(self._canonical_dict(lead), mappings)
        body["locationId"] = str(creds["location_id"])

        result = await self._request(
            api_key=api_key,
            method="POST",
            path="/contacts/",
            json_body=body,
        )
        if not result:
            raise RuntimeError("ghl create contact failed")
        contact_id = (result.get("contact") or {}).get("id")
        if not contact_id:
            raise RuntimeError(f"ghl create contact: no id in response: {result}")
        return str(contact_id)

    async def update_lead(
        self,
        external_id: str,
        updates: dict[str, Any],
        config: ClientConfig,
    ) -> None:
        creds = self._creds(config)

        mappings = await resolve_mappings(config.client_id, "ghl")
        for canonical_field in updates:
            if canonical_field not in mappings:
                logger.warning(
                    "ghl update: no mapping for canonical field",
                    extra={"field": canonical_field},
                )

        body = self._build_contact_body(updates, mappings)
        if not body:
            logger.info("ghl update_lead: nothing mapped to update", extra={"contact_id": external_id})
            return

        # Update is scoped by the contact id in the path — no locationId in body.
        await self._request(
            api_key=creds["api_key"],
            method="PUT",
            path=f"/contacts/{external_id}",
            json_body=body,
        )

    async def parse_webhook(
        self,
        payload: dict[str, Any],
        config: ClientConfig,
    ) -> LeadCreate:
        # GHL fires webhooks for many event types (ContactCreate, status
        # changes, opportunity updates). TraceFlow doesn't subscribe to any
        # yet — bidirectional CRM sync is Phase 2. When a use case appears,
        # branch on payload['type'] here.
        return LeadCreate(
            client_id=config.client_id,
            source_system="ghl",
            raw_payload=payload,
        )

    async def health_check(self, config: ClientConfig) -> bool:
        try:
            creds = self._creds(config)
        except ValueError:
            return False
        try:
            result = await self._request(
                api_key=creds["api_key"],
                method="GET",
                path=f"/locations/{creds['location_id']}",
            )
            return result is not None
        except Exception as e:
            logger.warning("ghl health_check failed", exc_info=e)
            return False

    async def lookup_by_phone(
        self,
        phone: str,
        config: ClientConfig,
    ) -> CRMContact | None:
        """Best-effort: find a GHL contact by phone. None on miss/error.

        Returns None (never raises) so the caller can treat 'no answer' as
        'no match, proceed as potential_lead'. Bounded by LOOKUP_TIMEOUT so a
        slow GHL never delays the missed-call SMS.
        """
        try:
            creds = self._creds(config)
        except ValueError:
            return None

        result = await self._request(
            api_key=creds["api_key"],
            method="GET",
            path="/contacts/",
            params={"locationId": str(creds["location_id"]), "query": phone},
            request_timeout=LOOKUP_TIMEOUT,
        )
        if not result:
            return None

        match = self._match_contact_by_phone(result.get("contacts") or [], phone)
        if match is None:
            return None
        return self._to_crm_contact(match)

    async def fetch_recovered_value(
        self,
        external_id: str,
        config: ClientConfig,
    ) -> Decimal | None:
        """Sum the contact's won opportunities — GHL's equivalent of HubSpot's
        total_revenue rollup (ADR-0003 attribution unit: contact total spent).

        Best-effort: None on missing creds, unknown contact, no won
        opportunities yet, or any error — revenue_sync treats None as 'nothing
        confirmed yet', never a hard failure, and a non-positive total never
        overwrites a stored value.
        """
        try:
            creds = self._creds(config)
        except ValueError:
            return None

        # Unlike the contacts endpoints (camelCase locationId), GHL's
        # opportunity search takes snake_case params — an API quirk to confirm
        # against the client's GHL when the first mode='crm' GHL client
        # onboards. status='won' filters server-side; the loop below guards
        # client-side anyway in case the param is ignored. Default page size
        # (20) is plenty for one contact's won opportunities.
        result = await self._request(
            api_key=creds["api_key"],
            method="GET",
            path="/opportunities/search",
            params={
                "location_id": str(creds["location_id"]),
                "contact_id": external_id,
                "status": "won",
            },
            request_timeout=READBACK_TIMEOUT,
        )
        if not result:
            return None

        total = Decimal("0")
        for opportunity in result.get("opportunities") or []:
            if str(opportunity.get("status") or "").lower() != "won":
                continue
            value = self._parse_money(opportunity.get("monetaryValue"))
            if value is not None:
                total += value
        return total if total > 0 else None

    @staticmethod
    def _parse_money(raw: Any) -> Decimal | None:
        """Coerce a GHL money value to a positive Decimal, else None."""
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
        contacts: list[dict[str, Any]],
        phone: str,
    ) -> dict[str, Any] | None:
        """Pick the contact whose phone really matches the query.

        GHL's `query` search is fuzzy and matches on name/email too, so we
        confirm on the last 10 digits to avoid a false positive routing a
        real lead to the wrong disposition.
        """
        want = self._digits(phone)
        if not want:
            return None
        tail = want[-10:]
        for contact in contacts:
            if not contact.get("id"):
                continue
            cand = self._digits(contact.get("phone"))
            if cand and (cand == want or cand.endswith(tail) or want.endswith(cand[-10:])):
                return contact
        return None

    def _to_crm_contact(self, contact: dict[str, Any]) -> CRMContact:
        tags = [str(t) for t in (contact.get("tags") or [])]
        name = contact.get("contactName") or " ".join(
            p for p in (contact.get("firstName"), contact.get("lastName")) if p
        )
        return CRMContact(
            external_id=str(contact["id"]),
            name=name or None,
            tags=tags,
            contact_type=self._classify_contact(contact.get("type"), tags),
        )

    @staticmethod
    def _classify_contact(ghl_type: str | None, tags: list[str]) -> ContactType:
        lowered = [t.lower() for t in tags]
        if any(hint in t for t in lowered for hint in _VENDOR_TAG_HINTS):
            return ContactType.vendor
        if ghl_type == "customer":
            return ContactType.customer
        if ghl_type == "lead":
            return ContactType.lead
        return ContactType.unknown

    # ------------------------------------------------------------------
    # Internals — contact body composition
    # ------------------------------------------------------------------

    def _build_contact_body(
        self,
        canonical_values: dict[str, Any],
        mappings: dict[str, FieldMapping],
    ) -> dict[str, Any]:
        """Translate canonical field values into a GHL contact body.

        Mapping-driven: only canonical fields the client has mapped are
        sent. 'standard' fields become top-level keys; 'custom_field'
        fields are collected into the customFields array. Fields with no
        value are skipped.
        """
        body: dict[str, Any] = {}
        custom_fields: list[dict[str, Any]] = []

        for canonical_field, mapping in mappings.items():
            value = canonical_values.get(canonical_field)
            if value is None:
                continue
            translated = apply_transform(value, mapping.transform)
            if mapping.external_field_type == "custom_field":
                # GHL custom fields are referenced by id. The value key
                # ('field_value') should be confirmed when the first GHL
                # client onboards — GHL has used both 'field_value' and
                # 'value' across API revisions.
                custom_fields.append({"id": mapping.external_field, "field_value": translated})
            else:
                # 'standard' — a top-level contact key (firstName, phone, …)
                body[mapping.external_field] = translated

        if custom_fields:
            body["customFields"] = custom_fields
        return body

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
            "external_id": lead.external_id,
            # Non-canonical qualification fields, addressable as dotted paths.
            **dotted_qualification_data(lead),
        }

    # ------------------------------------------------------------------
    # Internals — HTTP
    # ------------------------------------------------------------------

    async def _request(
        self,
        *,
        api_key: str,
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        request_timeout: float = DEFAULT_TIMEOUT,
    ) -> dict[str, Any] | None:
        """Call the GHL v2 REST API. Returns parsed JSON, or None on failure."""
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Version": GHL_API_VERSION,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        url = f"{GHL_API_BASE}{path}"
        try:
            async with httpx.AsyncClient(timeout=request_timeout) as client:
                resp = await client.request(
                    method, url, headers=headers, json=json_body, params=params
                )
        except httpx.HTTPError as e:
            logger.error("ghl request HTTP error: %s", e)
            return None

        if resp.status_code >= 400:
            logger.error(
                "ghl API error",
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
            logger.error("ghl request: non-JSON response (status=%d)", resp.status_code)
            return None

    # ------------------------------------------------------------------
    # Internals — credentials
    # ------------------------------------------------------------------

    @staticmethod
    def _creds(config: ClientConfig) -> dict[str, Any]:
        creds = config.crm_credentials or {}
        if "api_key" not in creds or "location_id" not in creds:
            raise ValueError(
                "ghl adapter requires crm_credentials with 'api_key' and 'location_id'"
            )
        return creds
