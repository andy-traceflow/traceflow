"""Shared test fixtures and two suite-wide guarantees.

1. No network. Every real httpx transport is patched to refuse; adapters
   and alerts are exercised through httpx.MockTransport, the app through
   Starlette's TestClient. A test that reaches the network fails loudly.
2. No database unless TEST_DB_URL is set. The Postgres-backed store tests
   skip cleanly on a machine without Postgres and run in CI.
"""

from __future__ import annotations

import os

# Set SUPABASE_DB_URL from the test DSN BEFORE any test imports app.main.
# The FastAPI app reads the DSN at lifespan startup; without this the pool
# can't initialize and integration tests via TestClient fail with
# "DB pool not initialized". Has to live in conftest (not a fixture) so it
# runs at collection time, before module imports.
if "TEST_DB_URL" in os.environ:
    os.environ.setdefault("SUPABASE_DB_URL", os.environ["TEST_DB_URL"])
    os.environ.setdefault("ENVIRONMENT", "test")

import httpx
import pytest


@pytest.fixture(autouse=True)
def _no_real_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse any HTTP request that is not going through a MockTransport."""

    def refuse(self: object, request: httpx.Request, *args: object, **kwargs: object) -> None:
        raise RuntimeError(f"test attempted a real HTTP request: {request.method} {request.url}")

    async def refuse_async(
        self: object, request: httpx.Request, *args: object, **kwargs: object
    ) -> None:
        raise RuntimeError(f"test attempted a real HTTP request: {request.method} {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse_async)


@pytest.fixture(scope="session")
def db_url() -> str:
    """Test Postgres DSN.

    Local dev: leave TEST_DB_URL unset — DB-dependent tests skip cleanly so
    a contributor without a local Postgres can still run the unit suite.

    CI: .github/workflows/ci.yml sets TEST_DB_URL pointing at a fresh
    Postgres service and applies migrations before running tests.
    """
    url = os.getenv("TEST_DB_URL", "")
    if not url:
        pytest.skip("TEST_DB_URL not set — skipping DB-dependent tests")
    return url
