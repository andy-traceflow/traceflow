"""Registry: construct-once, fail closed on unknown names / missing creds; Protocol conformance."""

from __future__ import annotations

from typing import Any

import pytest

from app.adapters.base import AdapterConfigError, Destination, display_name, split_record
from app.adapters.hubspot import HubSpotAdapter
from app.adapters.monday import MondayAdapter
from app.adapters.notion import NotionAdapter
from app.adapters.registry import get_adapter, list_providers, register_adapter
from app.adapters.sheets import SheetsAdapter
from app.adapters.slack import SlackAdapter


def test_list_providers():
    assert list_providers() == ["hubspot", "monday", "notion", "sheets", "slack"]


def test_unknown_destination_is_a_value_error():
    with pytest.raises(ValueError, match="Unknown destination: 'salesforce'"):
        get_adapter("salesforce")


@pytest.mark.parametrize("name", ["hubspot", "monday", "notion", "sheets", "slack"])
def test_missing_credentials_fail_construction(name, monkeypatch):
    for var in (
        "HUBSPOT_ACCESS_TOKEN", "MONDAY_API_KEY", "MONDAY_BOARD_ID", "NOTION_API_KEY",
        "NOTION_DATABASE_ID", "GOOGLE_SERVICE_ACCOUNT_JSON", "GOOGLE_SHEET_ID",
        "SLACK_BOT_TOKEN", "SLACK_CHANNEL",
    ):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(AdapterConfigError):
        get_adapter(name)


def test_get_adapter_constructs_once(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_CHANNEL", "#orders")
    assert get_adapter("slack") is get_adapter("slack")


def test_register_adapter_replaces_factory_and_instance(monkeypatch):
    class Fake:
        name = "fake"

        async def upsert_record(self, record: dict[str, Any]) -> str:
            return "x"

        async def health_check(self) -> bool:
            return True

    register_adapter("slack", Fake)
    assert isinstance(get_adapter("slack"), Fake)


@pytest.mark.parametrize(
    "adapter",
    [
        HubSpotAdapter(access_token="t"),
        MondayAdapter(api_key="k", board_id="1"),
        NotionAdapter(api_key="k", database_id="d"),
        SlackAdapter(bot_token="t", channel="c"),
        SheetsAdapter(
            service_account_json='{"client_email": "a@b", "private_key": "k"}', spreadsheet_id="s"
        ),
    ],
    ids=lambda a: a.name,
)
def test_every_adapter_conforms_to_destination(adapter):
    assert isinstance(adapter, Destination)


def test_split_record_and_display_name():
    record = {"_name": "Order #1001", "_key": "Order ID", "Order ID": "1001", "_line_items": []}
    fields, meta = split_record(record)
    assert fields == {"Order ID": "1001"}
    assert meta == {"_name": "Order #1001", "_key": "Order ID", "_line_items": []}
    assert display_name(record) == "Order #1001"
    assert display_name({"Total": 5, "Email": "a@b.c"}) == "a@b.c"
    assert display_name({"Total": 5}) == "Untitled"
