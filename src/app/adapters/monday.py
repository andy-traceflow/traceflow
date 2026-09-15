"""Monday.com destination.

Creates a parent item on the configured board and one subitem per entry
in `_line_items`. Column ids are resolved by display name on every call
— the adapter caches nothing, so renaming a column on the board takes
effect on the next event. Subitem columns are discovered by following
the parent board's `subtasks` column to the subitem board.

Env: MONDAY_API_KEY, MONDAY_BOARD_ID.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.adapters.base import display_name, require_env, split_record
from app.pipeline import PermanentDeliveryError, TransientDeliveryError

logger = logging.getLogger(__name__)

MONDAY_API_URL = "https://api.monday.com/v2"
MONDAY_API_VERSION = "2024-10"
DEFAULT_TIMEOUT = 30.0

_TRANSIENT_GRAPHQL_HINTS = ("complexity", "rate limit", "ratelimit", "429", "timeout")


@dataclass
class BoardColumns:
    parent: dict[str, str] = field(default_factory=dict)  # title → column id
    subitem: dict[str, str] = field(default_factory=dict)
    subitem_board_id: str | None = None


class MondayAdapter:
    name = "monday"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        board_id: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key or require_env("MONDAY_API_KEY")
        self.board_id = str(board_id or require_env("MONDAY_BOARD_ID"))
        self._client = httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT,
            transport=transport,
            headers={
                "Authorization": self.api_key,
                "Content-Type": "application/json",
                "API-Version": MONDAY_API_VERSION,
            },
        )

    # ------------------------------------------------------------------
    # Destination interface
    # ------------------------------------------------------------------

    async def upsert_record(self, record: dict[str, Any]) -> str:
        fields, meta = split_record(record)
        columns = await self._discover_columns()
        column_values = self._build_column_values(fields, columns.parent, scope="parent")

        key = meta.get("_key")
        if key:
            existing = await self._find_existing(key, fields, columns.parent)
            if existing:
                await self._change_column_values(existing, column_values)
                return existing

        item_id = await self._create_item(display_name(record), column_values)

        line_items = meta.get("_line_items") or []
        if line_items:
            await self._create_subitems(item_id, line_items, columns)
        return item_id

    async def health_check(self) -> bool:
        try:
            await self._request("query { me { id name } }", {})
            return True
        except Exception as e:
            logger.warning("monday health_check failed", exc_info=e)
            return False

    # ------------------------------------------------------------------
    # Column discovery — by display name, every call
    # ------------------------------------------------------------------

    _COLUMNS_QUERY = """
    query ($boardId: [ID!]) {
        boards(ids: $boardId) {
            columns { id title type settings_str }
        }
    }
    """

    async def _discover_columns(self) -> BoardColumns:
        data = await self._request(self._COLUMNS_QUERY, {"boardId": [self.board_id]})
        boards = data.get("boards") or []
        if not boards:
            raise PermanentDeliveryError(f"monday: board {self.board_id} not found")

        result = BoardColumns()
        for col in boards[0].get("columns") or []:
            result.parent[col["title"]] = col["id"]
            if col.get("type") == "subtasks":
                try:
                    ids = json.loads(col.get("settings_str") or "{}").get("boardIds") or []
                except json.JSONDecodeError:
                    ids = []
                if ids:
                    result.subitem_board_id = str(ids[0])

        if result.subitem_board_id:
            sub = await self._request(self._COLUMNS_QUERY, {"boardId": [result.subitem_board_id]})
            sub_boards = sub.get("boards") or []
            for col in (sub_boards[0].get("columns") if sub_boards else []) or []:
                result.subitem[col["title"]] = col["id"]

        logger.info(
            "monday columns discovered",
            extra={
                "board_id": self.board_id,
                "parent_count": len(result.parent),
                "subitem_count": len(result.subitem),
                "subitem_board_id": result.subitem_board_id,
            },
        )
        return result

    # ------------------------------------------------------------------
    # Column value composition
    # ------------------------------------------------------------------

    def _build_column_values(
        self, fields: dict[str, Any], title_to_id: dict[str, str], *, scope: str
    ) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for title, value in fields.items():
            if value is None:
                continue
            col_id = title_to_id.get(title)
            if not col_id:
                logger.warning(
                    "monday: no %s column titled %r on board — field skipped", scope, title
                )
                continue
            out[col_id] = self._serialize_column_value(value)
        return out

    @staticmethod
    def _serialize_column_value(value: Any) -> Any:
        """Wrap values in the shape Monday expects.

        Status/label columns want {"label": "..."}; everything else is a
        plain string. Dicts pass through untouched so a mapping can hand
        over a fully-formed column value.
        """
        if isinstance(value, dict):
            return value
        if isinstance(value, list | tuple):
            return ", ".join(str(v) for v in value)
        return str(value)

    # ------------------------------------------------------------------
    # Upsert lookup
    # ------------------------------------------------------------------

    async def _find_existing(
        self, key: str, fields: dict[str, Any], parent_cols: dict[str, str]
    ) -> str | None:
        value = fields.get(key)
        col_id = parent_cols.get(key)
        if value is None or not col_id:
            logger.warning("monday: _key %r has no value or no matching column — creating", key)
            return None
        query = """
        query ($boardId: ID!, $columnId: String!, $value: String!) {
            items_page_by_column_values(
                board_id: $boardId,
                limit: 1,
                columns: [{column_id: $columnId, column_values: [$value]}]
            ) { items { id } }
        }
        """
        data = await self._request(
            query, {"boardId": self.board_id, "columnId": col_id, "value": str(value)}
        )
        items = (data.get("items_page_by_column_values") or {}).get("items") or []
        return str(items[0]["id"]) if items else None

    # ------------------------------------------------------------------
    # Subitems
    # ------------------------------------------------------------------

    async def _create_subitems(
        self, parent_id: str, line_items: list[dict[str, Any]], columns: BoardColumns
    ) -> None:
        for li in line_items:
            li_fields, _ = split_record(li)
            name = li.get("_name") or " - ".join(
                p for p in (li.get("title"), li.get("variant_title")) if isinstance(p, str) and p
            ) or display_name(li)
            col_values = self._build_column_values(li_fields, columns.subitem, scope="subitem")
            try:
                await self._create_subitem(parent_id, name, col_values)
            except Exception as e:
                # One subitem failure should not abort the whole push — the
                # parent exists and a retry would duplicate it.
                logger.exception("monday subitem create failed", exc_info=e)

    # ------------------------------------------------------------------
    # GraphQL ops
    # ------------------------------------------------------------------

    async def _create_item(self, item_name: str, column_values: dict[str, Any]) -> str:
        query = """
        mutation ($boardId: ID!, $itemName: String!, $columnValues: JSON!) {
            create_item(
                board_id: $boardId,
                item_name: $itemName,
                column_values: $columnValues,
                create_labels_if_missing: true
            ) { id }
        }
        """
        data = await self._request(
            query,
            {
                "boardId": self.board_id,
                "itemName": item_name,
                "columnValues": json.dumps(column_values),
            },
        )
        return str(data["create_item"]["id"])

    async def _create_subitem(
        self, parent_id: str, item_name: str, column_values: dict[str, Any]
    ) -> str:
        query = """
        mutation ($parentItemId: ID!, $itemName: String!, $columnValues: JSON!) {
            create_subitem(
                parent_item_id: $parentItemId,
                item_name: $itemName,
                column_values: $columnValues
            ) { id }
        }
        """
        data = await self._request(
            query,
            {
                "parentItemId": parent_id,
                "itemName": item_name,
                "columnValues": json.dumps(column_values),
            },
        )
        return str(data["create_subitem"]["id"])

    async def _change_column_values(self, item_id: str, column_values: dict[str, Any]) -> None:
        query = """
        mutation ($boardId: ID!, $itemId: ID!, $columnValues: JSON!) {
            change_multiple_column_values(
                board_id: $boardId,
                item_id: $itemId,
                column_values: $columnValues,
                create_labels_if_missing: true
            ) { id }
        }
        """
        await self._request(
            query,
            {
                "boardId": self.board_id,
                "itemId": item_id,
                "columnValues": json.dumps(column_values),
            },
        )

    async def _request(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """POST one GraphQL document. Returns the `data` object.

        HTTP failures propagate as httpx.HTTPStatusError (the worker
        classifies them). GraphQL-level errors arrive with HTTP 200, so
        they are mapped here: complexity/rate-limit → transient, anything
        else → permanent.
        """
        resp = await self._client.post(
            MONDAY_API_URL, json={"query": query, "variables": variables}
        )
        resp.raise_for_status()
        body = resp.json()
        errors = body.get("errors") or ([body["error_message"]] if "error_message" in body else [])
        if errors:
            messages = "; ".join(
                e.get("message", str(e)) if isinstance(e, dict) else str(e) for e in errors
            )
            if any(hint in messages.lower() for hint in _TRANSIENT_GRAPHQL_HINTS):
                raise TransientDeliveryError(f"monday: {messages}")
            raise PermanentDeliveryError(f"monday: {messages}")
        return body.get("data") or {}
