"""One-command tenant provisioner.

Reads a YAML config describing a new client (business info, integration
choice, brand, etc.) and inserts the matching clients + client_configs
rows. Stub for Phase 0 — the schema is in place but the heavy lifting
(Twilio number allocation, Render env-var sync, etc.) is added in
Phase 2 per the UI Maturity Model.

Usage:
    python scripts/onboard_client.py path/to/client.yaml
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

import asyncpg

from app.config import get_settings


async def provision(config_path: Path) -> None:
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        print("PyYAML not installed. `pip install pyyaml` to use this script.", file=sys.stderr)
        sys.exit(1)

    with config_path.open() as fh:
        cfg = yaml.safe_load(fh)

    settings = get_settings()
    if not settings.supabase_db_url:
        print("SUPABASE_DB_URL not set in environment.", file=sys.stderr)
        sys.exit(1)

    client_id = uuid4()
    conn = await asyncpg.connect(settings.supabase_db_url)
    try:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO clients (id, slug, business_name, tier, timezone)
                VALUES ($1, $2, $3, $4, $5)
                """,
                client_id,
                cfg["slug"],
                cfg["business_name"],
                cfg.get("tier", "standard"),
                cfg.get("timezone", "America/Los_Angeles"),
            )
            await conn.execute(
                """
                INSERT INTO client_configs (
                    client_id,
                    business_hours,
                    service_area_zips,
                    crm_provider,
                    brand,
                    notification_emails
                ) VALUES ($1, $2::jsonb, $3, $4, $5::jsonb, $6)
                """,
                client_id,
                json.dumps(cfg.get("business_hours") or {}),
                cfg.get("service_area_zips") or [],
                cfg.get("crm_provider"),
                json.dumps(cfg.get("brand") or {}),
                cfg.get("notification_emails") or [],
            )

        print(f"client provisioned: id={client_id} slug={cfg['slug']}")

    finally:
        await conn.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/onboard_client.py path/to/client.yaml", file=sys.stderr)
        sys.exit(1)
    asyncio.run(provision(Path(sys.argv[1])))
