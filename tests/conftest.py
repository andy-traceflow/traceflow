"""Shared test fixtures.

Most tests run against an in-memory or short-lived setup. The
test_tenant_isolation and test_generic_webhook suites are the
exceptions — they need a real Postgres with RLS to provide meaningful
coverage. Run those against a test Supabase project or a local
Postgres with the migrations applied.
"""

from __future__ import annotations

import os
from uuid import UUID, uuid4

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

    Local dev: leave TRACEFLOW_TEST_DB_URL unset — DB-dependent tests
    (including the tenant isolation suite) skip cleanly so a contributor
    without a local Postgres can still run the pure unit suite.

    CI: .github/workflows/ci.yml sets TRACEFLOW_TEST_DB_URL pointing at
    a fresh pgvector-enabled Postgres service, applies migrations, and
    hard-fails the build on any cross-tenant leak.
    """
    url = os.getenv("TRACEFLOW_TEST_DB_URL", "")
    if not url:
        pytest.skip("TRACEFLOW_TEST_DB_URL not set — skipping DB-dependent tests")
    return url


@pytest.fixture
def client_a_id() -> UUID:
    return uuid4()


@pytest.fixture
def client_b_id() -> UUID:
    return uuid4()
