"""Nightly per-tenant recovery digest.

Once a day at 06:00 in each client's local timezone, email the owner a
one-page summary of the last 24 hours: how many genuine missed-call leads
were captured, how many texted back (the recovery rate — the core LLR
promise), how many qualified, and the value entering the pipeline. It also
shows the noise the system filtered automatically (existing-customer calls,
vendors, spam) so the owner sees what they did NOT have to field.

Run via:
    python -m app.jobs.daily_digest            # honors the 06:00-local gate
    python -m app.jobs.daily_digest --force    # send now for every active client

Scheduling: a single hourly Render cron invokes this; each client is gated
to its own local 06:00 (see ``_is_due``), so one job serves every timezone.
A ``daily_digest_sent`` event per client makes re-runs idempotent within a
day.

Recovery metrics are computed over ``classification = 'potential_lead'``
ONLY — spam, existing customers, and vendors are excluded from the
denominator (see docs/workflow-schema.md, ``digest_inclusion``). The job
spends no AI and no Twilio: pure SQL plus one email, so it runs fully in
Phase 0 with the Anthropic key still unset. A missing Resend key simply
means the email no-ops and the day is retried tomorrow — never a crash.
"""

from __future__ import annotations

import asyncio
import html
import logging
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.db import close_pool, get_service_connection, init_pool, set_tenant_context
from app.models.client_config import ClientConfig
from app.services.notifications import send_email

logger = logging.getLogger(__name__)

# --- tunables --------------------------------------------------------------
DIGEST_HOUR = 6  # 06:00 in the client's local timezone (docs/workflow-schema.md)
WINDOW_HOURS = 24
DEDUP_GUARD_HOURS = 20  # skip if a digest was already recorded this recently
DIGEST_EVENT = "daily_digest_sent"

# Recovery rate is computed over genuine leads only. This is the whole point
# of the classification feature: a spam/vendor/existing-customer call is never
# in the denominator, so the rate reflects real recoverable revenue.
GENUINE = "potential_lead"

# A genuine lead has "recovered" the moment the caller texted back — i.e. it
# left the greeted-but-silent state. ``duplicate`` is a creation-time dedupe
# artifact, not an engagement, so it never counts as recovered.
_NON_RECOVERED = frozenset({"unqualified", "duplicate"})
_QUALIFIED = frozenset({"qualified", "high_value"})
_PENDING = frozenset({"unqualified", "qualifying", "needs_review"})

# Coarse $ midpoints per budget bucket — a directional pipeline estimate, not
# an invoice. Buckets mirror the leads.budget_range CHECK constraint.
BUDGET_MIDPOINTS: dict[str, int] = {
    "<5k": 2_500,
    "5k-15k": 10_000,
    "15k-50k": 32_500,
    "50k+": 75_000,
}

_STATUS_LABELS: dict[str, str] = {
    "unqualified": "Texted &mdash; awaiting reply",
    "qualifying": "In conversation",
    "qualified": "Qualified",
    "high_value": "High value",
    "needs_review": "Needs review",
    "support_touch": "Existing customer",
    "non_lead_contact": "Not a sales lead",
    "spam": "Spam",
    "duplicate": "Duplicate",
}


# ===========================================================================
# Metrics (pure — all SQL lives in the IO layer below, so this is fully
# unit-testable without a database)
# ===========================================================================


@dataclass(frozen=True)
class LeadLine:
    """One genuine lead's row in the digest table."""

    name: str
    phone: str
    service_type: str
    budget_range: str
    status: str


@dataclass(frozen=True)
class DigestMetrics:
    captured: int
    replied: int
    qualified: int
    pending: int
    pipeline_dollars: int
    existing_customer_touches: int
    known_non_lead_contacts: int
    spam_blocked: int
    leads: tuple[LeadLine, ...]

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


def compute_metrics(rows: Iterable[Mapping[str, Any]]) -> DigestMetrics:
    """Fold the window's lead rows into the digest counters.

    ``rows`` is whatever the leads query returned (asyncpg Records or, in
    tests, plain dicts) — anything that supports ``row["col"]``.
    """
    captured = replied = qualified = pending = pipeline = 0
    existing = known_non_lead = spam = 0
    leads: list[LeadLine] = []

    for row in rows:
        classification = row["classification"]
        if classification == GENUINE:
            captured += 1
            status = row["qualification_status"]
            if status not in _NON_RECOVERED:
                replied += 1
            if status in _QUALIFIED:
                qualified += 1
            if status in _PENDING:
                pending += 1
            budget = row["budget_range"]
            pipeline += BUDGET_MIDPOINTS.get(budget or "", 0)
            leads.append(
                LeadLine(
                    name=row["contact_name"] or "Unknown caller",
                    phone=row["phone"] or "",
                    service_type=row["service_type"] or "",
                    budget_range=budget or "",
                    status=status,
                )
            )
        elif classification == "existing_customer":
            existing += 1
        elif classification == "known_non_lead":
            known_non_lead += 1
        elif classification == "spam":
            spam += 1

    return DigestMetrics(
        captured=captured,
        replied=replied,
        qualified=qualified,
        pending=pending,
        pipeline_dollars=pipeline,
        existing_customer_touches=existing,
        known_non_lead_contacts=known_non_lead,
        spam_blocked=spam,
        leads=tuple(leads),
    )


def should_send(metrics: DigestMetrics) -> bool:
    """Skip truly dead days (zero calls of any kind) so the digest stays a
    signal, not daily noise. Any genuine lead OR any filtered call earns a
    send."""
    return metrics.total_activity > 0


# ===========================================================================
# Rendering (pure)
# ===========================================================================


def _count_noun(n: int, singular: str, plural: str | None = None) -> str:
    word = singular if n == 1 else (plural or f"{singular}s")
    return f"{n} {word}"


def render_subject(business_name: str, metrics: DigestMetrics) -> str:
    if metrics.captured > 0:
        leads_word = "lead" if metrics.captured == 1 else "leads"
        return (
            f"{business_name}: {metrics.captured} new {leads_word}, "
            f"{metrics.recovery_rate}% recovered"
        )
    return f"{business_name}: {_count_noun(metrics.handled_total, 'non-lead call')} filtered"


def render_html(business_name: str, metrics: DigestMetrics, date_label: str) -> str:
    return f"""\
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;\
max-width:600px;margin:0 auto;color:#1f2933;">
  <p style="font-size:13px;color:#7b8794;margin:0 0 4px;">\
{html.escape(business_name)} &middot; 24h ending {html.escape(date_label)} 6:00 AM</p>
  <h1 style="font-size:20px;margin:0 0 16px;">Daily recovery digest</h1>
  {_render_hero(metrics)}
  {_render_stats(metrics)}
  {_render_pipeline(metrics)}
  {_render_leads_table(metrics)}
  {_render_handled(metrics)}
  <p style="font-size:12px;color:#9aa5b1;margin-top:28px;border-top:1px solid #e4e7eb;\
padding-top:12px;">Sent automatically by TraceFlow. Recovery rate counts genuine \
missed-call leads only &mdash; existing customers, vendors, and spam are filtered out before \
they reach this number.</p>
</div>"""


def _render_hero(metrics: DigestMetrics) -> str:
    if metrics.captured == 0:
        return (
            '<div style="background:#f5f7fa;border-radius:10px;padding:20px;margin-bottom:16px;">'
            '<p style="margin:0;font-size:15px;color:#52606d;">No new sales leads in the last '
            f"24 hours &mdash; we filtered {_count_noun(metrics.handled_total, 'non-lead call')} "
            "for you.</p></div>"
        )
    return (
        '<div style="background:#ecfdf3;border:1px solid #c6f0d6;border-radius:10px;'
        'padding:20px;margin-bottom:16px;">'
        f'<div style="font-size:40px;font-weight:700;color:#067647;line-height:1;">'
        f"{metrics.recovery_rate}%</div>"
        f'<p style="margin:6px 0 0;font-size:15px;color:#1f2933;">recovered &mdash; {metrics.replied} '
        f"of {metrics.captured} missed-call leads texted back.</p></div>"
    )


def _render_stats(metrics: DigestMetrics) -> str:
    cells = (
        ("Captured", metrics.captured),
        ("Replied", metrics.replied),
        ("Qualified", metrics.qualified),
        ("Pending", metrics.pending),
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


def _render_pipeline(metrics: DigestMetrics) -> str:
    if metrics.pipeline_dollars <= 0:
        return ""
    return (
        '<p style="font-size:15px;margin:0 0 16px;color:#1f2933;">'
        f"<strong>~${metrics.pipeline_dollars:,}</strong> in estimated pipeline added.</p>"
    )


def _render_leads_table(metrics: DigestMetrics) -> str:
    if not metrics.leads:
        return ""
    header = (
        '<tr style="text-align:left;font-size:12px;color:#7b8794;'
        'text-transform:uppercase;letter-spacing:.04em;">'
        '<th style="padding:6px 8px;">Lead</th><th style="padding:6px 8px;">Service</th>'
        '<th style="padding:6px 8px;">Budget</th><th style="padding:6px 8px;">Status</th></tr>'
    )
    body = "".join(_render_lead_row(lead) for lead in metrics.leads)
    return (
        '<h2 style="font-size:15px;margin:0 0 8px;">Your leads</h2>'
        '<table role="presentation" width="100%" '
        'style="border-collapse:collapse;margin-bottom:20px;font-size:14px;">'
        f"{header}{body}</table>"
    )


def _render_lead_row(lead: LeadLine) -> str:
    name = html.escape(lead.name)
    phone = html.escape(lead.phone)
    service = html.escape(lead.service_type) or "&mdash;"
    budget = html.escape(lead.budget_range) or "&mdash;"
    status = html.escape(_STATUS_LABELS.get(lead.status, lead.status))
    contact = (
        f'{name}<br><span style="color:#7b8794;font-size:12px;">{phone}</span>'
        if phone
        else name
    )
    return (
        '<tr style="border-top:1px solid #e4e7eb;">'
        f'<td style="padding:8px;">{contact}</td>'
        f'<td style="padding:8px;">{service}</td>'
        f'<td style="padding:8px;">{budget}</td>'
        f'<td style="padding:8px;">{status}</td></tr>'
    )


def _render_handled(metrics: DigestMetrics) -> str:
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
    """Owner first; fall back to the ops notification list. The digest is the
    owner's morning read, but a client who only set an ops inbox still gets
    it."""
    return list(config.owner_alert_emails or config.notification_emails)


def _local_hour(timezone: str, now: datetime) -> int | None:
    try:
        return now.astimezone(ZoneInfo(timezone)).hour
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("digest: unrecognized timezone — skipping", extra={"timezone": timezone})
        return None


def _is_due(timezone: str, now: datetime) -> bool:
    """True only during the client's local 06:00 hour. An unknown timezone is
    treated as not-due (and logged) — better a missing digest we can see in
    logs than one sent at 3am."""
    return _local_hour(timezone, now) == DIGEST_HOUR


def _date_label(timezone: str, now: datetime) -> str:
    local = now
    try:
        local = now.astimezone(ZoneInfo(timezone))
    except (ZoneInfoNotFoundError, ValueError):
        pass
    # Avoid platform-specific %-d / %#d; build the no-leading-zero day by hand.
    return f"{local:%b} {local.day}, {local.year}"


async def _already_sent(conn: Any, client_id: UUID, now: datetime) -> bool:
    guard_since = now - timedelta(hours=DEDUP_GUARD_HOURS)
    existing = await conn.fetchval(
        """
        SELECT 1 FROM events
        WHERE client_id = $1 AND event_type = $2 AND created_at >= $3
        LIMIT 1
        """,
        client_id,
        DIGEST_EVENT,
        guard_since,
    )
    return existing is not None


async def _fetch_window_leads(
    conn: Any, client_id: UUID, since: datetime
) -> Sequence[Mapping[str, Any]]:
    return await conn.fetch(
        """
        SELECT classification, qualification_status, contact_name, phone,
               service_type, budget_range
        FROM leads
        WHERE client_id = $1 AND created_at >= $2 AND is_test = FALSE
        ORDER BY created_at DESC
        """,
        client_id,
        since,
    )


async def _record_sent(conn: Any, client_id: UUID, metrics: DigestMetrics) -> None:
    await conn.execute(
        """
        INSERT INTO events (client_id, event_type, payload)
        VALUES ($1, $2, $3)
        """,
        client_id,
        DIGEST_EVENT,
        {
            "captured": metrics.captured,
            "replied": metrics.replied,
            "qualified": metrics.qualified,
            "pending": metrics.pending,
            "recovery_rate": metrics.recovery_rate,
            "pipeline_dollars": metrics.pipeline_dollars,
            "existing_customer_touches": metrics.existing_customer_touches,
            "known_non_lead_contacts": metrics.known_non_lead_contacts,
            "spam_blocked": metrics.spam_blocked,
        },
    )


async def _run_for_client(
    client_id: UUID, business_name: str, timezone: str, *, now: datetime
) -> bool:
    """Build and send one client's digest. Returns True iff an email went out.

    Every query is RLS-scoped to this tenant via ``set_tenant_context``. The
    read phase and the record phase are separate tenant contexts so no DB
    transaction is ever held open across the email send (network IO).
    """
    since = now - timedelta(hours=WINDOW_HOURS)

    # --- read phase: gather everything, then release the connection ---------
    async with set_tenant_context(client_id) as conn:
        if await _already_sent(conn, client_id, now):
            logger.info("digest: already sent today", extra={"client_id": str(client_id)})
            return False

        config_row = await conn.fetchrow(
            "SELECT * FROM client_configs WHERE client_id = $1", client_id
        )
        if config_row is None:
            logger.warning("digest: no client_config", extra={"client_id": str(client_id)})
            return False
        config = ClientConfig(**dict(config_row))

        metrics = compute_metrics(await _fetch_window_leads(conn, client_id, since))
        if not should_send(metrics):
            logger.info("digest: nothing to report", extra={"client_id": str(client_id)})
            return False

        recipients = _recipients(config)
        if not recipients:
            logger.warning(
                "digest: no recipient emails configured", extra={"client_id": str(client_id)}
            )
            return False

        subject = render_subject(business_name, metrics)
        body = render_html(business_name, metrics, _date_label(timezone, now))

    # --- send phase: no DB transaction held -------------------------------
    sent = await send_email(to=recipients, subject=subject, html=body)
    if not sent:
        # Most likely RESEND_API_KEY unset (Phase 0) or a vendor blip. Do NOT
        # record the event, so tomorrow's run retries cleanly.
        logger.warning(
            "digest: email not sent (vendor error or RESEND_API_KEY unset)",
            extra={"client_id": str(client_id)},
        )
        return False

    # --- record phase: mark as sent so re-runs are idempotent --------------
    async with set_tenant_context(client_id) as conn:
        await _record_sent(conn, client_id, metrics)
    logger.info(
        "digest sent",
        extra={"client_id": str(client_id), "captured": metrics.captured},
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


async def run_digests(*, now: datetime, force: bool = False) -> int:
    """Send every due client's digest. Returns the count of emails sent.

    One client's failure never aborts the run — the loop logs and continues.
    """
    clients = await _fetch_active_clients()
    logger.info("digest: %d active client(s)", len(clients))

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
                "digest: client failed", extra={"client_id": str(client_id)}, exc_info=e
            )
    return sent_count


async def main() -> None:
    force = "--force" in sys.argv[1:]
    await init_pool()
    try:
        sent = await run_digests(now=datetime.now(UTC), force=force)
        logger.info("digest run complete — %d email(s) sent", sent)
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
