"""Monday.com adapter.

push_lead creates a parent item on the configured board and a subitem
per line item attached to the lead. Column IDs are resolved by display
name on every push/update — the adapter holds no state, so a client
editing their field mappings takes effect on the next call.

The canonical Lead doesn't carry "line items" directly — they live in
`raw_payload['line_items']` when a Shopify webhook produced the lead.
For other source systems, the adapter creates the parent item with no
subitems.

Field mappings (Layer 2) translate canonical fields like sqft, service_type,
phone, email to whatever the client's Monday board calls them. The adapter
never hardcodes external field names.
"""

from __future__ import annotations

import asyncio
import json
import logging
from decimal import Decimal
from typing import Any

import httpx

from app.models.client_config import ClientConfig
from app.models.crm_contact import ContactType, CRMContact
from app.models.lead import Lead, LeadCreate
from app.services.field_mappings import apply_transform, resolve_mappings

logger = logging.getLogger(__name__)

MONDAY_API_URL = "https://api.monday.com/v2"
DEFAULT_TIMEOUT = 30.0
# Whole-lookup ceiling. Monday has no contact directory, so a phone search
# means column discovery + a board query — bounded here so it can never
# delay the missed-call SMS.
LOOKUP_TIMEOUT = 2.0


class MondayAdapter:
    name = "monday"

    # ------------------------------------------------------------------
    # CRMAdapter interface
    # ------------------------------------------------------------------

    async def push_lead(self, lead: Lead, config: ClientConfig) -> str:
        creds = self._creds(config)
        board_id = str(creds["board_id"])
        api_key = creds["api_key"]

        mappings = await resolve_mappings(lead.client_id, "monday")
        columns = await self._discover_columns(board_id, api_key, mappings)

        item_name = self._format_item_name(lead)
        column_values = self._build_parent_columns(lead, mappings, columns)

        parent_id = await self._create_item(
            api_key=api_key,
            board_id=board_id,
            item_name=item_name,
            column_values=column_values,
        )

        # Subitems from raw_payload line items, if present (Shopify-shaped).
        line_items = lead.raw_payload.get("line_items") or []
        if line_items:
            await self._create_subitems_for_line_items(
                api_key=api_key,
                parent_id=parent_id,
                line_items=line_items,
                columns=columns,
            )

        return parent_id

    async def update_lead(
        self,
        external_id: str,
        updates: dict[str, Any],
        config: ClientConfig,
    ) -> None:
        creds = self._creds(config)
        board_id = str(creds["board_id"])
        api_key = creds["api_key"]

        mappings = await resolve_mappings(config.client_id, "monday")
        columns = await self._discover_columns(board_id, api_key, mappings)
        parent_cols = columns["parent"]

        for canonical_field, value in updates.items():
            col_id = parent_cols.get(canonical_field)
            if not col_id:
                logger.warning("monday update: no mapping for canonical field", extra={"field": canonical_field})
                continue
            mapping = mappings.get(canonical_field)
            translated = apply_transform(value, mapping.transform if mapping else None)
            await self._change_column_value(
                api_key=api_key,
                board_id=board_id,
                item_id=external_id,
                column_id=col_id,
                value=self._serialize_column_value(translated),
            )

    async def parse_webhook(
        self,
        payload: dict[str, Any],
        config: ClientConfig,
    ) -> LeadCreate:
        # Monday webhooks come in many shapes; we don't currently subscribe
        # to any. When a use case appears (e.g. a client wants Monday-side
        # status changes to flow back into TraceFlow), implement the
        # specific event type here.
        return LeadCreate(
            client_id=config.client_id,
            source_system="monday",
            raw_payload=payload,
        )

    async def health_check(self, config: ClientConfig) -> bool:
        creds = self._creds(config)
        api_key = creds.get("api_key")
        if not api_key:
            return False
        try:
            await self._request(
                api_key=api_key,
                query="query { me { id name } }",
                variables={},
            )
            return True
        except Exception as e:
            logger.warning("monday health_check failed", exc_info=e)
            return False

    async def lookup_by_phone(
        self,
        phone: str,
        config: ClientConfig,
    ) -> CRMContact | None:
        """Best-effort phone lookup against the client's board.

        Monday is a project board, not a contact CRM: matching by phone
        depends on the client having mapped a phone column. When that isn't
        possible — no mapping, no match, timeout, or any error — this returns
        None and the caller proceeds as potential_lead. A found item is
        reported as contact_type=unknown; the board carries no reliable
        customer-vs-vendor signal, so disposition defers to the post-reply
        intent classifier.
        """
        try:
            return await asyncio.wait_for(
                self._lookup_impl(phone, config), timeout=LOOKUP_TIMEOUT
            )
        except Exception as e:
            logger.warning("monday lookup_by_phone failed/timed out", exc_info=e)
            return None

    async def fetch_recovered_value(
        self,
        external_id: str,
        config: ClientConfig,
    ) -> Decimal | None:
        """Not yet implemented for Monday — a project board has no native
        revenue concept. Recovered revenue for Monday clients is captured via
        owner_report (the admin outcome endpoint). Returning None keeps
        revenue_sync a clean no-op for this provider. TODO: read a mapped value
        column off the item.
        """
        return None

    async def _lookup_impl(self, phone: str, config: ClientConfig) -> CRMContact | None:
        try:
            creds = self._creds(config)
        except ValueError:
            return None
        board_id = str(creds["board_id"])
        api_key = creds["api_key"]

        mappings = await resolve_mappings(config.client_id, "monday")
        phone_mapping = mappings.get("phone")
        if not phone_mapping or phone_mapping.external_field_type != "column":
            return None

        columns = await self._discover_columns(board_id, api_key, mappings)
        phone_col = columns.get("parent", {}).get("phone")
        if not phone_col:
            return None

        items = await self._find_items_by_column(api_key, board_id, phone_col, phone)
        if not items:
            return None
        item = items[0]
        return CRMContact(
            external_id=str(item["id"]),
            name=item.get("name"),
            tags=[],
            contact_type=ContactType.unknown,
        )

    async def _find_items_by_column(
        self,
        api_key: str,
        board_id: str,
        column_id: str,
        value: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        query = """
        query ($boardId: ID!, $columnId: String!, $value: String!, $limit: Int!) {
            items_page_by_column_values(
                board_id: $boardId,
                limit: $limit,
                columns: [{column_id: $columnId, column_values: [$value]}]
            ) {
                items { id name }
            }
        }
        """
        data = await self._request(
            api_key,
            query,
            {"boardId": board_id, "columnId": column_id, "value": value, "limit": limit},
        )
        if not data:
            return []
        page = data.get("data", {}).get("items_page_by_column_values") or {}
        return page.get("items") or []

    # ------------------------------------------------------------------
    # Internals — column discovery
    # ------------------------------------------------------------------

    async def _discover_columns(
        self,
        board_id: str,
        api_key: str,
        mappings: dict[str, Any],
    ) -> dict[str, Any]:
        """Resolve column IDs by display name.

        Returns {"parent": {canonical_field: col_id},
                 "subitem": {canonical_field: col_id},
                 "subitem_board_id": <id> | None}.

        Resolves every canonical field with a mapping of
        external_field_type='column'. Subitem columns are resolved by
        following the parent board's 'subtasks' column to discover the
        subitem board ID, then querying its columns. Runs on every
        push/update — the adapter caches nothing, so a client editing
        their field mappings takes effect immediately.
        """
        empty: dict[str, Any] = {"parent": {}, "subitem": {}, "subitem_board_id": None}

        query = """
        query ($boardId: [ID!]) {
            boards(ids: $boardId) {
                columns { id title type settings_str }
            }
        }
        """
        data = await self._request(api_key, query, {"boardId": [board_id]})
        if not data or not data.get("data", {}).get("boards"):
            logger.error("monday column discovery: board not found", extra={"board_id": board_id})
            return empty

        parent_cols = data["data"]["boards"][0]["columns"]
        parent_map: dict[str, str] = {}
        subitem_board_id: str | None = None

        # Build a reverse lookup: external_field_display_name → canonical_field
        wanted_columns = {
            m.external_field: m.canonical_field
            for m in mappings.values()
            if m.external_field_type == "column"
        }

        for col in parent_cols:
            if col["title"] in wanted_columns:
                canonical = wanted_columns[col["title"]]
                parent_map[canonical] = col["id"]
            if col["type"] == "subtasks":
                try:
                    settings = json.loads(col["settings_str"])
                    ids = settings.get("boardIds") or []
                    if ids:
                        subitem_board_id = str(ids[0])
                except (json.JSONDecodeError, KeyError):
                    pass

        subitem_map: dict[str, str] = {}
        if subitem_board_id:
            sub_data = await self._request(api_key, query, {"boardId": [subitem_board_id]})
            sub_cols = (
                sub_data.get("data", {}).get("boards", [{}])[0].get("columns", [])
                if sub_data
                else []
            )
            for col in sub_cols:
                if col["title"] in wanted_columns:
                    subitem_map[wanted_columns[col["title"]]] = col["id"]
                # Always resolve "Quantity" by exact title (subitem convention)
                if col["title"].lower() == "quantity" and "quantity" not in subitem_map:
                    subitem_map["quantity"] = col["id"]

        logger.info(
            "monday columns discovered",
            extra={
                "board_id": board_id,
                "parent_count": len(parent_map),
                "subitem_count": len(subitem_map),
                "subitem_board_id": subitem_board_id,
            },
        )
        return {
            "parent": parent_map,
            "subitem": subitem_map,
            "subitem_board_id": subitem_board_id,
        }

    # ------------------------------------------------------------------
    # Internals — column value composition
    # ------------------------------------------------------------------

    def _format_item_name(self, lead: Lead) -> str:
        """`<Contact> / <Company> / <ExternalRef>` — Company is omitted when missing."""
        contact = lead.contact_name or "Unknown Contact"
        company = lead.contact_company or ""
        ref = lead.external_id or str(lead.id)[:8]
        if company:
            return f"{contact} / {company} / {ref}"
        return f"{contact} / {ref}"

    def _build_parent_columns(
        self,
        lead: Lead,
        mappings: dict[str, Any],
        columns: dict[str, Any],
    ) -> dict[str, Any]:
        """Translate canonical Lead fields → Monday column_values JSON."""
        parent_cols = columns.get("parent", {})

        out: dict[str, Any] = {}

        # Walk every canonical field we know about. The adapter never
        # hardcodes which canonical fields are "interesting" — the
        # client's mappings drive what gets sent.
        canonical_values = self._canonical_dict(lead)
        for canonical_field, col_id in parent_cols.items():
            value = canonical_values.get(canonical_field)
            if value is None:
                continue
            mapping = mappings.get(canonical_field)
            translated = apply_transform(value, mapping.transform if mapping else None)
            out[col_id] = self._serialize_column_value(translated)

        return out

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
        }

    @staticmethod
    def _serialize_column_value(value: Any) -> Any:
        """Wrap values in the shape Monday expects.

        Status/label columns want {"label": "..."}; everything else is a
        plain string. The caller is responsible for translating canonical
        values via apply_transform before reaching here.
        """
        if isinstance(value, dict):
            return value
        if isinstance(value, (list, tuple)):
            return ", ".join(str(v) for v in value)
        return str(value)

    # ------------------------------------------------------------------
    # Internals — subitems
    # ------------------------------------------------------------------

    async def _create_subitems_for_line_items(
        self,
        *,
        api_key: str,
        parent_id: str,
        line_items: list[dict[str, Any]],
        columns: dict[str, Any],
    ) -> None:
        subitem_cols = columns.get("subitem", {})
        qty_col = subitem_cols.get("quantity")

        for li in line_items:
            title = (li.get("title") or "").strip() or "Untitled"
            variant = (li.get("variant_title") or "").strip()
            parts = [p for p in [title, variant] if p]
            subitem_name = " - ".join(parts)
            quantity = li.get("quantity", 1)

            col_values: dict[str, Any] = {}
            if qty_col:
                col_values[qty_col] = str(quantity)

            try:
                await self._create_subitem(
                    api_key=api_key,
                    parent_id=parent_id,
                    item_name=subitem_name,
                    column_values=col_values,
                )
            except Exception as e:
                # One subitem failure should not abort the whole push.
                logger.exception("subitem create failed", exc_info=e)

    # ------------------------------------------------------------------
    # Internals — Monday GraphQL ops
    # ------------------------------------------------------------------

    async def _create_item(
        self,
        *,
        api_key: str,
        board_id: str,
        item_name: str,
        column_values: dict[str, Any],
    ) -> str:
        query = """
        mutation ($boardId: ID!, $itemName: String!, $columnValues: JSON!) {
            create_item(
                board_id: $boardId,
                item_name: $itemName,
                column_values: $columnValues,
                create_labels_if_missing: true
            ) {
                id
            }
        }
        """
        result = await self._request(
            api_key,
            query,
            {
                "boardId": board_id,
                "itemName": item_name,
                "columnValues": json.dumps(column_values),
            },
        )
        if not result or "data" not in result:
            raise RuntimeError(f"create_item failed: {result}")
        return result["data"]["create_item"]["id"]

    async def _create_subitem(
        self,
        *,
        api_key: str,
        parent_id: str,
        item_name: str,
        column_values: dict[str, Any],
    ) -> str:
        query = """
        mutation ($parentItemId: ID!, $itemName: String!, $columnValues: JSON!) {
            create_subitem(
                parent_item_id: $parentItemId,
                item_name: $itemName,
                column_values: $columnValues
            ) {
                id
            }
        }
        """
        result = await self._request(
            api_key,
            query,
            {
                "parentItemId": parent_id,
                "itemName": item_name,
                "columnValues": json.dumps(column_values),
            },
        )
        if not result or "data" not in result:
            raise RuntimeError(f"create_subitem failed: {result}")
        return result["data"]["create_subitem"]["id"]

    async def _change_column_value(
        self,
        *,
        api_key: str,
        board_id: str,
        item_id: str,
        column_id: str,
        value: Any,
    ) -> None:
        query = """
        mutation ($boardId: ID!, $itemId: ID!, $columnId: String!, $value: JSON!) {
            change_column_value(
                board_id: $boardId,
                item_id: $itemId,
                column_id: $columnId,
                value: $value
            ) { id }
        }
        """
        await self._request(
            api_key,
            query,
            {
                "boardId": board_id,
                "itemId": item_id,
                "columnId": column_id,
                "value": json.dumps(value) if not isinstance(value, str) else value,
            },
        )

    async def _request(
        self,
        api_key: str,
        query: str,
        variables: dict[str, Any],
    ) -> dict[str, Any] | None:
        headers = {"Authorization": api_key, "Content-Type": "application/json"}
        payload = {"query": query, "variables": variables}
        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                resp = await client.post(MONDAY_API_URL, headers=headers, json=payload)
        except httpx.HTTPError as e:
            logger.error("monday request HTTP error: %s", e)
            return None

        try:
            data = resp.json()
        except ValueError:
            logger.error("monday request: non-JSON response (status=%d)", resp.status_code)
            return None

        if "errors" in data:
            logger.error("monday API returned errors: %s", data["errors"])
            return None
        if resp.status_code != 200:
            logger.error("monday API returned status %d: %s", resp.status_code, data)
            return None
        return data

    # ------------------------------------------------------------------
    # Internals — credentials
    # ------------------------------------------------------------------

    @staticmethod
    def _creds(config: ClientConfig) -> dict[str, Any]:
        creds = config.crm_credentials or {}
        if "api_key" not in creds or "board_id" not in creds:
            raise ValueError(
                "monday adapter requires crm_credentials with 'api_key' and 'board_id'"
            )
        return creds
