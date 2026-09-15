"""Monday adapter: column ids resolved by display name, subitems from _line_items,
_key upsert, GraphQL error classification. All HTTP via MockTransport."""

from __future__ import annotations

import json

import httpx
import pytest

from app.adapters.monday import MondayAdapter
from app.pipeline import PermanentDeliveryError, TransientDeliveryError
from tests.adapters.conftest import Recorder, body_of, json_response

PARENT_COLUMNS = [
    {"id": "name", "title": "Name", "type": "name", "settings_str": "{}"},
    {"id": "text_1", "title": "Order ID", "type": "text", "settings_str": "{}"},
    {"id": "numbers_2", "title": "Total", "type": "numbers", "settings_str": "{}"},
    {"id": "status_3", "title": "Status", "type": "status", "settings_str": "{}"},
    {"id": "subitems_4", "title": "Subitems", "type": "subtasks", "settings_str": '{"boardIds": [777]}'},
]
SUBITEM_COLUMNS = [
    {"id": "name", "title": "Name", "type": "name", "settings_str": "{}"},
    {"id": "numbers_9", "title": "Quantity", "type": "numbers", "settings_str": "{}"},
]


def graphql_handler(*, existing_item_id: str | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        body = body_of(request)
        query, variables = body["query"], body["variables"]
        if "boards(ids" in query:
            board = str(variables["boardId"][0])
            cols = SUBITEM_COLUMNS if board == "777" else PARENT_COLUMNS
            return json_response(200, {"data": {"boards": [{"columns": cols}]}})
        if "items_page_by_column_values" in query:
            items = [{"id": existing_item_id}] if existing_item_id else []
            return json_response(200, {"data": {"items_page_by_column_values": {"items": items}}})
        if "create_item" in query:
            return json_response(200, {"data": {"create_item": {"id": "item-123"}}})
        if "create_subitem" in query:
            return json_response(200, {"data": {"create_subitem": {"id": "sub-1"}}})
        if "change_multiple_column_values" in query:
            return json_response(200, {"data": {"change_multiple_column_values": {"id": existing_item_id}}})
        if "me {" in query:
            return json_response(200, {"data": {"me": {"id": "u1", "name": "Bot"}}})
        return json_response(200, {"data": {}})

    return handler


def _adapter(rec: Recorder) -> MondayAdapter:
    return MondayAdapter(api_key="key", board_id="123", transport=rec.transport)


RECORD = {
    "_name": "Jon Snow / Night's Watch / 1001",
    "Order ID": "1001",
    "Total": 254.98,
    "Status": {"label": "New"},
    "Nonexistent Column": "skipped",
    "_line_items": [
        {"_name": "Widget - Blue", "Quantity": 2},
        {"title": "Gadget", "variant_title": "Large", "Quantity": 1},
    ],
}


async def test_creates_item_with_columns_resolved_by_display_name():
    rec = Recorder(graphql_handler())
    external_id = await _adapter(rec).upsert_record(RECORD)

    assert external_id == "item-123"
    create = next(b for b in rec.bodies() if "create_item" in b["query"])
    assert create["variables"]["boardId"] == "123"
    assert create["variables"]["itemName"] == "Jon Snow / Night's Watch / 1001"
    column_values = json.loads(create["variables"]["columnValues"])
    assert column_values == {"text_1": "1001", "numbers_2": "254.98", "status_3": {"label": "New"}}
    assert "Nonexistent Column" not in json.dumps(column_values)


async def test_creates_one_subitem_per_line_item_with_subitem_columns():
    rec = Recorder(graphql_handler())
    await _adapter(rec).upsert_record(RECORD)

    subs = [b for b in rec.bodies() if "create_subitem" in b["query"]]
    assert [s["variables"]["itemName"] for s in subs] == ["Widget - Blue", "Gadget - Large"]
    assert all(s["variables"]["parentItemId"] == "item-123" for s in subs)
    assert json.loads(subs[0]["variables"]["columnValues"]) == {"numbers_9": "2"}
    # Discovery followed the subtasks column to board 777.
    discovery = [b for b in rec.bodies() if "boards(ids" in b["query"]]
    assert [d["variables"]["boardId"] for d in discovery] == [["123"], ["777"]]


async def test_subitem_failure_does_not_abort_the_push():
    base = graphql_handler()

    def handler(request: httpx.Request) -> httpx.Response:
        if "create_subitem" in body_of(request)["query"]:
            return json_response(200, {"errors": [{"message": "subitem boom"}]})
        return base(request)

    rec = Recorder(handler)
    assert await _adapter(rec).upsert_record(RECORD) == "item-123"


async def test_key_match_updates_instead_of_creating():
    rec = Recorder(graphql_handler(existing_item_id="item-old"))
    record = {**RECORD, "_key": "Order ID"}
    external_id = await _adapter(rec).upsert_record(record)

    assert external_id == "item-old"
    queries = [b["query"] for b in rec.bodies()]
    assert not any("create_item" in q for q in queries)
    assert not any("create_subitem" in q for q in queries)
    update = next(b for b in rec.bodies() if "change_multiple_column_values" in b["query"])
    assert update["variables"]["itemId"] == "item-old"
    lookup = next(b for b in rec.bodies() if "items_page_by_column_values" in b["query"])
    assert lookup["variables"] == {"boardId": "123", "columnId": "text_1", "value": "1001"}


async def test_key_without_match_creates():
    rec = Recorder(graphql_handler(existing_item_id=None))
    assert await _adapter(rec).upsert_record({**RECORD, "_key": "Order ID"}) == "item-123"


async def test_graphql_complexity_error_is_transient():
    rec = Recorder(lambda r: json_response(200, {"errors": [{"message": "Complexity budget exhausted"}]}))
    with pytest.raises(TransientDeliveryError, match="Complexity"):
        await _adapter(rec).upsert_record(RECORD)


async def test_graphql_other_error_is_permanent():
    rec = Recorder(lambda r: json_response(200, {"errors": [{"message": "Column not found"}]}))
    with pytest.raises(PermanentDeliveryError, match="Column not found"):
        await _adapter(rec).upsert_record(RECORD)


async def test_http_error_propagates_for_worker_classification():
    rec = Recorder(lambda r: json_response(503, {"error": "down"}))
    with pytest.raises(httpx.HTTPStatusError) as exc:
        await _adapter(rec).upsert_record(RECORD)
    assert exc.value.response.status_code == 503


async def test_board_not_found_is_permanent():
    rec = Recorder(lambda r: json_response(200, {"data": {"boards": []}}))
    with pytest.raises(PermanentDeliveryError, match="not found"):
        await _adapter(rec).upsert_record(RECORD)


async def test_health_check():
    assert await _adapter(Recorder(graphql_handler())).health_check() is True
    assert await _adapter(Recorder(lambda r: json_response(401, {}))).health_check() is False


def test_serialize_column_value():
    s = MondayAdapter._serialize_column_value
    assert s({"label": "Done"}) == {"label": "Done"}
    assert s(["a", "b"]) == "a, b"
    assert s(254.98) == "254.98"
    assert s("x") == "x"


def test_sends_auth_and_api_version_headers():
    adapter = MondayAdapter(api_key="secret-key", board_id="1")
    assert adapter._client.headers["Authorization"] == "secret-key"
    assert adapter._client.headers["API-Version"] == "2024-10"
    assert adapter._client.timeout.read == 30.0
