"""Monthly per-tenant performance report.

By the 5th of each month, email each client's owner the previous calendar
month's results (docs/workflow-schema.md, ``monthly_performance_report``):
leads captured, recovery + conversion rates, CONFIRMED recovered revenue
(actuals from ``leads.recovered_value``, labeled by ``outcome_source``), the
estimated pipeline (budget-bucket proxy — shown separately, never blended,
per ADR-0003's provenance rule), an ROI multiple against the monthly
retainer when ``revenue_config.monthly_fee`` is set, and an hours-saved
estimate. This is the number the LLR promise ("recover 25%+ of missed-call
revenue") and the case study stand on.

Run via:
    python -m app.jobs.monthly_report            # honors the day 1-5, 09:00-local gate
    python -m app.jobs.monthly_report --force    # send now for every active client

Scheduling: an hourly Render cron on days 1–5 invokes this; each client is
gated to its own local 09:00 (the first matching hour sends), so one job
serves every timezone and a failed day retries the next — "by the 5th" with
four built-in retries. A ``monthly_report_sent`` event keyed by period
(``payload->>'period' = 'YYYY-MM'``) makes re-runs idempotent.

Spends no AI and no Twilio — pure SQL plus one email — so it runs fully in
Phase 0. A missing Resend key means the email no-ops and the next cron hour
retries; the event is recorded only after a successful send.
"""

from __future__ import annotations

import asyncio
import html
import logging
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.db import close_pool, get_service_connection, init_pool, set_tenant_context

# Shared lead semantics live in daily_digest — one definition of what counts
# as genuine/replied/qualified and one budget-midpoint table, never two.
from app.jobs.daily_digest import (
    BUDGET_MIDPOINTS,
    GENUINE,
    NON_RECOVERED_STATUSES,
    QUALIFIED_STATUSES,
)
from app.models.client_config import ClientConfig
from app.services.notifications import send_email

logger = logging.getLogger(__name__)

# --- tunables --------------------------------------------------------------
REPORT_HOUR = 9  # 09:00 local — lands in the owner's business morning
REPORT_DAY_LIMIT = 5  # workflow-schema: delivery_by the 5th of the month
REPORT_EVENT = "monthly_report_sent"

# Actuals only — 'estimated' is provenance for the budget-bucket proxy and is
# never counted as confirmed revenue (ADR-0003 prime directive).
CONFIRMED_SOURCES = frozenset({"crm", "owner_report"})

# Hours-saved heuristic (always labeled an estimate in the email). Per-unit
# minutes are deliberately conservative: every filtered non-lead call is a
# ring/screen/callback the owner didn't field; every recovered lead is an SMS
# qualification conversation the system ran end-to-end.
MINUTES_PER_FILTERED_CALL = 4
MINUTES_PER_RECOVERED_CONVERSATION = 12

_SOURCE_LABELS: dict[str, str] = {
    "crm": "CRM-confirmed",
    "owner_report": "Owner-reported",
}


# ===========================================================================
# Metrics (pure — all SQL lives in the IO layer below, so this is fully
# unit-testable without a database)
# ===========================================================================


@dataclass(frozen=True)
class WonJob:
    """One confirmed-won lead's row in the report table."""

    name: str
    service_type: str
    value: Decimal
    source: str  # 'crm' | 'owner_report'


@dataclass(frozen=True)
class ReportMetrics:
    captured: int
    replied: int
    qualified: int
    estimated_pipeline: int
    confirmed_recovered: Decimal
    confirmed_by_crm: Decimal
    confirmed_by_owner: Decimal
    program_confirmed: Decimal  # all leads through period end — the case-study number
    existing_customer_touches: int
    known_non_lead_contacts: int
    spam_blocked: int
    won_jobs: tuple[WonJob, ...]

    @property
    def handled_total(self) -> int:
        """Non-lead calls the system absorbed without bothering the owner."""
        return (
            self.existing_customer_touches
            + self.known_non_lead_contacts
            + self.spam_blocked
        )

    @property
    def total_activity(self) -> int:
        return self.captured + self.handled_total

    @property
    def recovery_rate(self) -> int | None:
        """Percent of captured genuine leads that texted back. None when there
        were no genuine leads — a rate over an empty denominator is undefined,
        not zero."""
        if self.captured == 0:
            return None
        return round(self.replied / self.captured * 100)

    @property
    def conversion_rate(self) -> int | None:
        """Percent of captured genuine leads that fully qualified."""
        if self.captured == 0:
            return None
        return round(self.qualified / self.captured * 100)

    @property
    def hours_saved(self) -> int:
        """Estimated owner-hours the system absorbed this month."""
        minutes = (
            self.replied * MINUTES_PER_RECOVERED_CONVERSATION
            + self.handled_total * MINUTES_PER_FILTERED_CALL
        )
        return round(minutes / 60)

    def roi_multiple(self, monthly_fee: Decimal | None) -> Decimal | None:
        """Confirmed recovered ÷ monthly retainer (PRD §13, target ≥10x).
        None when the fee is unset/non-positive or nothing is confirmed yet —
        an ROI over an estimate would violate the provenance rule."""
        if monthly_fee is None or monthly_fee <= 0 or self.confirmed_recovered <= 0:
            return None
        return (self.confirmed_recovered / monthly_fee).quantize(Decimal("0.1"))


def compute_report(
    rows: Iterable[Mapping[str, Any]], *, program_confirmed: Decimal
) -> ReportMetrics:
    """Fold the report month's lead rows into the report counters.

    ``rows`` is whatever the leads query returned (asyncpg Records or, in
    tests, plain dicts). Confirmed revenue counts only genuine leads with
    ``outcome='won'`` and a confirmed ``outcome_source`` — the 'estimated'
    provenance tag never reaches a confirmed total.
    """
    captured = replied = qualified = pipeline = 0
    existing = known_non_lead = spam = 0
    confirmed = by_crm = by_owner = Decimal("0")
    won: list[WonJob] = []

    for row in rows:
        classification = row["classification"]
        if classification == GENUINE:
            captured += 1
            status = row["qualification_status"]
            if status not in NON_RECOVERED_STATUSES:
                replied += 1
            if status in QUALIFIED_STATUSES:
                qualified += 1
            pipeline += BUDGET_MIDPOINTS.get(row["budget_range"] or "", 0)

            source = row["outcome_source"]
            raw_value = row["recovered_value"]
            if row["outcome"] == "won" and source in CONFIRMED_SOURCES and raw_value:
                value = Decimal(str(raw_value))
                if value > 0:
                    confirmed += value
                    if source == "crm":
                        by_crm += value
                    else:
                        by_owner += value
                    won.append(
                        WonJob(
                            name=row["contact_name"] or "Unknown caller",
                            service_type=row["service_type"] or "",
                            value=value,
                            source=source,
                        )
                    )
        elif classification == "existing_customer":
            existing += 1
        elif classification == "known_non_lead":
            known_non_lead += 1
        elif classification == "spam":
            spam += 1

    return ReportMetrics(
        captured=captured,
        replied=replied,
        qualified=qualified,
        estimated_pipeline=pipeline,
        confirmed_recovered=confirmed,
        confirmed_by_crm=by_crm,
        confirmed_by_owner=by_owner,
        program_confirmed=program_confirmed,
        existing_customer_touches=existing,
        known_non_lead_contacts=known_non_lead,
        spam_blocked=spam,
        won_jobs=tuple(sorted(won, key=lambda j: j.value, reverse=True)),
    )


def should_send(metrics: ReportMetrics) -> bool:
    """Skip truly dead months (zero calls of any kind and nothing confirmed)
    so the report stays an accountability artifact, not noise."""
    return metrics.total_activity > 0 or metrics.confirmed_recovered > 0


# ===========================================================================
# Report period (pure)
# ===========================================================================


@dataclass(frozen=True)
class ReportPeriod:
    """The previous calendar month, in the client's local timezone."""

    start: datetime  # UTC instant of local month start (inclusive)
    end: datetime  # UTC instant of local next-month start (exclusive)
    key: str  # 'YYYY-MM' — the idempotency key
    label: str  # 'May 2026' — the human label


def report_period(timezone: str, now: datetime) -> ReportPeriod | None:
    """The month being reported on, anchored to the client's local calendar.
    None on an unrecognized timezone (logged) — better a missing report we can
    see in logs than one cut on the wrong month boundary."""
    try:
        tz = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning(
            "monthly_report: unrecognized timezone — skipping", extra={"timezone": timezone}
        )
        return None
    local = now.astimezone(tz)
    this_month_start = datetime(local.year, local.month, 1, tzinfo=tz)
    if local.month == 1:
        prev_year, prev_month = local.year - 1, 12
    else:
        prev_year, prev_month = local.year, local.month - 1
    prev_month_start = datetime(prev_year, prev_month, 1, tzinfo=tz)
    return ReportPeriod(
        start=prev_month_start.astimezone(UTC),
        end=this_month_start.astimezone(UTC),
        key=f"{prev_year:04d}-{prev_month:02d}",
        label=f"{prev_month_start:%B} {prev_year}",
    )


def _is_due(timezone: str, now: datetime) -> bool:
    """True only during the client's local 09:00 hour on days 1–5."""
    try:
        local = now.astimezone(ZoneInfo(timezone))
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning(
            "monthly_report: unrecognized timezone — skipping", extra={"timezone": timezone}
        )
        return False
    return local.day <= REPORT_DAY_LIMIT and local.hour == REPORT_HOUR


# ===========================================================================
# Rendering (pure)
# ===========================================================================


def _money(value: Decimal | int) -> str:
    return f"${value:,.0f}"


def render_subject(business_name: str, metrics: ReportMetrics, period_label: str) -> str:
    if metrics.confirmed_recovered > 0:
        return (
            f"{business_name}: {_money(metrics.confirmed_recovered)} recovered "
            f"in {period_label}"
        )
    if metrics.captured > 0:
        leads_word = "lead" if metrics.captured == 1 else "leads"
        return (
            f"{business_name}: {period_label} report — {metrics.captured} new "
            f"{leads_word}, {metrics.recovery_rate}% recovered"
        )
    return f"{business_name}: {period_label} report"


def render_html(
    business_name: str,
    metrics: ReportMetrics,
    period_label: str,
    *,
    monthly_fee: Decimal | None = None,
) -> str:
    return f"""\
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;\
max-width:600px;margin:0 auto;color:#1f2933;">
  <p style="font-size:13px;color:#7b8794;margin:0 0 4px;">\
{html.escape(business_name)} &middot; {html.escape(period_label)}</p>
  <h1 style="font-size:20px;margin:0 0 16px;">Monthly performance report</h1>
  {_render_hero(metrics, period_label, monthly_fee)}
  {_render_stats(metrics)}
  {_render_pipeline(metrics, period_label)}
  {_render_won_table(metrics)}
  {_render_program(metrics)}
  {_render_hours(metrics)}
  {_render_handled(metrics)}
  <p style="font-size:12px;color:#9aa5b1;margin-top:28px;border-top:1px solid #e4e7eb;\
padding-top:12px;">Sent automatically by TraceFlow. Confirmed revenue comes from your CRM \
or your own booked-job reports &mdash; the source is recorded on every dollar. Estimated \
pipeline is a budget-range midpoint and is never counted as confirmed.</p>
</div>"""


def _count_noun(n: int, singular: str, plural: str | None = None) -> str:
    word = singular if n == 1 else (plural or f"{singular}s")
    return f"{n} {word}"


def _render_hero(
    metrics: ReportMetrics, period_label: str, monthly_fee: Decimal | None
) -> str:
    if metrics.confirmed_recovered > 0:
        # System-generated money strings only — nothing user-supplied to escape.
        provenance_bits = []
        if metrics.confirmed_by_crm > 0:
            provenance_bits.append(f"{_money(metrics.confirmed_by_crm)} CRM-confirmed")
        if metrics.confirmed_by_owner > 0:
            provenance_bits.append(f"{_money(metrics.confirmed_by_owner)} owner-reported")
        provenance = " &middot; ".join(provenance_bits)
        roi = metrics.roi_multiple(monthly_fee)
        roi_line = (
            f'<p style="margin:8px 0 0;font-size:15px;color:#067647;font-weight:600;">'
            f"That&rsquo;s {roi}&times; your monthly retainer.</p>"
            if roi is not None
            else ""
        )
        return (
            '<div style="background:#ecfdf3;border:1px solid #c6f0d6;border-radius:10px;'
            'padding:20px;margin-bottom:16px;">'
            f'<div style="font-size:40px;font-weight:700;color:#067647;line-height:1;">'
            f"{_money(metrics.confirmed_recovered)}</div>"
            f'<p style="margin:6px 0 0;font-size:15px;color:#1f2933;">confirmed recovered '
            f"revenue from {html.escape(period_label)}&rsquo;s missed calls."
            f'<br><span style="font-size:12px;color:#52606d;">{provenance}</span></p>'
            f"{roi_line}</div>"
        )
    if metrics.captured > 0:
        return (
            '<div style="background:#ecfdf3;border:1px solid #c6f0d6;border-radius:10px;'
            'padding:20px;margin-bottom:16px;">'
            f'<div style="font-size:40px;font-weight:700;color:#067647;line-height:1;">'
            f"{metrics.recovery_rate}%</div>"
            f'<p style="margin:6px 0 0;font-size:15px;color:#1f2933;">recovered &mdash; '
            f"{metrics.replied} of {metrics.captured} missed-call leads texted back. "
            "No booked jobs confirmed yet for this month&rsquo;s leads.</p></div>"
        )
    return (
        '<div style="background:#f5f7fa;border-radius:10px;padding:20px;margin-bottom:16px;">'
        f'<p style="margin:0;font-size:15px;color:#52606d;">No new sales leads in '
        f"{html.escape(period_label)} &mdash; we filtered "
        f"{_count_noun(metrics.handled_total, 'non-lead call')} for you.</p></div>"
    )


def _render_stats(metrics: ReportMetrics) -> str:
    conversion = f"{metrics.conversion_rate}%" if metrics.conversion_rate is not None else "&mdash;"
    cells = (
        ("Captured", str(metrics.captured)),
        ("Replied", str(metrics.replied)),
        ("Qualified", str(metrics.qualified)),
        ("Conversion", conversion),
    )
    tds = "".join(
        '<td style="text-align:center;padding:8px;">'
        f'<div style="font-size:24px;font-weight:700;color:#1f2933;">{value}</div>'
        '<div style="font-size:12px;color:#7b8794;text-transform:uppercase;'
        f'letter-spacing:.04em;">{label}</div></td>'
        for label, value in cells
    )
    return (
        '<table role="presentation" width="100%" '
        'style="border-collapse:collapse;margin-bottom:16px;">'
        f"<tr>{tds}</tr></table>"
    )


def _render_pipeline(metrics: ReportMetrics, period_label: str) -> str:
    if metrics.estimated_pipeline <= 0:
        return ""
    return (
        '<p style="font-size:15px;margin:0 0 16px;color:#1f2933;">'
        f"<strong>~{_money(metrics.estimated_pipeline)}</strong> in estimated pipeline "
        f"entered in {html.escape(period_label)} "
        '<span style="color:#7b8794;font-size:13px;">(budget-range midpoints &mdash; an '
        "estimate, reported separately from confirmed revenue)</span>.</p>"
    )


def _render_won_table(metrics: ReportMetrics) -> str:
    if not metrics.won_jobs:
        return ""
    header = (
        '<tr style="text-align:left;font-size:12px;color:#7b8794;'
        'text-transform:uppercase;letter-spacing:.04em;">'
        '<th style="padding:6px 8px;">Booked job</th><th style="padding:6px 8px;">Service</th>'
        '<th style="padding:6px 8px;">Value</th><th style="padding:6px 8px;">Confirmed via</th></tr>'
    )
    body = "".join(_render_won_row(job) for job in metrics.won_jobs)
    return (
        '<h2 style="font-size:15px;margin:0 0 8px;">Booked jobs from recovered leads</h2>'
        '<table role="presentation" width="100%" '
        'style="border-collapse:collapse;margin-bottom:20px;font-size:14px;">'
        f"{header}{body}</table>"
    )


def _render_won_row(job: WonJob) -> str:
    name = html.escape(job.name)
    service = html.escape(job.service_type) or "&mdash;"
    source = html.escape(_SOURCE_LABELS.get(job.source, job.source))
    return (
        '<tr style="border-top:1px solid #e4e7eb;">'
        f'<td style="padding:8px;">{name}</td>'
        f'<td style="padding:8px;">{service}</td>'
        f'<td style="padding:8px;font-weight:600;">{_money(job.value)}</td>'
        f'<td style="padding:8px;color:#52606d;">{source}</td></tr>'
    )


def _render_program(metrics: ReportMetrics) -> str:
    if metrics.program_confirmed <= 0:
        return ""
    return (
        '<p style="font-size:15px;margin:0 0 16px;color:#1f2933;">'
        f"Program to date: <strong>{_money(metrics.program_confirmed)}</strong> "
        "confirmed recovered revenue.</p>"
    )


def _render_hours(metrics: ReportMetrics) -> str:
    if metrics.hours_saved <= 0:
        return ""
    return (
        '<p style="font-size:15px;margin:0 0 16px;color:#1f2933;">'
        f"&#8776;<strong>{_count_noun(metrics.hours_saved, 'hour')}</strong> of call "
        "screening and lead follow-up handled automatically this month "
        '<span style="color:#7b8794;font-size:13px;">(estimate)</span>.</p>'
    )


def _render_handled(metrics: ReportMetrics) -> str:
    if metrics.handled_total == 0:
        return ""
    parts: list[str] = []
    if metrics.existing_customer_touches:
        parts.append(_count_noun(metrics.existing_customer_touches, "existing-customer call"))
    if metrics.known_non_lead_contacts:
        parts.append(_count_noun(metrics.known_non_lead_contacts, "vendor / non-lead"))
    if metrics.spam_blocked:
        parts.append(_count_noun(metrics.spam_blocked, "spam call"))
    summary = html.escape(", ".join(parts))
    return (
        '<div style="background:#f5f7fa;border-radius:10px;padding:14px 16px;margin-bottom:8px;">'
        '<p style="margin:0;font-size:13px;color:#52606d;">'
        f"&#128737;&#65039; Handled automatically so you didn't have to: {summary}.</p></div>"
    )


# ===========================================================================
# IO + orchestration
# ===========================================================================


def _recipients(config: ClientConfig) -> list[str]:
    """Owner first; fall back to the ops notification list — same resolution
    as the daily digest."""
    return list(config.owner_alert_emails or config.notification_emails)


async def _already_sent(conn: Any, client_id: UUID, period_key: str) -> bool:
    existing = await conn.fetchval(
        """
        SELECT 1 FROM events
        WHERE client_id = $1 AND event_type = $2 AND payload->>'period' = $3
        LIMIT 1
        """,
        client_id,
        REPORT_EVENT,
        period_key,
    )
    return existing is not None


async def _fetch_month_leads(
    conn: Any, client_id: UUID, period: ReportPeriod
) -> Sequence[Mapping[str, Any]]:
    return await conn.fetch(
        """
        SELECT classification, qualification_status, budget_range, contact_name,
               service_type, outcome, recovered_value, outcome_source
        FROM leads
        WHERE client_id = $1 AND created_at >= $2 AND created_at < $3
          AND is_test = FALSE
        ORDER BY created_at
        """,
        client_id,
        period.start,
        period.end,
    )


async def _fetch_program_confirmed(conn: Any, client_id: UUID, before: datetime) -> Decimal:
    """All-time confirmed recovered revenue for leads captured before ``before``
    (the report period's end). Anchoring on creation date keeps each report's
    program number stable while still picking up late confirmations — a May
    lead whose job books in June shows up here in June's report. Only genuine
    leads are attributed (ADR-0003)."""
    value = await conn.fetchval(
        """
        SELECT COALESCE(SUM(recovered_value), 0)
        FROM leads
        WHERE client_id = $1
          AND classification = 'potential_lead'
          AND outcome = 'won'
          AND outcome_source IN ('crm', 'owner_report')
          AND recovered_value IS NOT NULL
          AND is_test = FALSE
          AND created_at < $2
        """,
        client_id,
        before,
    )
    return Decimal(str(value or 0))


async def _record_sent(
    conn: Any, client_id: UUID, period: ReportPeriod, metrics: ReportMetrics, roi: Decimal | None
) -> None:
    # Decimals are stringified: the JSONB codec is plain json.dumps, which
    # cannot serialize Decimal.
    await conn.execute(
        """
        INSERT INTO events (client_id, event_type, payload)
        VALUES ($1, $2, $3)
        """,
        client_id,
        REPORT_EVENT,
        {
            "period": period.key,
            "captured": metrics.captured,
            "replied": metrics.replied,
            "qualified": metrics.qualified,
            "recovery_rate": metrics.recovery_rate,
            "conversion_rate": metrics.conversion_rate,
            "estimated_pipeline": metrics.estimated_pipeline,
            "confirmed_recovered": str(metrics.confirmed_recovered),
            "confirmed_by_crm": str(metrics.confirmed_by_crm),
            "confirmed_by_owner": str(metrics.confirmed_by_owner),
            "program_confirmed": str(metrics.program_confirmed),
            "roi_multiple": str(roi) if roi is not None else None,
            "hours_saved": metrics.hours_saved,
        },
    )


async def _run_for_client(
    client_id: UUID, business_name: str, timezone: str, *, now: datetime
) -> bool:
    """Build and send one client's monthly report. Returns True iff an email
    went out.

    Every query is RLS-scoped to this tenant via ``set_tenant_context``. The
    read phase and the record phase are separate tenant contexts so no DB
    transaction is ever held open across the email send (network IO).
    """
    period = report_period(timezone, now)
    if period is None:
        return False

    # --- read phase: gather everything, then release the connection ---------
    async with set_tenant_context(client_id) as conn:
        if await _already_sent(conn, client_id, period.key):
            logger.info(
                "monthly_report: already sent for period",
                extra={"client_id": str(client_id), "period": period.key},
            )
            return False

        config_row = await conn.fetchrow(
            "SELECT * FROM client_configs WHERE client_id = $1", client_id
        )
        if config_row is None:
            logger.warning(
                "monthly_report: no client_config", extra={"client_id": str(client_id)}
            )
            return False
        config = ClientConfig(**dict(config_row))

        program_confirmed = await _fetch_program_confirmed(conn, client_id, period.end)
        metrics = compute_report(
            await _fetch_month_leads(conn, client_id, period),
            program_confirmed=program_confirmed,
        )
        if not should_send(metrics):
            logger.info(
                "monthly_report: dead month — nothing to report",
                extra={"client_id": str(client_id), "period": period.key},
            )
            return False

        recipients = _recipients(config)
        if not recipients:
            logger.warning(
                "monthly_report: no recipient emails configured",
                extra={"client_id": str(client_id)},
            )
            return False

        roi = metrics.roi_multiple(config.monthly_fee)
        subject = render_subject(business_name, metrics, period.label)
        body = render_html(
            business_name, metrics, period.label, monthly_fee=config.monthly_fee
        )

    # --- send phase: no DB transaction held -------------------------------
    sent = await send_email(to=recipients, subject=subject, html=body)
    if not sent:
        # Most likely RESEND_API_KEY unset (Phase 0) or a vendor blip. Do NOT
        # record the event, so the next cron hour (or day, through the 5th)
        # retries cleanly.
        logger.warning(
            "monthly_report: email not sent (vendor error or RESEND_API_KEY unset)",
            extra={"client_id": str(client_id), "period": period.key},
        )
        return False

    # --- record phase: mark as sent so re-runs are idempotent --------------
    async with set_tenant_context(client_id) as conn:
        await _record_sent(conn, client_id, period, metrics, roi)
    logger.info(
        "monthly report sent",
        extra={
            "client_id": str(client_id),
            "period": period.key,
            "confirmed_recovered": str(metrics.confirmed_recovered),
        },
    )
    return True


async def _fetch_active_clients() -> list[dict[str, Any]]:
    """All active tenants, read with a service-role connection (RLS bypass).

    A tenant-scoped connection with no client set matches ZERO rows under the
    forced RLS policies (migrations 010/011), so cross-tenant enumeration MUST
    use the service connection.
    """
    async with get_service_connection() as conn:
        rows = await conn.fetch(
            """
            SELECT id, business_name, timezone
            FROM clients
            WHERE status = 'active'
            ORDER BY created_at
            """
        )
    return [dict(r) for r in rows]


async def run_reports(*, now: datetime, force: bool = False) -> int:
    """Send every due client's monthly report. Returns the count of emails
    sent. One client's failure never aborts the run — the loop logs and
    continues."""
    clients = await _fetch_active_clients()
    logger.info("monthly_report: %d active client(s)", len(clients))

    sent_count = 0
    for client in clients:
        client_id = UUID(str(client["id"]))
        timezone = client["timezone"]
        if not force and not _is_due(timezone, now):
            continue
        try:
            if await _run_for_client(client_id, client["business_name"], timezone, now=now):
                sent_count += 1
        except Exception as e:
            logger.exception(
                "monthly_report: client failed",
                extra={"client_id": str(client_id)},
                exc_info=e,
            )
    return sent_count


async def main() -> None:
    force = "--force" in sys.argv[1:]
    await init_pool()
    try:
        sent = await run_reports(now=datetime.now(UTC), force=force)
        logger.info("monthly_report run complete — %d email(s) sent", sent)
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
