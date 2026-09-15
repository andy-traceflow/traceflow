"""Dead-letter alert: payload shape, POST to ALERT_WEBHOOK_URL, log-only when unset."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest

from app.config import get_settings
from app.models.event import Event, EventStatus
from app.services.notifications import build_alert, send_alert

EVENT = Event(
    id=UUID("11111111-2222-3333-4444-555555555555"),
    source="shopify",
    topic="orders/create",
    webhook_id="wh-42",
    shop_domain="example.myshopify.com",
    payload={},
    status=EventStatus.DEAD,
    attempts=5,
    received_at=datetime(2026, 9, 15, 12, 0, tzinfo=UTC),
)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BASE_URL", "https://sia.onrender.com/")
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_build_alert_shape():
    payload = build_alert(EVENT, "HTTP 422 from https://api.notion.com/v1/pages: bad", base_url="https://sia.onrender.com/")
    assert payload["text"].startswith(":rotating_light:")
    assert "orders/create" in payload["text"] and "wh-42" in payload["text"]
    blocks = payload["blocks"]
    assert blocks[0]["type"] == "header"
    fields = " ".join(f["text"] for f in blocks[1]["fields"])
    assert "11111111-2222-3333-4444-555555555555" in fields
    assert "wh-42" in fields and "*Attempts*\n5" in fields and "example.myshopify.com" in fields
    assert "HTTP 422" in blocks[2]["text"]["text"]
    context = blocks[3]["elements"][0]["text"]
    assert "GET https://sia.onrender.com/events?status=dead" in context
    assert "POST https://sia.onrender.com/events/11111111-2222-3333-4444-555555555555/replay" in context


def test_build_alert_truncates_long_errors():
    payload = build_alert(EVENT, "x" * 5000, base_url="https://b")
    assert len(payload["blocks"][2]["text"]["text"]) < 1600


async def test_send_alert_posts_to_webhook(monkeypatch):
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/x")
    get_settings.cache_clear()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="ok")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await send_alert(EVENT, "boom", client=client)

    assert len(seen) == 1
    assert str(seen[0].url) == "https://hooks.slack.com/services/T/B/x"
    body = json.loads(seen[0].content)
    assert "boom" in body["blocks"][2]["text"]["text"]


async def test_send_alert_raises_on_webhook_failure(monkeypatch):
    """The worker swallows this; the function itself must surface it."""
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/x")
    get_settings.cache_clear()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500, text="no"))
    ) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await send_alert(EVENT, "boom", client=client)


async def test_send_alert_without_url_logs_and_returns(caplog):
    caplog.set_level(logging.WARNING)
    await send_alert(EVENT, "boom")  # no client, no URL → no HTTP at all
    messages = [r.getMessage() for r in caplog.records]
    assert any("event dead-lettered" in m for m in messages)
    assert any("ALERT_WEBHOOK_URL not set" in m for m in messages)
    dead_line = next(r for r in caplog.records if r.getMessage() == "event dead-lettered")
    assert dead_line.event_id == str(EVENT.id)  # type: ignore[attr-defined]
    assert dead_line.status == "dead"  # type: ignore[attr-defined]
