"""Nightly recovery-digest tests.

The DB layer (get_service_connection, set_tenant_context) and the email
layer (send_email) are mocked, so the whole suite runs offline. Coverage
splits three ways:

* pure metrics/rendering — the counters, the recovery-rate math, HTML escaping
* the 06:00-local timezone gate and recipient resolution
* orchestration — idempotency, empty-day skip, graceful no-key degradation,
  per-client failure isolation, and that enumeration uses the service
  connection while per-client work is RLS-scoped via set_tenant_context

The 06:00 gate needs a real IANA tz database; `tzdata` is a declared
dependency so ZoneInfo("America/Los_Angeles") resolves on every platform,
Windows included.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.jobs import daily_digest
from app.jobs.daily_digest import (
    LeadLine,
    compute_metrics,
    render_html,
    render_subject,
    run_digests,
    should_send,
)
from app.models.client_config import ClientConfig

LA = "America/Los_Angeles"
# 06:00 America/Los_Angeles == 13:00 UTC during PDT (summer).
DUE_LA = datetime(2026, 6, 5, 13, 0, tzinfo=UTC)
OFF_LA = datetime(2026, 6, 5, 14, 0, tzinfo=UTC)  # 07:00 PDT — off-hour


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _lead(
    *,
    classification: str = "potential_lead",
    status: str = "unqualified",
    name: str | None = "Jane Doe",
    phone: str | None = "+15551234567",
    service_type: str | None = "countertop",
    budget_range: str | None = None,
) -> dict[str, Any]:
    return {
        "classification": classification,
        "qualification_status": status,
        "contact_name": name,
        "phone": phone,
        "service_type": service_type,
        "budget_range": budget_range,
    }


def _config_row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "client_id": uuid4(),
        "ai_period_resets_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return base


def _client_row(timezone: str = LA, **overrides: Any) -> dict[str, Any]:
    base = {"id": uuid4(), "business_name": "Acme Surfaces", "timezone": timezone}
    base.update(overrides)
    return base


def _fake_service_conn(conn: Any):
    @asynccontextmanager
    async def _ctx():
        yield conn

    return _ctx


def _fake_tenant_ctx(conn: Any, seen: list[Any] | None = None):
    @asynccontextmanager
    async def _ctx(client_id: Any):
        if seen is not None:
            seen.append(client_id)
        yield conn

    return _ctx


def _tenant_conn(
    *, already_sent: Any = None, config_row: dict[str, Any] | None = None, leads: list | None = None
) -> AsyncMock:
    """A mock tenant connection wired for the read + record phases."""
    conn = AsyncMock()
    conn.fetchval.return_value = already_sent
    conn.fetchrow.return_value = config_row if config_row is not None else _config_row()
    conn.fetch.return_value = leads if leads is not None else []
    return conn


# ===========================================================================
# compute_metrics — partitioning + counters
# ===========================================================================


def test_compute_partitions_every_classification():
    rows = [
        _lead(status="qualified", budget_range="15k-50k"),  # genuine, replied, qualified
        _lead(status="qualifying"),  # genuine, replied, pending
        _lead(status="unqualified"),  # genuine, silent, pending
        _lead(classification="existing_customer"),
        _lead(classification="known_non_lead"),
        _lead(classification="spam"),
    ]
    m = compute_metrics(rows)
    assert m.captured == 3
    assert m.replied == 2
    assert m.qualified == 1
    assert m.pending == 2
    assert m.pipeline_dollars == 32_500
    assert m.existing_customer_touches == 1
    assert m.known_non_lead_contacts == 1
    assert m.spam_blocked == 1
    assert m.handled_total == 3
    assert m.total_activity == 6
    assert len(m.leads) == 3  # only genuine leads are listed


def test_recovery_rate_none_without_genuine_leads():
    m = compute_metrics([_lead(classification="spam")])
    assert m.captured == 0
    assert m.recovery_rate is None
    assert m.handled_total == 1


def test_recovery_rate_rounds():
    rows = [_lead(status="qualifying"), _lead(status="unqualified"), _lead(status="unqualified")]
    assert compute_metrics(rows).recovery_rate == 33  # 1/3


def test_duplicate_is_not_recovered():
    m = compute_metrics([_lead(status="duplicate")])
    assert m.captured == 1
    assert m.replied == 0
    assert m.recovery_rate == 0


def test_reclassified_genuine_lead_counts_as_replied():
    """A potential_lead the intent gate moved to support_touch / non_lead /
    spam still texted back — it counts as recovered but never as qualified."""
    rows = [
        _lead(status="support_touch"),
        _lead(status="non_lead_contact"),
        _lead(status="spam"),
    ]
    m = compute_metrics(rows)
    assert m.captured == 3
    assert m.replied == 3
    assert m.qualified == 0
    assert m.pending == 0


def test_pipeline_sums_known_budgets_only():
    rows = [
        _lead(budget_range="<5k"),
        _lead(budget_range="50k+"),
        _lead(budget_range=None),
        _lead(budget_range="garbage"),
    ]
    assert compute_metrics(rows).pipeline_dollars == 77_500


def test_lead_line_fields_and_null_fallbacks():
    rows = [
        _lead(name="Ann", phone="+1999", service_type="tile", budget_range="5k-15k", status="qualifying"),
        _lead(name=None, phone=None, service_type=None, budget_range=None),
        _lead(classification="spam"),  # excluded from the list
    ]
    leads = compute_metrics(rows).leads
    assert leads[0] == LeadLine("Ann", "+1999", "tile", "5k-15k", "qualifying")
    assert leads[1] == LeadLine("Unknown caller", "", "", "", "unqualified")
    assert len(leads) == 2


# ===========================================================================
# should_send
# ===========================================================================


def test_should_send_true_with_a_genuine_lead():
    assert should_send(compute_metrics([_lead()])) is True


def test_should_send_true_with_only_filtered_noise():
    assert should_send(compute_metrics([_lead(classification="spam")])) is True


def test_should_send_false_on_a_dead_day():
    assert should_send(compute_metrics([])) is False


# ===========================================================================
# rendering
# ===========================================================================


def test_subject_with_leads():
    m = compute_metrics([_lead(status="qualifying"), _lead(status="unqualified")])
    assert render_subject("Acme Surfaces", m) == "Acme Surfaces: 2 new leads, 50% recovered"


def test_subject_singular_lead():
    m = compute_metrics([_lead(status="qualifying")])
    assert render_subject("Acme", m) == "Acme: 1 new lead, 100% recovered"


def test_subject_noise_only():
    m = compute_metrics([_lead(classification="spam")])
    assert render_subject("Acme Surfaces", m) == "Acme Surfaces: 1 non-lead call filtered"


def test_html_shows_rate_and_escapes_user_content():
    m = compute_metrics(
        [_lead(name="<b>Bobby</b>", service_type="tile & stone", status="qualified", budget_range="15k-50k")]
    )
    out = render_html("Acme", m, "Jun 5, 2026")
    assert "100%" in out
    assert "Jun 5, 2026" in out
    assert "&lt;b&gt;Bobby&lt;/b&gt;" in out  # name escaped
    assert "<b>Bobby</b>" not in out  # raw HTML never injected
    assert "tile &amp; stone" in out
    assert "~$32,500" in out  # pipeline formatted


def test_html_noise_only_has_no_leads_table():
    m = compute_metrics([_lead(classification="spam"), _lead(classification="existing_customer")])
    out = render_html("Acme", m, "Jun 5, 2026")
    assert "Your leads" not in out
    assert "No new sales leads" in out
    assert "Handled automatically" in out


# ===========================================================================
# timezone gate + recipients
# ===========================================================================


def test_is_due_true_at_local_six():
    assert daily_digest._is_due(LA, DUE_LA) is True


def test_is_due_false_off_hour():
    assert daily_digest._is_due(LA, OFF_LA) is False


def test_is_due_respects_timezone():
    """The same instant is 06:00 in one zone and not in another."""
    six_utc = datetime(2026, 6, 5, 6, 0, tzinfo=UTC)
    assert daily_digest._is_due("UTC", six_utc) is True
    assert daily_digest._is_due(LA, six_utc) is False  # 23:00 the night before in LA


def test_local_hour_none_on_bad_timezone():
    assert daily_digest._local_hour("Mars/Phobos", DUE_LA) is None
    assert daily_digest._is_due("Mars/Phobos", DUE_LA) is False


def test_recipients_prefers_owner():
    cfg = _make_config(owner_alert_emails=["owner@x.com"], notification_emails=["ops@x.com"])
    assert daily_digest._recipients(cfg) == ["owner@x.com"]


def test_recipients_falls_back_to_notification():
    cfg = _make_config(notification_emails=["ops@x.com"])
    assert daily_digest._recipients(cfg) == ["ops@x.com"]


def test_recipients_empty_when_nothing_configured():
    assert daily_digest._recipients(_make_config()) == []


def _make_config(**overrides: Any) -> ClientConfig:
    base = {
        "client_id": uuid4(),
        "ai_period_resets_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return ClientConfig(**base)


# ===========================================================================
# _run_for_client — full per-client flow (tenant ctx + send_email mocked)
# ===========================================================================


@pytest.mark.asyncio
async def test_run_for_client_sends_and_records():
    conn = _tenant_conn(
        config_row=_config_row(owner_alert_emails=["owner@x.com"]),
        leads=[_lead(status="qualifying", budget_range="5k-15k")],
    )
    with (
        patch("app.jobs.daily_digest.set_tenant_context", new=_fake_tenant_ctx(conn)),
        patch("app.jobs.daily_digest.send_email", new=AsyncMock(return_value=True)) as mock_send,
    ):
        sent = await daily_digest._run_for_client(uuid4(), "Acme", "UTC", now=DUE_LA)

    assert sent is True
    mock_send.assert_awaited_once()
    assert mock_send.call_args.kwargs["to"] == ["owner@x.com"]
    assert "Acme" in mock_send.call_args.kwargs["subject"]
    conn.execute.assert_awaited_once()  # daily_digest_sent event recorded


@pytest.mark.asyncio
async def test_run_for_client_skips_when_already_sent():
    conn = _tenant_conn(already_sent=1)
    with (
        patch("app.jobs.daily_digest.set_tenant_context", new=_fake_tenant_ctx(conn)),
        patch("app.jobs.daily_digest.send_email", new=AsyncMock()) as mock_send,
    ):
        sent = await daily_digest._run_for_client(uuid4(), "Acme", "UTC", now=DUE_LA)

    assert sent is False
    mock_send.assert_not_awaited()
    conn.fetchrow.assert_not_awaited()  # short-circuits before loading config


@pytest.mark.asyncio
async def test_run_for_client_skips_empty_day():
    conn = _tenant_conn(leads=[])  # nothing happened in the window
    with (
        patch("app.jobs.daily_digest.set_tenant_context", new=_fake_tenant_ctx(conn)),
        patch("app.jobs.daily_digest.send_email", new=AsyncMock()) as mock_send,
    ):
        sent = await daily_digest._run_for_client(uuid4(), "Acme", "UTC", now=DUE_LA)

    assert sent is False
    mock_send.assert_not_awaited()
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_for_client_skips_when_no_recipients():
    conn = _tenant_conn(config_row=_config_row(), leads=[_lead()])  # no emails configured
    with (
        patch("app.jobs.daily_digest.set_tenant_context", new=_fake_tenant_ctx(conn)),
        patch("app.jobs.daily_digest.send_email", new=AsyncMock()) as mock_send,
    ):
        sent = await daily_digest._run_for_client(uuid4(), "Acme", "UTC", now=DUE_LA)

    assert sent is False
    mock_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_for_client_no_config_skips():
    conn = _tenant_conn(config_row=None)
    conn.fetchrow.return_value = None
    with (
        patch("app.jobs.daily_digest.set_tenant_context", new=_fake_tenant_ctx(conn)),
        patch("app.jobs.daily_digest.send_email", new=AsyncMock()) as mock_send,
    ):
        sent = await daily_digest._run_for_client(uuid4(), "Acme", "UTC", now=DUE_LA)

    assert sent is False
    mock_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_for_client_unsent_email_is_not_recorded():
    """RESEND_API_KEY unset (Phase 0) → send_email returns False → no event,
    so tomorrow retries cleanly."""
    conn = _tenant_conn(
        config_row=_config_row(owner_alert_emails=["owner@x.com"]),
        leads=[_lead()],
    )
    with (
        patch("app.jobs.daily_digest.set_tenant_context", new=_fake_tenant_ctx(conn)),
        patch("app.jobs.daily_digest.send_email", new=AsyncMock(return_value=False)) as mock_send,
    ):
        sent = await daily_digest._run_for_client(uuid4(), "Acme", "UTC", now=DUE_LA)

    assert sent is False
    mock_send.assert_awaited_once()
    conn.execute.assert_not_awaited()  # no daily_digest_sent recorded


# ===========================================================================
# run_digests — enumeration, gating, isolation
# ===========================================================================


@pytest.mark.asyncio
async def test_run_digests_runs_due_client():
    service_conn = AsyncMock()
    service_conn.fetch.return_value = [_client_row(timezone=LA)]
    with (
        patch("app.jobs.daily_digest.get_service_connection", new=_fake_service_conn(service_conn)),
        patch("app.jobs.daily_digest._run_for_client", new=AsyncMock(return_value=True)) as mock_run,
    ):
        sent = await run_digests(now=DUE_LA)

    assert sent == 1
    mock_run.assert_awaited_once()
    service_conn.fetch.assert_awaited_once()  # enumeration via service connection


@pytest.mark.asyncio
async def test_run_digests_skips_off_hour_client():
    service_conn = AsyncMock()
    service_conn.fetch.return_value = [_client_row(timezone=LA)]
    with (
        patch("app.jobs.daily_digest.get_service_connection", new=_fake_service_conn(service_conn)),
        patch("app.jobs.daily_digest._run_for_client", new=AsyncMock()) as mock_run,
    ):
        sent = await run_digests(now=OFF_LA)  # 07:00 in LA

    assert sent == 0
    mock_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_digests_force_bypasses_gate():
    service_conn = AsyncMock()
    service_conn.fetch.return_value = [_client_row(timezone=LA)]
    off_peak = datetime(2026, 6, 5, 0, 0, tzinfo=UTC)  # 17:00 in LA
    with (
        patch("app.jobs.daily_digest.get_service_connection", new=_fake_service_conn(service_conn)),
        patch("app.jobs.daily_digest._run_for_client", new=AsyncMock(return_value=True)) as mock_run,
    ):
        sent = await run_digests(now=off_peak, force=True)

    assert sent == 1
    mock_run.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_digests_isolates_per_client_failures():
    service_conn = AsyncMock()
    service_conn.fetch.return_value = [_client_row(timezone=LA), _client_row(timezone=LA)]
    with (
        patch("app.jobs.daily_digest.get_service_connection", new=_fake_service_conn(service_conn)),
        patch(
            "app.jobs.daily_digest._run_for_client",
            new=AsyncMock(side_effect=[Exception("boom"), True]),
        ) as mock_run,
    ):
        sent = await run_digests(now=DUE_LA, force=True)

    assert sent == 1  # first raised, loop continued, second succeeded
    assert mock_run.await_count == 2


@pytest.mark.asyncio
async def test_run_digests_scopes_each_client_via_tenant_context():
    """End-to-end through the real _run_for_client: enumeration uses the
    service connection; per-client work runs inside set_tenant_context."""
    client_row = _client_row(timezone="UTC")
    service_conn = AsyncMock()
    service_conn.fetch.return_value = [client_row]

    tenant_conn = _tenant_conn(
        config_row=_config_row(owner_alert_emails=["owner@x.com"]),
        leads=[_lead(status="qualified")],
    )
    seen: list[Any] = []
    with (
        patch("app.jobs.daily_digest.get_service_connection", new=_fake_service_conn(service_conn)),
        patch("app.jobs.daily_digest.set_tenant_context", new=_fake_tenant_ctx(tenant_conn, seen)),
        patch("app.jobs.daily_digest.send_email", new=AsyncMock(return_value=True)),
    ):
        sent = await run_digests(now=datetime(2026, 6, 5, 6, 0, tzinfo=UTC))

    assert sent == 1
    assert seen and str(seen[0]) == str(client_row["id"])  # RLS-scoped to the client
