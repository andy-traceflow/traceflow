"""build_pipeline(): mapping.yaml + DESTINATION → transform + adapter, fail closed on bad config."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from app.adapters.base import AdapterConfigError
from app.adapters.registry import register_adapter, reset_registry
from app.config import get_settings, require_startup_settings
from app.mapping import MappingError
from app.models.event import Event
from app.pipeline import build_pipeline

FIXTURES = Path(__file__).parent / "fixtures"
ORDER = json.loads((FIXTURES / "shopify_order.json").read_text())

MAPPING = """
topic: orders/create
destination: {destination}
name: "$.name"
fields:
  - from: "$.id"
    to: "Order ID"
    type: text
  - from: "$.total_price"
    to: "Total"
    type: number
"""


class Fake:
    name = "fake"
    seen: list[dict[str, Any]] = []

    async def upsert_record(self, record: dict[str, Any]) -> str:
        self.seen.append(record)
        return "ext-1"

    async def health_check(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://unused")
    monkeypatch.setenv("DESTINATION", "fake")
    mapping_file = tmp_path / "mapping.yaml"
    mapping_file.write_text(MAPPING.format(destination="fake"), encoding="utf-8")
    monkeypatch.setenv("MAPPING_PATH", str(mapping_file))
    register_adapter("fake", Fake)
    Fake.seen = []
    get_settings.cache_clear()
    reset_registry()
    yield mapping_file
    reset_registry()
    get_settings.cache_clear()


def test_destination_is_required_at_startup(monkeypatch):
    monkeypatch.delenv("DESTINATION")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="DESTINATION"):
        require_startup_settings()


def test_unknown_destination_fails(monkeypatch, _env):
    _env.write_text(MAPPING.format(destination="fax"), encoding="utf-8")
    monkeypatch.setenv("DESTINATION", "fax")
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="Unknown destination"):
        build_pipeline()


def test_missing_adapter_credentials_fail(monkeypatch, _env):
    _env.write_text(MAPPING.format(destination="notion"), encoding="utf-8")
    monkeypatch.setenv("DESTINATION", "notion")
    monkeypatch.delenv("NOTION_API_KEY", raising=False)
    get_settings.cache_clear()
    with pytest.raises(AdapterConfigError, match="NOTION_API_KEY"):
        build_pipeline()


def test_missing_mapping_file_fails(monkeypatch):
    monkeypatch.setenv("MAPPING_PATH", "/nowhere/mapping.yaml")
    get_settings.cache_clear()
    with pytest.raises(MappingError, match="not found"):
        build_pipeline()


def test_mapping_destination_must_match_env(_env):
    _env.write_text(MAPPING.format(destination="slack"), encoding="utf-8")
    with pytest.raises(RuntimeError, match="destination: 'slack' but DESTINATION='fake'"):
        build_pipeline()


async def test_pipeline_transforms_then_delivers_through_the_adapter():
    pipeline = build_pipeline()
    event = Event(
        id=uuid4(), source="shopify", topic="orders/create", webhook_id="wh",
        payload=ORDER, received_at=datetime.now(UTC),
    )
    record = pipeline.transform(event)
    assert record == {"_name": "#1001", "Order ID": "820982911946154508", "Total": 254.98}
    assert await pipeline.deliver(record) == "ext-1"
    assert Fake.seen == [record]
