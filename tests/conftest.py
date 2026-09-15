"""Shared test fixtures.

Most tests run in-process with every adapter HTTP call mocked. The
DB-backed suites (dedupe, worker claiming, retry) need a real Postgres
with the migrations applied; they skip cleanly when no DSN is provided.
"""

from __future__ import annotations

import os

# Set SUPABASE_DB_URL from the test DSN BEFORE any test imports app.main.
# The FastAPI app reads the DSN at lifespan startup; without this the pool
# can't initialize and integration tests via TestClient fail with
# "DB pool not initialized". Has to live in conftest (not a fixture) so it
# runs at collection time, before module imports.
if "TRACEFLOW_TEST_DB_URL" in os.environ:
    os.environ.setdefault("SUPABASE_DB_URL", os.environ["TRACEFLOW_TEST_DB_URL"])
    os.environ.setdefault("ENVIRONMENT", "test")

import pytest


@pytest.fixture(scope="session")
def db_url() -> str:
    """Test Postgres DSN.

    Local dev: leave TRACEFLOW_TEST_DB_URL unset — DB-dependent tests skip
    cleanly so a contributor without a local Postgres can still run the
    pure unit suite.

    CI: .github/workflows/ci.yml sets TRACEFLOW_TEST_DB_URL pointing at a
    fresh Postgres service and applies migrations before running tests.
    """
    url = os.getenv("TRACEFLOW_TEST_DB_URL", "")
    if not url:
        pytest.skip("TRACEFLOW_TEST_DB_URL not set — skipping DB-dependent tests")
    return url
