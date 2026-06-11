"""Monthly performance-report tests (jobs/monthly_report.py).

The DB layer (get_service_connection, set_tenant_context) and the email
layer (send_email) are mocked, so the whole suite runs offline. Coverage
splits four ways:

* pure metrics — partitioning, the confirmed-revenue provenance rule
  (ADR-0003: 'estimated' never reaches a confirmed total), rates over empty
  denominators, the ROI multiple, the hours-saved estimate
* the report period (previous local calendar month, January rollover) and
  the day-1-5 / 09:00-local delivery gate
* rendering — subject variants, HTML escaping, estimate-vs-confirmed labels
* orchestration — per-period idempotency, dead-month skip, no-key
  degradation (email fail records nothing, so the next hour retries),
  per-client failure isolation, service-conn enumeration + tenant-scoped work
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.jobs import monthly_report
from app.jobs.monthly_report import (
    ReportMetrics,
    WonJob,
    _is_due,
    compute_report,
    render_html,
    render_subject,
    report_period,
    run_reports,
    should_send,
)
from app.models.client_config import ClientConfig

LA = "America/Los_Angeles"
# 09:00 America/Los_Angeles == 16:00 UTC during PDT (summer); June 3 is day ≤ 5.
DUE = datetime(2026, 6, 3, 16, 0, tzinfo=UTC)
OFF_HOUR = datetime(2026, 6, 3, 17, 0, tzinfo=UTC)  # 10:00 PDT — off-hour
OFF_DAY = datetime(2026, 6, 8, 16, 0, tzinfo=UTC)  # 09:00 PDT but day 8


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _lead(
    *,
    classification: str = "potential_lead",
    status: str = "unqualified",
    name: str | None = "Jane Doe",
    service_type: str | None = "countertop",
    budget_range: str | None = None,
    outcome: str = "open",
    recovered_value: Any = None,
    outcome_source: str | None = None,
) -> dict[str, Any]:
    return {
        "classification": classification,
        "qualification_status": status,
        "contact_name": name,
        "service_type": service_type,
        "budget_range": budget_range,
        "outcome": outcome,
        "recovered_value": recovered_value,
        "outcome_source": outcome_source,
    }


def _won(value: str, source: str = "crm", **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "status": "qualified",
        "outcome": "won",
        "recovered_value": Decimal(value),
        "outcome_source": source,
    }
    base.update(overrides)
    return _lead(**base)


def _metrics(**overrides: Any) -> ReportMetrics:
    base: dict[str, Any] = {
        "captured": 0,
        "replied": 0,
        "qualified": 0,
        "estimated_pipeline": 0,
        "confirmed_recovered": Decimal("0"),
        "confirmed_by_crm": Decimal("0"),
        "confirmed_by_owner": Decimal("0"),
        "program_confirmed": Decimal("0"),
        "existing_customer_touches": 0,
        "known_non_lead_contacts": 0,
        "spam_blocked": 0,
        "won_jobs": (),
    }
    base.update(overrides)
    return ReportMetrics(**base)


def _config_row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "client_id": uuid4(),
        "ai_period_resets_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "owner_alert_emails": ["owner@acme.test"],
    }
    base.update(overrides)
    return base


def _client_row(timezone: str = LA, **overrides: Any) -> dict[str, Any]:
    base = {"id": uuid4(), "business_name": "Acme Surfaces", "timezone": timezone}
    base.update(overrides)
    return base


def _fake_service_conn(rows: list[dict[str, Any]]):
    conn = AsyncMock()
    conn.fetch.return_value = rows

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
    *,
    already_sent: Any = None,
    program: Decimal = Decimal("0"),
    config_row: dict[str, Any] | None = None,
    leads: list | None = None,
) -> AsyncMock:
    """A mock tenant connection wired for the read + record phases.
    fetchval serves _already_sent first, then _fetch_program_confirmed."""
    conn = AsyncMock()
    conn.fetchval.side_effect = [already_sent, program]
    conn.fetchrow.return_value = config_row if config_row is not None else _config_row()
    conn.fetch.return_value = leads if leads is not None else []
    return conn


# ===========================================================================
# compute_report — partitioning + the provenance rule
# ===========================================================================


def test_compute_partitions_and_counters():
    rows = [
        _lead(status="qualified", budget_range="5k-15k"),
        _lead(status="unqualified"),  # greeted, never replied
        _lead(classification="existing_customer"),
        _lead(classification="known_non_lead"),
        _lead(classification="spam"),
    ]
    m = compute_report(rows, program_confirmed=Decimal("0"))
    assert m.captured == 2
    assert m.replied == 1
    assert m.qualified == 1
    assert m.estimated_pipeline == 10_000
    assert m.handled_total == 3
    assert m.total_activity == 5
    assert m.recovery_rate == 50
    assert m.conversion_rate == 50


def test_confirmed_revenue_respects_provenance():
    rows = [
        _won("4500", source="crm"),
        _won("2000", source="owner_report"),
        _won("999", source="estimated"),  # provenance rule: never confirmed
        _lead(status="qualified", outcome="open", recovered_value=Decimal("888")),
        _lead(status="qualified", outcome="lost"),
        _won("777", source="crm", classification="spam"),  # non-genuine: not attributed
    ]
    m = compute_report(rows, program_confirmed=Decimal("0"))
    assert m.confirmed_recovered == Decimal("6500")
    assert m.confirmed_by_crm == Decimal("4500")
    assert m.confirmed_by_owner == Decimal("2000")
    assert len(m.won_jobs) == 2
    # sorted by value, largest first
    assert m.won_jobs[0].value == Decimal("4500")
    assert m.won_jobs[0].source == "crm"
    assert m.won_jobs[1].source == "owner_report"


def test_rates_undefined_on_zero_captured():
    m = compute_report([_lead(classification="spam")], program_confirmed=Decimal("0"))
    assert m.recovery_rate is None
    assert m.conversion_rate is None
    assert should_send(m) is True  # filtered noise still earns a report


def test_dead_month_should_not_send():
    m = compute_report([], program_confirmed=Decimal("0"))
    assert should_send(m) is False


def test_confirmed_revenue_alone_earns_a_send():
    # Zero activity but a prior lead's job confirmed this month — still report.
    m = _metrics(confirmed_recovered=Decimal("4500"))
    assert should_send(m) is True


def test_hours_saved_estimate():
    # 5 conversations × 12 min + 10 filtered × 4 min = 100 min → 2 h (rounded)
    m = _metrics(replied=5, spam_blocked=10)
    assert m.hours_saved == 2
    assert _metrics().hours_saved == 0


def test_roi_multiple():
    m = _metrics(confirmed_recovered=Decimal("4500"))
    assert m.roi_multiple(Decimal("397")) == Decimal("11.3")
    assert m.roi_multiple(None) is None
    assert m.roi_multiple(Decimal("0")) is None
    assert _metrics().roi_multiple(Decimal("397")) is None  # nothing confirmed


# ===========================================================================
# report period + delivery gate
# ===========================================================================


def test_report_period_is_previous_local_month():
    p = report_period(LA, DUE)
    assert p is not None
    assert p.key == "2026-05"
    assert p.label == "May 2026"
    # May 1 00:00 PDT == 07:00 UTC; June 1 00:00 PDT == 07:00 UTC
    assert p.start == datetime(2026, 5, 1, 7, 0, tzinfo=UTC)
    assert p.end == datetime(2026, 6, 1, 7, 0, tzinfo=UTC)


def test_report_period_january_rolls_to_december():
    # Jan 3 2026 09:00 PST == 17:00 UTC
    p = report_period(LA, datetime(2026, 1, 3, 17, 0, tzinfo=UTC))
    assert p is not None
    assert p.key == "2025-12"
    assert p.label == "December 2025"


def test_report_period_unknown_timezone_is_none():
    assert report_period("Not/AZone", DUE) is None


def test_is_due_gate():
    assert _is_due(LA, DUE) is True
    assert _is_due(LA, OFF_HOUR) is False
    assert _is_due(LA, OFF_DAY) is False
    assert _is_due("Not/AZone", DUE) is False


# ===========================================================================
# rendering
# ===========================================================================


def test_subject_leads_with_confirmed_dollars():
    m = _metrics(captured=3, replied=2, confirmed_recovered=Decimal("4500"))
    assert render_subject("Acme", m, "May 2026") == "Acme: $4,500 recovered in May 2026"


def test_subject_falls_back_to_recovery_rate_then_plain():
    m = _metrics(captured=2, replied=1)
    assert render_subject("Acme", m, "May 2026") == (
        "Acme: May 2026 report — 2 new leads, 50% recovered"
    )
    quiet = _metrics(spam_blocked=3)
    assert render_subject("Acme", quiet, "May 2026") == "Acme: May 2026 report"


def test_html_escapes_user_content():
    job = WonJob(
        name="<script>alert(1)</script>",
        service_type="countertop",
        value=Decimal("4500"),
        source="crm",
    )
    m = _metrics(
        captured=1,
        replied=1,
        confirmed_recovered=Decimal("4500"),
        confirmed_by_crm=Decimal("4500"),
        won_jobs=(job,),
    )
    out = render_html("Acme <&> Co", m, "May 2026")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "Acme &lt;&amp;&gt; Co" in out


def test_html_separates_confirmed_from_estimate():
    m = _metrics(
        captured=2,
        replied=2,
        qualified=1,
        estimated_pipeline=10_000,
        confirmed_recovered=Decimal("4500"),
        confirmed_by_crm=Decimal("4500"),
        program_confirmed=Decimal("12000"),
        won_jobs=(
            WonJob(name="Jane", service_type="tile", value=Decimal("4500"), source="crm"),
        ),
    )
    out = render_html("Acme", m, "May 2026", monthly_fee=Decimal("397"))
    assert "$4,500" in out  # confirmed hero
    assert "CRM-confirmed" in out  # provenance label
    assert "estimate" in out  # pipeline explicitly labeled
    assert "never counted as confirmed" in out  # footer provenance note
    assert "Program to date" in out and "$12,000" in out
    assert "11.3&times; your monthly retainer" in out


def test_html_omits_roi_without_fee():
    m = _metrics(
        captured=1,
        replied=1,
        confirmed_recovered=Decimal("4500"),
        confirmed_by_crm=Decimal("4500"),
    )
    out = render_html("Acme", m, "May 2026", monthly_fee=None)
    assert "monthly retainer" not in out


# ===========================================================================
# ClientConfig.monthly_fee
# ===========================================================================


def _config(revenue_config: dict[str, Any]) -> ClientConfig:
    return ClientConfig(
        client_id=uuid4(),
        revenue_config=revenue_config,
        ai_period_resets_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def test_monthly_fee_parsing():
    assert _config({"monthly_fee": 397}).monthly_fee == Decimal("397")
    assert _config({"monthly_fee": "499.50"}).monthly_fee == Decimal("499.50")
    assert _config({}).monthly_fee is None
    assert _config({"monthly_fee": "not-a-number"}).monthly_fee is None
    assert _config({"monthly_fee": 0}).monthly_fee is None
    assert _config({"monthly_fee": -5}).monthly_fee is None


# ===========================================================================
# orchestration
# ===========================================================================


@pytest.mark.asyncio
async def test_run_sends_and_records_period_event():
    client = _client_row()
    config_row = _config_row(revenue_config={"monthly_fee": 397})
    conn = _tenant_conn(
        config_row=config_row,
        leads=[_won("4500", source="crm")],
    )
    seen: list[Any] = []
    send = AsyncMock(return_value=True)

    with patch.object(monthly_report, "get_service_connection", _fake_service_conn([client])), \
         patch.object(monthly_report, "set_tenant_context", _fake_tenant_ctx(conn, seen)), \
         patch.object(monthly_report, "send_email", send):
        sent = await run_reports(now=DUE)

    assert sent == 1
    send.assert_awaited_once()
    assert send.await_args.kwargs["to"] == ["owner@acme.test"]
    assert "$4,500 recovered in May 2026" in send.await_args.kwargs["subject"]
    # read phase + record phase — both tenant-scoped to this client
    assert seen == [client["id"], client["id"]]
    # the sent event carries the idempotency period + the confirmed actuals
    insert_sql, _cid, event_type, payload = conn.execute.await_args.args
    assert "INSERT INTO events" in insert_sql
    assert event_type == "monthly_report_sent"
    assert payload["period"] == "2026-05"
    assert payload["confirmed_recovered"] == "4500"
    assert payload["roi_multiple"] == "11.3"


@pytest.mark.asyncio
async def test_run_skips_when_period_already_sent():
    client = _client_row()
    conn = AsyncMock()
    conn.fetchval.side_effect = [1]  # _already_sent hit; nothing else queried
    send = AsyncMock(return_value=True)

    with patch.object(monthly_report, "get_service_connection", _fake_service_conn([client])), \
         patch.object(monthly_report, "set_tenant_context", _fake_tenant_ctx(conn)), \
         patch.object(monthly_report, "send_email", send):
        sent = await run_reports(now=DUE)

    assert sent == 0
    send.assert_not_awaited()
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_email_failure_records_nothing_so_next_hour_retries():
    client = _client_row()
    conn = _tenant_conn(leads=[_won("4500")])
    send = AsyncMock(return_value=False)  # RESEND_API_KEY unset / vendor blip

    with patch.object(monthly_report, "get_service_connection", _fake_service_conn([client])), \
         patch.object(monthly_report, "set_tenant_context", _fake_tenant_ctx(conn)), \
         patch.object(monthly_report, "send_email", send):
        sent = await run_reports(now=DUE)

    assert sent == 0
    conn.execute.assert_not_awaited()  # no event → tomorrow retries


@pytest.mark.asyncio
async def test_dead_month_skips_send():
    client = _client_row()
    conn = _tenant_conn(leads=[])
    send = AsyncMock(return_value=True)

    with patch.object(monthly_report, "get_service_connection", _fake_service_conn([client])), \
         patch.object(monthly_report, "set_tenant_context", _fake_tenant_ctx(conn)), \
         patch.object(monthly_report, "send_email", send):
        sent = await run_reports(now=DUE)

    assert sent == 0
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_gate_skips_off_hour_unless_forced():
    client = _client_row()
    send = AsyncMock(return_value=True)

    def fresh_conn() -> AsyncMock:
        return _tenant_conn(leads=[_won("4500")])

    with patch.object(monthly_report, "get_service_connection", _fake_service_conn([client])), \
         patch.object(monthly_report, "set_tenant_context", _fake_tenant_ctx(fresh_conn())), \
         patch.object(monthly_report, "send_email", send):
        assert await run_reports(now=OFF_HOUR) == 0
        send.assert_not_awaited()

    with patch.object(monthly_report, "get_service_connection", _fake_service_conn([client])), \
         patch.object(monthly_report, "set_tenant_context", _fake_tenant_ctx(fresh_conn())), \
         patch.object(monthly_report, "send_email", send):
        assert await run_reports(now=OFF_HOUR, force=True) == 1


@pytest.mark.asyncio
async def test_one_client_failure_never_aborts_the_run():
    bad, good = _client_row(), _client_row()
    good_conn = _tenant_conn(leads=[_won("4500")])
    send = AsyncMock(return_value=True)

    @asynccontextmanager
    async def _ctx(client_id: Any):
        if client_id == bad["id"]:
            raise RuntimeError("tenant exploded")
        yield good_conn

    with patch.object(monthly_report, "get_service_connection", _fake_service_conn([bad, good])), \
         patch.object(monthly_report, "set_tenant_context", _ctx), \
         patch.object(monthly_report, "send_email", send):
        sent = await run_reports(now=DUE)

    assert sent == 1
    send.assert_awaited_once()
