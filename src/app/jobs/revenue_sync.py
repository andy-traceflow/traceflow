"""Scheduled CRM revenue readback — confirmed recovered-revenue attribution.

For each active client whose ``revenue_config.mode`` is ``crm``, read the booked
value of every lead we pushed to the CRM within the attribution window and
freeze it onto the lead (``recovered_value`` + ``outcome='won'``,
``outcome_source='crm'``). This is the *confirmed* recovered revenue the monthly
report and case study stand on — distinct from the digest's budget-bucket
*estimate*.

Snapshot-bounded: a lead is read back only while it is within
``attribution_window_days`` of creation (default 90). The value is refreshed on
each run while in window (so a deal that grows from deposit to final payment is
captured), then frozen when the lead ages out — so a contact who books a second
job months later never inflates the figure attributed to the original missed
call. See docs/decisions/0003-recovered-revenue-attribution.md.

Spends no AI and no Twilio — pure CRM reads + SQL — so it runs fully in Phase 0.
A client with no CRM (or ``mode != 'crm'``) is skipped; their recovered revenue
comes from the admin outcome endpoint (owner report) instead. A per-client read
failure is logged and counted; it never aborts the sweep.

Run via:
    python -m app.jobs.revenue_sync
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.adapters.registry import get_adapter
from app.db import close_pool, get_service_connection, init_pool, set_tenant_context
from app.models.client_config import ClientConfig

logger = logging.getLogger(__name__)

SYNC_TARGET = "crm_revenue"  # sync_log.target


def needs_update(current: Decimal | None, fetched: Decimal) -> bool:
    """True when the CRM reported a positive value that differs from what we
    already have stored. A non-positive read never overwrites a stored value."""
    if fetched <= 0:
        return False
    return current is None or fetched != current


@dataclass(frozen=True)
class _Candidate:
    """A pushed lead still inside its attribution window."""

    lead_id: UUID
    external_id: str
    recovered_value: Decimal | None


# ===========================================================================
# IO — all tenant-scoped (RLS) except the active-client enumeration
# ===========================================================================


async def _fetch_active_client_ids() -> list[UUID]:
    """All active tenants, via the service role (RLS bypass) — a tenant-scoped
    connection with no client set matches zero rows under forced RLS."""
    async with get_service_connection() as conn:
        rows = await conn.fetch(
            "SELECT id FROM clients WHERE status = 'active' ORDER BY created_at"
        )
    return [UUID(str(r["id"])) for r in rows]


async def _load_config(client_id: UUID) -> ClientConfig | None:
    async with set_tenant_context(client_id) as conn:
        row = await conn.fetchrow(
            "SELECT * FROM client_configs WHERE client_id = $1", client_id
        )
    return ClientConfig(**dict(row)) if row else None


async def _fetch_candidates(
    conn: Any, client_id: UUID, since: datetime
) -> list[_Candidate]:
    rows = await conn.fetch(
        """
        SELECT id, external_id, recovered_value
        FROM leads
        WHERE client_id = $1
          AND external_id IS NOT NULL
          AND created_at >= $2
          AND is_test = FALSE
        """,
        client_id,
        since,
    )
    return [
        _Candidate(r["id"], str(r["external_id"]), r["recovered_value"]) for r in rows
    ]


async def _apply_update(
    conn: Any, client_id: UUID, lead_id: UUID, value: Decimal
) -> None:
    await conn.execute(
        """
        UPDATE leads
        SET recovered_value = $1, outcome = 'won', outcome_source = 'crm',
            outcome_recorded_at = NOW()
        WHERE id = $2 AND client_id = $3
        """,
        value,
        lead_id,
        client_id,
    )


async def _record_sync(
    conn: Any, client_id: UUID, *, total: int, succeeded: int, failed: int
) -> None:
    await conn.execute(
        """
        INSERT INTO sync_log
            (client_id, target, finished_at, total_entries, succeeded, failed, triggered_by)
        VALUES ($1, $2, NOW(), $3, $4, $5, 'cron')
        """,
        client_id,
        SYNC_TARGET,
        total,
        succeeded,
        failed,
    )


# ===========================================================================
# Orchestration
# ===========================================================================


async def _sync_client(client_id: UUID, config: ClientConfig, *, now: datetime) -> int:
    """Read back + freeze recovered values for one tenant. Returns rows updated.

    Read and write phases are separate tenant contexts so no DB transaction is
    held open across the CRM network calls.
    """
    if config.revenue_mode != "crm" or not config.crm_provider:
        return 0
    try:
        adapter = get_adapter(config.crm_provider)
    except ValueError:
        logger.warning(
            "revenue_sync: unknown crm_provider — skipping",
            extra={"client_id": str(client_id), "provider": config.crm_provider},
        )
        return 0

    since = now - timedelta(days=config.attribution_window_days)

    # read phase — gather candidates, then release the connection
    async with set_tenant_context(client_id) as conn:
        candidates = await _fetch_candidates(conn, client_id, since)
    if not candidates:
        return 0

    # fetch phase — CRM network IO, no DB transaction held
    updates: list[tuple[UUID, Decimal]] = []
    failed = 0
    for candidate in candidates:
        try:
            value = await adapter.fetch_recovered_value(candidate.external_id, config)
        except Exception as e:
            failed += 1
            logger.warning(
                "revenue_sync: fetch failed",
                extra={"client_id": str(client_id), "lead_id": str(candidate.lead_id)},
                exc_info=e,
            )
            continue
        if value is not None and needs_update(candidate.recovered_value, value):
            updates.append((candidate.lead_id, value))

    # write phase — apply updates + record the run
    async with set_tenant_context(client_id) as conn:
        for lead_id, value in updates:
            await _apply_update(conn, client_id, lead_id, value)
        await _record_sync(
            conn, client_id, total=len(candidates), succeeded=len(updates), failed=failed
        )

    if updates or failed:
        logger.info(
            "revenue_sync: client done",
            extra={
                "client_id": str(client_id),
                "scanned": len(candidates),
                "updated": len(updates),
                "failed": failed,
            },
        )
    return len(updates)


async def run_sync(*, now: datetime) -> int:
    """Sync every active client. Returns total leads updated. One client's
    failure never aborts the run — the loop logs and continues."""
    client_ids = await _fetch_active_client_ids()
    logger.info("revenue_sync: %d active client(s)", len(client_ids))

    total_updated = 0
    for client_id in client_ids:
        try:
            config = await _load_config(client_id)
            if config is None:
                logger.warning(
                    "revenue_sync: no client_config", extra={"client_id": str(client_id)}
                )
                continue
            total_updated += await _sync_client(client_id, config, now=now)
        except Exception as e:
            logger.exception(
                "revenue_sync: client failed",
                extra={"client_id": str(client_id)},
                exc_info=e,
            )
    return total_updated


async def main() -> None:
    await init_pool()
    try:
        updated = await run_sync(now=datetime.now(UTC))
        logger.info("revenue_sync complete — %d lead(s) updated", updated)
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
