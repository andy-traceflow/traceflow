"""HubSpot destination (CRM v3 objects API).

Record keys are HubSpot property internal names (e.g. `email`,
`firstname`, or a custom property) — HubSpot's flat `properties` object
treats standard and custom properties the same way. Upsert: with `_key`
set, an existing object with that property value is PATCHed instead of
created; a 409 on create (HubSpot's own dedupe on `email`) is also
resolved to a PATCH.

Env: HUBSPOT_ACCESS_TOKEN (Private App token), HUBSPOT_OBJECT (default
`contacts`; also `companies`, `deals`, or a custom object type id).
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from app.adapters.base import optional_env, require_env, split_record
from app.pipeline import PermanentDeliveryError

logger = logging.getLogger(__name__)

HUBSPOT_API_BASE = "https://api.hubapi.com"
DEFAULT_TIMEOUT = 30.0

_EXISTING_ID_RE = re.compile(r"Existing ID:\s*(\d+)")


class HubSpotAdapter:
    name = "hubspot"

    def __init__(
        self,
        *,
        access_token: str | None = None,
        object_type: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.access_token = access_token or require_env("HUBSPOT_ACCESS_TOKEN")
        self.object_type = object_type or optional_env("HUBSPOT_OBJECT", "contacts")
        self._client = httpx.AsyncClient(
            base_url=HUBSPOT_API_BASE,
            timeout=DEFAULT_TIMEOUT,
            transport=transport,
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        self._warned_line_items = False

    # ------------------------------------------------------------------
    # Destination interface
    # ------------------------------------------------------------------

    async def upsert_record(self, record: dict[str, Any]) -> str:
        fields, meta = split_record(record)
        properties = {
            name: self._serialize(value)
            for name, value in fields.items()
            if value is not None and value != ""
        }
        if not properties:
            raise PermanentDeliveryError("hubspot: record has no non-empty properties")

        if meta.get("_line_items") and not self._warned_line_items:
            logger.warning("hubspot: _line_items are not supported for this destination — ignored")
            self._warned_line_items = True

        key = meta.get("_key")
        if key and key in properties:
            existing = await self._search(key, properties[key])
            if existing:
                await self._patch(existing, properties)
                return existing

        try:
            data = await self._request(
                "POST", f"/crm/v3/objects/{self.object_type}", json={"properties": properties}
            )
        except httpx.HTTPStatusError as e:
            existing = self._existing_id_from_conflict(e)
            if existing is None:
                raise
            await self._patch(existing, properties)
            return existing

        object_id = data.get("id")
        if not object_id:
            raise PermanentDeliveryError(f"hubspot: create returned no id: {data}")
        return str(object_id)

    async def health_check(self) -> bool:
        try:
            await self._request(
                "GET", f"/crm/v3/objects/{self.object_type}", params={"limit": "1"}
            )
            return True
        except Exception as e:
            logger.warning("hubspot health_check failed", exc_info=e)
            return False

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _serialize(value: Any) -> Any:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, list | tuple):
            return ";".join(str(v) for v in value)  # HubSpot multi-checkbox format
        return value

    @staticmethod
    def _existing_id_from_conflict(exc: httpx.HTTPStatusError) -> str | None:
        if exc.response.status_code != 409:
            return None
        match = _EXISTING_ID_RE.search(exc.response.text)
        return match.group(1) if match else None

    async def _search(self, property_name: str, value: Any) -> str | None:
        data = await self._request(
            "POST",
            f"/crm/v3/objects/{self.object_type}/search",
            json={
                "filterGroups": [
                    {
                        "filters": [
                            {"propertyName": property_name, "operator": "EQ", "value": str(value)}
                        ]
                    }
                ],
                "properties": [property_name],
                "limit": 1,
            },
        )
        results = data.get("results") or []
        return str(results[0]["id"]) if results else None

    async def _patch(self, object_id: str, properties: dict[str, Any]) -> None:
        await self._request(
            "PATCH",
            f"/crm/v3/objects/{self.object_type}/{object_id}",
            json={"properties": properties},
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resp = await self._client.request(method, path, json=json, params=params)
        resp.raise_for_status()
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()
