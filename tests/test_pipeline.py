"""build_pipeline(): DESTINATION → registry adapter, fail closed on bad config."""

from __future__ import annotations

from typing import Any

import pytest

from app.adapters.base import AdapterConfigError
from app.adapters.registry import register_adapter, reset_registry
from app.config import get_settings, require_startup_settings
from app.pipeline import build_pipeline


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://unused")
    get_settings.cache_clear()
    reset_registry()
    yield
    reset_registry()
    get_settings.cache_clear()


def test_destination_is_required_at_startup(monkeypatch):
    monkeypatch.delenv("DESTINATION", raising=False)
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="DESTINATION"):
        require_startup_settings()


def test_unknown_destination_fails(monkeypatch):
    monkeypatch.setenv("DESTINATION", "fax")
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="Unknown destination"):
        build_pipeline()


def test_missing_adapter_credentials_fail(monkeypatch):
    monkeypatch.setenv("DESTINATION", "notion")
    monkeypatch.delenv("NOTION_API_KEY", raising=False)
    get_settings.cache_clear()
    with pytest.raises(AdapterConfigError, match="NOTION_API_KEY"):
        build_pipeline()


async def test_pipeline_delivers_through_the_configured_adapter(monkeypatch):
    class Fake:
        name = "fake"
        seen: list[dict[str, Any]] = []

        async def upsert_record(self, record: dict[str, Any]) -> str:
            self.seen.append(record)
            return "ext-1"

        async def health_check(self) -> bool:
            return True

    register_adapter("fake", Fake)
    monkeypatch.setenv("DESTINATION", "fake")
    get_settings.cache_clear()

    pipeline = build_pipeline()
    assert await pipeline.deliver({"Order ID": "1"}) == "ext-1"
    assert Fake.seen == [{"Order ID": "1"}]
