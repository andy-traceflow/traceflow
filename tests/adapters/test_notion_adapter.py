"""Notion adapter: schema-driven property encoding, title fallback, _key query → PATCH,
429 handling with Retry-After."""

from __future__ import annotations

import httpx
import pytest

from app.adapters.notion import MAX_RATE_LIMIT_RETRIES, NotionAdapter
from app.pipeline import PermanentDeliveryError, TransientDeliveryError
from tests.adapters.conftest import Recorder, body_of, json_response

SCHEMA = {
    "Order": {"type": "title"},
    "Order ID": {"type": "rich_text"},
    "Total": {"type": "number"},
    "Email": {"type": "email"},
    "Phone": {"type": "phone_number"},
    "Site": {"type": "url"},
    "Channel": {"type": "select"},
    "Tags": {"type": "multi_select"},
    "Placed": {"type": "date"},
    "Customer": {"type": "relation"},
    "Paid": {"type": "checkbox"},
    "Thumbnail": {"type": "files"},  # unsupported
}


def notion_handler(*, query_hit: str | None = None, rate_limit_times: int = 0):
    state = {"429s_left": rate_limit_times}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if state["429s_left"] > 0:
            state["429s_left"] -= 1
            return json_response(429, {"code": "rate_limited"}, **{"Retry-After": "2"})
        if request.method == "GET" and path.startswith("/v1/databases/"):
            return json_response(200, {"properties": SCHEMA})
        if path.endswith("/query"):
            results = [{"id": query_hit}] if query_hit else []
            return json_response(200, {"results": results})
        if request.method == "POST" and path == "/v1/pages":
            return json_response(200, {"id": "page-new"})
        if request.method == "PATCH" and path.startswith("/v1/pages/"):
            return json_response(200, {"id": path.rsplit("/", 1)[-1]})
        return json_response(404, {})

    return handler


class Sleeper:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def _adapter(rec: Recorder, sleeper: Sleeper | None = None) -> NotionAdapter:
    return NotionAdapter(
        api_key="secret", database_id="db-1", transport=rec.transport, sleep=sleeper or Sleeper()
    )


RECORD = {
    "_name": "Order #1001",
    "Order ID": "1001",
    "Total": "254.98",
    "Email": "jon@example.com",
    "Phone": "+15551234567",
    "Site": "https://example.com",
    "Channel": "web",
    "Tags": ["vip", "wholesale"],
    "Placed": "2026-09-15",
    "Customer": ["rel-1"],
    "Paid": True,
    "Thumbnail": "x.png",
    "Unknown": "skipped",
    "Nothing": None,
}


async def test_encodes_every_supported_type_and_falls_back_title_to_name():
    rec = Recorder(notion_handler())
    assert await _adapter(rec).upsert_record(RECORD) == "page-new"

    create = rec.requests[-1]
    assert create.headers["Notion-Version"] == "2022-06-28"
    assert create.headers["Authorization"] == "Bearer secret"
    body = body_of(create)
    assert body["parent"] == {"database_id": "db-1"}
    assert body["properties"] == {
        "Order": {"title": [{"type": "text", "text": {"content": "Order #1001"}}]},
        "Order ID": {"rich_text": [{"type": "text", "text": {"content": "1001"}}]},
        "Total": {"number": 254.98},
        "Email": {"email": "jon@example.com"},
        "Phone": {"phone_number": "+15551234567"},
        "Site": {"url": "https://example.com"},
        "Channel": {"select": {"name": "web"}},
        "Tags": {"multi_select": [{"name": "vip"}, {"name": "wholesale"}]},
        "Placed": {"date": {"start": "2026-09-15"}},
        "Customer": {"relation": [{"id": "rel-1"}]},
        "Paid": {"checkbox": True},
    }


async def test_schema_is_fetched_once_and_refreshed_on_unknown_property():
    rec = Recorder(notion_handler())
    adapter = _adapter(rec)
    await adapter.upsert_record({"Order ID": "1"})
    await adapter.upsert_record({"Order ID": "2"})
    schema_fetches = [r for r in rec.requests if r.method == "GET"]
    assert len(schema_fetches) == 1  # cached

    await adapter.upsert_record({"Order ID": "3", "Brand New": "x"})
    schema_fetches = [r for r in rec.requests if r.method == "GET"]
    assert len(schema_fetches) == 2  # refreshed once for the unknown name


async def test_explicit_title_field_wins_over_name():
    rec = Recorder(notion_handler())
    await _adapter(rec).upsert_record({"_name": "ignored", "Order": "explicit"})
    assert rec.last_body()["properties"]["Order"]["title"][0]["text"]["content"] == "explicit"


async def test_nothing_matching_schema_is_permanent():
    rec = Recorder(notion_handler())
    with pytest.raises(PermanentDeliveryError, match="no record fields matched"):
        await _adapter(rec).upsert_record({"Unknown": "x"})


async def test_non_numeric_number_is_permanent_via_value_error():
    rec = Recorder(notion_handler())
    with pytest.raises(ValueError):
        await _adapter(rec).upsert_record({"Total": "not a number"})


async def test_key_query_hit_patches_page():
    rec = Recorder(notion_handler(query_hit="page-old"))
    assert await _adapter(rec).upsert_record({**RECORD, "_key": "Order ID"}) == "page-old"

    query = next(
        b for r, b in zip(rec.requests, rec.bodies(), strict=True) if r.url.path.endswith("/query")
    )
    assert query == {"filter": {"property": "Order ID", "rich_text": {"equals": "1001"}}, "page_size": 1}
    assert rec.requests[-1].method == "PATCH"
    assert rec.requests[-1].url.path == "/v1/pages/page-old"


async def test_key_query_miss_creates():
    rec = Recorder(notion_handler(query_hit=None))
    assert await _adapter(rec).upsert_record({**RECORD, "_key": "Total"}) == "page-new"
    query = next(
        b for r, b in zip(rec.requests, rec.bodies(), strict=True) if r.url.path.endswith("/query")
    )
    assert query["filter"] == {"property": "Total", "number": {"equals": 254.98}}


async def test_429_is_retried_honoring_retry_after():
    sleeper = Sleeper()
    rec = Recorder(notion_handler(rate_limit_times=2))
    assert await _adapter(rec, sleeper).upsert_record({"Order ID": "1"}) == "page-new"
    assert sleeper.calls == [2.0, 2.0]


async def test_persistent_429_becomes_transient_error():
    sleeper = Sleeper()
    rec = Recorder(notion_handler(rate_limit_times=MAX_RATE_LIMIT_RETRIES + 1))
    with pytest.raises(TransientDeliveryError, match="rate limited"):
        await _adapter(rec, sleeper).upsert_record({"Order ID": "1"})
    assert len(sleeper.calls) == MAX_RATE_LIMIT_RETRIES


async def test_4xx_propagates():
    rec = Recorder(lambda r: json_response(404, {"code": "object_not_found"}))
    with pytest.raises(httpx.HTTPStatusError):
        await _adapter(rec).upsert_record({"Order ID": "1"})


async def test_health_check():
    assert await _adapter(Recorder(notion_handler())).health_check() is True
    assert await _adapter(Recorder(lambda r: json_response(401, {}))).health_check() is False
