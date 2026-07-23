"""Tests for the CRM revenue readback job (jobs/revenue_sync.py).

The pure update-decision is unit-tested directly; `_sync_client` is exercised
with the tenant DB context and adapter mocked, so the suite runs offline.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.jobs.revenue_sync import _sync_client, needs_update
from app.models.client_config import ClientConfig

# ---------------------------------------------------------------------------
# needs_update — pure
# ---------------------------------------------------------------------------

def test_needs_update():
    assert needs_update(None, Decimal("4500")) is True          # first confirmed value
    assert needs_update(Decimal("4500"), Decimal("5000")) is True   # deal grew within window
    assert needs_update(Decimal("4500"), Decimal("4500")) is False  # unchanged
    assert needs_update(None, Decimal("0")) is False            # non-positive never overwrites
    assert needs_update(Decimal("4500"), Decimal("-1")) is False


# ---------------------------------------------------------------------------
# _sync_client
# ---------------------------------------------------------------------------

class _FakeConn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        return self._rows

    async def execute(self, query: str, *args: Any) -> None:
        self.executed.append((query, args))


def _ctx_factory(conn: _FakeConn):
    @contextlib.asynccontextmanager
    async def _ctx(_client_id):
        yield conn

    return _ctx


def _config(client_id, *, mode: str | None, provider: str | None = "hubspot") -> ClientConfig:
    revenue_config = {"mode": mode} if mode is not None else {}
    return ClientConfig(
        client_id=client_id,
        crm_provider=provider,
        revenue_config=revenue_config,
        ai_period_resets_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_sync_client_skips_non_crm_mode():
    client_id = uuid4()
    with patch("app.jobs.revenue_sync.get_adapter") as get_adapter, \
         patch("app.jobs.revenue_sync.set_tenant_context") as ctx:
        updated = await _sync_client(
            client_id, _config(client_id, mode="estimated"), now=datetime.now(UTC)
        )
    assert updated == 0
    get_adapter.assert_not_called()
    ctx.assert_not_called()


@pytest.mark.asyncio
async def test_sync_client_freezes_confirmed_value():
    client_id = uuid4()
    lead_id = uuid4()
    conn = _FakeConn([{"id": lead_id, "crm_external_id": "c1", "recovered_value": None}])
    adapter = MagicMock()
    adapter.fetch_recovered_value = AsyncMock(return_value=Decimal("4500"))

    with patch("app.jobs.revenue_sync.set_tenant_context", _ctx_factory(conn)), \
         patch("app.jobs.revenue_sync.get_adapter", return_value=adapter):
        updated = await _sync_client(
            client_id, _config(client_id, mode="crm"), now=datetime.now(UTC)
        )

    assert updated == 1
    adapter.fetch_recovered_value.assert_awaited_once()
    sql = " ".join(q for q, _ in conn.executed)
    assert "recovered_value = $1, outcome = 'won'" in sql
    assert "sync_log" in sql  # the run is recorded


@pytest.mark.asyncio
async def test_sync_client_no_update_when_value_unchanged():
    client_id = uuid4()
    lead_id = uuid4()
    conn = _FakeConn([{"id": lead_id, "crm_external_id": "c1", "recovered_value": Decimal("4500")}])
    adapter = MagicMock()
    adapter.fetch_recovered_value = AsyncMock(return_value=Decimal("4500"))  # same as stored

    with patch("app.jobs.revenue_sync.set_tenant_context", _ctx_factory(conn)), \
         patch("app.jobs.revenue_sync.get_adapter", return_value=adapter):
        updated = await _sync_client(
            client_id, _config(client_id, mode="crm"), now=datetime.now(UTC)
        )

    assert updated == 0
    sql = " ".join(q for q, _ in conn.executed)
    assert "recovered_value = $1, outcome = 'won'" not in sql
    assert "sync_log" in sql  # still records the (zero-update) run
