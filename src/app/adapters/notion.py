"""Notion destination — one database row per record.

Property types are resolved by introspecting the database schema
(`GET /databases/{id}`) and matching record keys to property names, so
the record stays a plain dict of display names. The schema is fetched
once and refreshed if a record names a property the cached schema does
not know (someone added a column in Notion).

Supported property types: title, rich_text, number, select,
multi_select, date, relation, email, phone_number, url, checkbox.
Anything else is skipped with a warning.

Rate limiting: Notion allows roughly 3 requests/second and answers 429
with a Retry-After header. The adapter honors it for up to
MAX_RATE_LIMIT_RETRIES attempts per request, then raises
TransientDeliveryError so the worker backs off.

Env: NOTION_API_KEY (internal integration token), NOTION_DATABASE_ID.
The database must be shared with the integration.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.adapters.base import as_list, require_env, split_record
from app.pipeline import PermanentDeliveryError, TransientDeliveryError

logger = logging.getLogger(__name__)

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
DEFAULT_TIMEOUT = 30.0
MAX_RATE_LIMIT_RETRIES = 3
DEFAULT_RETRY_AFTER_SECONDS = 1.0
MAX_TEXT_LENGTH = 2000  # Notion's per-rich-text-object limit

SUPPORTED_TYPES = frozenset(
    {
        "title",
        "rich_text",
        "number",
        "select",
        "multi_select",
        "date",
        "relation",
        "email",
        "phone_number",
        "url",
        "checkbox",
    }
)
# Types whose values can be used in a `_key` equality filter.
_FILTERABLE_TEXT = frozenset({"title", "rich_text", "email", "phone_number", "url"})


class NotionAdapter:
    name = "notion"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        database_id: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.api_key = api_key or require_env("NOTION_API_KEY")
        self.database_id = database_id or require_env("NOTION_DATABASE_ID")
        self._sleep = sleep
        self._schema: dict[str, str] | None = None  # property name → type
        self._warned_line_items = False
        self._client = httpx.AsyncClient(
            base_url=NOTION_API_BASE,
            timeout=DEFAULT_TIMEOUT,
            transport=transport,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
        )

    # ------------------------------------------------------------------
    # Destination interface
    # ------------------------------------------------------------------

    async def upsert_record(self, record: dict[str, Any]) -> str:
        fields, meta = split_record(record)

        schema = await self._get_schema()
        if any(name not in schema for name in fields):
            schema = await self._get_schema(refresh=True)

        if meta.get("_line_items") and not self._warned_line_items:
            logger.warning("notion: _line_items are not supported for this destination — ignored")
            self._warned_line_items = True

        properties = self._build_properties(fields, meta, schema)

        key = meta.get("_key")
        if key and key in fields and fields[key] is not None:
            page_id = await self._find_page(key, fields[key], schema)
            if page_id:
                await self._request("PATCH", f"/pages/{page_id}", json={"properties": properties})
                return page_id

        data = await self._request(
            "POST",
            "/pages",
            json={"parent": {"database_id": self.database_id}, "properties": properties},
        )
        return str(data["id"])

    async def health_check(self) -> bool:
        try:
            await self._get_schema(refresh=True)
            return True
        except Exception as e:
            logger.warning("notion health_check failed", exc_info=e)
            return False

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    async def _get_schema(self, *, refresh: bool = False) -> dict[str, str]:
        if self._schema is None or refresh:
            data = await self._request("GET", f"/databases/{self.database_id}")
            self._schema = {
                name: prop.get("type", "") for name, prop in (data.get("properties") or {}).items()
            }
            logger.info(
                "notion schema loaded",
                extra={"database_id": self.database_id, "properties": len(self._schema)},
            )
        return self._schema

    # ------------------------------------------------------------------
    # Property encoding
    # ------------------------------------------------------------------

    def _build_properties(
        self, fields: dict[str, Any], meta: dict[str, Any], schema: dict[str, str]
    ) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        for name, value in fields.items():
            ptype = schema.get(name)
            if ptype is None:
                logger.warning("notion: database has no property %r — field skipped", name)
                continue
            if ptype not in SUPPORTED_TYPES:
                logger.warning("notion: property %r has unsupported type %r — skipped", name, ptype)
                continue
            if value is None:
                continue
            properties[name] = self._encode(ptype, value)

        # Every Notion row needs a title; fall back to _name if no field targets it.
        title_prop = next((n for n, t in schema.items() if t == "title"), None)
        if title_prop and title_prop not in properties and meta.get("_name"):
            properties[title_prop] = self._encode("title", meta["_name"])

        if not properties:
            raise PermanentDeliveryError("notion: no record fields matched the database schema")
        return properties

    @staticmethod
    def _text(value: Any) -> list[dict[str, Any]]:
        return [{"type": "text", "text": {"content": str(value)[:MAX_TEXT_LENGTH]}}]

    @classmethod
    def _encode(cls, ptype: str, value: Any) -> dict[str, Any]:
        match ptype:
            case "title":
                return {"title": cls._text(value)}
            case "rich_text":
                return {"rich_text": cls._text(value)}
            case "number":
                return {"number": float(value)}  # ValueError → permanent, by design
            case "select":
                return {"select": {"name": str(value)}}
            case "multi_select":
                return {"multi_select": [{"name": str(v)} for v in as_list(value)]}
            case "date":
                return {"date": {"start": str(value)}}
            case "relation":
                return {"relation": [{"id": str(v)} for v in as_list(value)]}
            case "email" | "phone_number" | "url":
                return {ptype: str(value)}
            case "checkbox":
                return {"checkbox": bool(value)}
        raise PermanentDeliveryError(f"notion: unsupported property type {ptype!r}")

    # ------------------------------------------------------------------
    # Upsert lookup
    # ------------------------------------------------------------------

    async def _find_page(self, key: str, value: Any, schema: dict[str, str]) -> str | None:
        ptype = schema.get(key)
        if ptype in _FILTERABLE_TEXT:
            flt: dict[str, Any] = {"property": key, ptype: {"equals": str(value)}}
        elif ptype == "number":
            flt = {"property": key, "number": {"equals": float(value)}}
        elif ptype == "select":
            flt = {"property": key, "select": {"equals": str(value)}}
        else:
            logger.warning("notion: _key %r has type %r which cannot be matched — creating", key, ptype)
            return None
        data = await self._request(
            "POST", f"/databases/{self.database_id}/query", json={"filter": flt, "page_size": 1}
        )
        results = data.get("results") or []
        return str(results[0]["id"]) if results else None

    # ------------------------------------------------------------------
    # HTTP with rate-limit handling
    # ------------------------------------------------------------------

    async def _request(
        self, method: str, path: str, *, json: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            resp = await self._client.request(method, path, json=json)
            if resp.status_code == 429 and attempt < MAX_RATE_LIMIT_RETRIES:
                delay = _retry_after(resp)
                logger.info("notion rate limited; sleeping %.1fs (attempt %d)", delay, attempt + 1)
                await self._sleep(delay)
                continue
            if resp.status_code == 429:
                raise TransientDeliveryError(
                    f"notion: still rate limited after {MAX_RATE_LIMIT_RETRIES} retries"
                )
            resp.raise_for_status()
            return resp.json() if resp.content else {}
        raise AssertionError("unreachable")  # pragma: no cover


def _retry_after(resp: httpx.Response) -> float:
    try:
        return max(0.0, float(resp.headers.get("Retry-After", DEFAULT_RETRY_AFTER_SECONDS)))
    except ValueError:
        return DEFAULT_RETRY_AFTER_SECONDS
