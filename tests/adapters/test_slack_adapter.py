"""Slack adapter: Block Kit shape, ok:false error classification, ts as external id."""

from __future__ import annotations

import httpx
import pytest

from app.adapters.slack import SlackAdapter
from app.pipeline import PermanentDeliveryError, TransientDeliveryError
from tests.adapters.conftest import Recorder, json_response


def slack_handler(*, error: str | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if error:
            return json_response(200, {"ok": False, "error": error})
        if request.url.path.endswith("/auth.test"):
            return json_response(200, {"ok": True, "user": "bot"})
        return json_response(200, {"ok": True, "ts": "1726400000.000100"})

    return handler


def _adapter(rec: Recorder) -> SlackAdapter:
    return SlackAdapter(bot_token="xoxb-t", channel="C123", transport=rec.transport)


RECORD = {
    "_name": "Order #1001",
    "_key": "Order ID",
    "Order ID": "1001",
    "Total": 254.98,
    "Empty": "",
    "Missing": None,
    "Tags": ["vip", "wholesale"],
    "_line_items": [
        {"_name": "Widget", "Qty": 2, "Price": "10.00"},
        {"Title": "Gadget", "Qty": 1},
    ],
}


async def test_posts_block_kit_message_and_returns_ts():
    rec = Recorder(slack_handler())
    assert await _adapter(rec).upsert_record(RECORD) == "1726400000.000100"

    req = rec.requests[-1]
    assert req.url.path == "/api/chat.postMessage"
    assert req.headers["Authorization"] == "Bearer xoxb-t"
    body = rec.last_body()
    assert body["channel"] == "C123"
    assert body["text"] == "Order #1001"

    blocks = body["blocks"]
    assert blocks[0] == {"type": "header", "text": {"type": "plain_text", "text": "Order #1001"}}
    assert blocks[1]["type"] == "section"
    assert [f["text"] for f in blocks[1]["fields"]] == [
        "*Order ID*\n1001",
        "*Total*\n254.98",
        "*Tags*\nvip, wholesale",
    ]
    assert blocks[2] == {"type": "divider"}
    assert blocks[3]["text"]["text"] == "• *Widget* — Qty: 2, Price: 10.00\n• *Gadget* — Qty: 1"


def test_fields_are_chunked_to_block_kit_limit():
    record = {f"F{i}": i for i in range(23)}
    blocks = SlackAdapter.build_blocks(record)
    sections = [b for b in blocks if b["type"] == "section"]
    assert [len(s["fields"]) for s in sections] == [10, 10, 3]


async def test_ratelimited_is_transient():
    rec = Recorder(slack_handler(error="ratelimited"))
    with pytest.raises(TransientDeliveryError, match="ratelimited"):
        await _adapter(rec).upsert_record(RECORD)


async def test_channel_not_found_is_permanent():
    rec = Recorder(slack_handler(error="channel_not_found"))
    with pytest.raises(PermanentDeliveryError, match="channel_not_found"):
        await _adapter(rec).upsert_record(RECORD)


async def test_http_5xx_propagates():
    rec = Recorder(lambda r: json_response(503, {}))
    with pytest.raises(httpx.HTTPStatusError):
        await _adapter(rec).upsert_record(RECORD)


async def test_health_check():
    assert await _adapter(Recorder(slack_handler())).health_check() is True
    assert await _adapter(Recorder(slack_handler(error="invalid_auth"))).health_check() is False
