"""One-command tenant provisioner (CLI).

Reads a YAML config describing a new client and creates the matching clients +
client_configs rows. Shares the SAME create path as the admin "promote" action
(app.services.provisioning.provision_client) so there is exactly one place that
writes a tenant — the CLI and the UI can never drift.

Stub scope unchanged from Phase 0: the heavy lifting (Twilio number allocation,
webhook signing-secret generation, Render env-var sync) is still Phase 2 and
lives in provisioning.py when it lands.

Usage:
    python scripts/onboard_client.py path/to/client.yaml
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from app.config import get_settings
from app.db import close_pool, init_pool
from app.services.provisioning import ProvisionSpec, SlugConflictError, provision_client


async def provision(config_path: Path) -> None:
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        print("PyYAML not installed. `pip install pyyaml` to use this script.", file=sys.stderr)
        sys.exit(1)

    with config_path.open() as fh:
        cfg = yaml.safe_load(fh) or {}

    if not get_settings().supabase_db_url:
        print("SUPABASE_DB_URL not set in environment.", file=sys.stderr)
        sys.exit(1)

    # Keep only recognized fields — a stray YAML key is ignored here rather than
    # rejected, since ProvisionSpec(extra="forbid") would otherwise hard-fail.
    known = {k: v for k, v in cfg.items() if k in ProvisionSpec.model_fields}
    try:
        spec = ProvisionSpec(**known)
    except Exception as e:  # pydantic ValidationError → readable CLI error
        print(f"Invalid client config: {e}", file=sys.stderr)
        sys.exit(1)

    await init_pool()
    try:
        client_id = await provision_client(spec)
    except SlugConflictError:
        print(f"A client with slug '{spec.slug}' already exists.", file=sys.stderr)
        sys.exit(1)
    finally:
        await close_pool()

    print(f"client provisioned: id={client_id} slug={spec.slug}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/onboard_client.py path/to/client.yaml", file=sys.stderr)
        sys.exit(1)
    asyncio.run(provision(Path(sys.argv[1])))
