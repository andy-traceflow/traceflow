"""Run the admin surface against the canned multi-tenant demo fixtures — UI
dev/preview only, no database.

    python scripts/dev_admin_preview.py        # serves http://localhost:8000/admin
    login: dev@traceflow.app / preview

The REAL app runs (real routers, real auth, real JWTs); only the DB layer is
swapped for the in-memory FakeConn shared with DEMO_MODE (see app/demo/). It
logs in as an OWNER — not the locked-down demo role — so config edits,
mark-test, record-outcome, and mapping upserts round-trip against the fixtures;
everything resets on restart.

NEVER deploy this script: it binds localhost with a hardcoded secret/password
and monkeypatches the DB getter process-wide. The *deployed* read-only demo is
the DEMO_MODE path inside the real app (per-request FakeConn switch + a
read-only demo role), not this harness.
"""

from __future__ import annotations

import os
from uuid import uuid4

os.environ.setdefault("ADMIN_JWT_SECRET", "dev-preview-secret-0123456789abcdef")


def main() -> None:
    import uvicorn

    import app.routers.admin.activity as activity_mod
    import app.routers.admin.auth as auth_mod
    import app.routers.admin.clients as clients_mod
    import app.routers.admin.leads as leads_mod
    import app.routers.admin.mappings as mappings_mod
    import app.services.admin_auth as admin_auth_mod
    import app.services.audit as audit_mod
    from app.demo import fake_service_connection, set_preview_admin_row
    from app.main import app
    from app.services.admin_auth import hash_password

    # Owner identity for the preview login (role 'owner' so writes are allowed —
    # the demo role would be blocked by forbid_demo_writes). FakeConn resolves
    # admin_users against this row.
    set_preview_admin_row(
        {
            "id": uuid4(),
            "email": "dev@traceflow.app",
            "name": "Dev Preview",
            "role": "owner",
            "is_active": True,
            "password_hash": hash_password("preview"),
            "last_login_at": None,
        }
    )

    # Swap the DB getter at every admin import site for the in-memory FakeConn.
    for mod in (
        auth_mod, clients_mod, leads_mod, activity_mod, mappings_mod,
        admin_auth_mod, audit_mod,
    ):
        mod.get_service_connection = fake_service_connection  # type: ignore[attr-defined]

    print("Admin preview: http://localhost:8000/admin  (dev@traceflow.app / preview)")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
