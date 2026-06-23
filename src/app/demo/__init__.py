"""Self-contained demo dataset + fake DB connection for DEMO_MODE.

Importing this package pulls in only in-memory fixtures — no DB, no secrets,
no real client data. Used by ``db.get_service_connection`` (per-request, for
the demo role) and by ``scripts/dev_admin_preview.py``.
"""

from __future__ import annotations

from .fake_conn import FakeConn, fake_service_connection, set_preview_admin_row
from .fixtures import DEMO_ADMIN_ID, DEMO_ADMIN_NAME, DEMO_EMAIL, clients

__all__ = [
    "DEMO_ADMIN_ID",
    "DEMO_ADMIN_NAME",
    "DEMO_EMAIL",
    "FakeConn",
    "clients",
    "fake_service_connection",
    "set_preview_admin_row",
]
