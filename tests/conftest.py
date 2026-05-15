"""Shared test fixtures.

Most tests run against an in-memory or short-lived setup. The
test_tenant_isolation suite is the exception — it needs a real
Postgres with RLS to provide meaningful coverage. Run that suite
against a test Supabase project or a local Postgres with the
migrations applied.
"""

from __future__ import annotations

import os
from uuid import UUID, uuid4

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
