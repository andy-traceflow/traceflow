"""TrustedHost allow-list: config parsing + reject/allow behavior.

The middleware is opt-in (off when ALLOWED_HOSTS is unset). Because main.py
reads settings at import time, these tests exercise the property and a
standalone app wired the same way, rather than reimporting main.
"""

from __future__ import annotations

from fastapi import FastAPI
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.testclient import TestClient

from app.config import Settings


def test_allowed_hosts_empty_when_unset() -> None:
    assert Settings(allowed_hosts="").allowed_hosts_list == []


def test_allowed_hosts_appends_local_and_test_hosts() -> None:
    hosts = Settings(allowed_hosts="app.traceflow.app,*.onrender.com").allowed_hosts_list
    assert "app.traceflow.app" in hosts
    assert "*.onrender.com" in hosts
    for local in ("localhost", "127.0.0.1", "testserver"):
        assert local in hosts


def _app_with_hosts(hosts: list[str]) -> FastAPI:
    app = FastAPI()
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    return app


def test_known_host_is_allowed() -> None:
    client = TestClient(_app_with_hosts(Settings(allowed_hosts="app.traceflow.app").allowed_hosts_list))
    resp = client.get("/health", headers={"host": "app.traceflow.app"})
    assert resp.status_code == 200


def test_unknown_host_is_rejected() -> None:
    client = TestClient(_app_with_hosts(Settings(allowed_hosts="app.traceflow.app").allowed_hosts_list))
    resp = client.get("/health", headers={"host": "evil.example.com"})
    assert resp.status_code == 400
