"""Hourly cron: ping every active client's CRM adapter.

Catches credential rotations, API outages, rate limits before clients
notice. Failures land in events for visibility and (TODO) trigger
founder alerts.

Run via:
    python -m app.jobs.adapter_health
"""

from __future__ import annotations

import asyncio
import json
import logging
from uuid import UUID

from app.adapters.registry import get_adapter
from app.db import close_pool, get_connection, init_pool, set_tenant_context
from app.models.client_config import ClientConfig

logger = logging.getLogger(__name__)


async def main() -> None:
    await init_pool()
    try:
        await _check_all_clients()
    finally:
        await close_pool()


async def _check_all_clients() -> None:
    async with get_connection() as conn:
        rows = await conn.fetch(
            """
            SELECT c.id, cc.crm_provider
            FROM clients c
            JOIN client_configs cc ON cc.client_id = c.id
            WHERE c.status = 'active' AND cc.crm_provider IS NOT NULL
            """
        )

    logger.info("adapter health check: %d active clients with a CRM provider", len(rows))

    for r in rows:
        client_id = UUID(str(r["id"]))
        provider = r["crm_provider"]
        try:
            await _check_one(client_id, provider)
        except Exception as e:
            logger.exception("health check failed", extra={"client_id": str(client_id)}, exc_info=e)


async def _check_one(client_id: UUID, provider: str) -> None:
    async with set_tenant_context(client_id) as conn:
        row = await conn.fetchrow(
            """
            SELECT *
            FROM client_configs
            WHERE client_id = $1
            """,
            client_id,
        )
        if row is None:
            logger.warning("no config", extra={"client_id": str(client_id)})
            return

        config = ClientConfig(**dict(row))
        adapter = get_adapter(provider)
        ok = await adapter.health_check(config)

        await conn.execute(
            """
            INSERT INTO events (client_id, event_type, payload)
            VALUES ($1, 'adapter_health_check', $2::jsonb)
            """,
            client_id,
            json.dumps({"provider": provider, "healthy": ok}),
        )

        if not ok:
            logger.warning(
                "adapter unhealthy",
                extra={"client_id": str(client_id), "provider": provider},
            )


if __name__ == "__main__":
    asyncio.run(main())
