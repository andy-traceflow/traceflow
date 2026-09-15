"""WORKER_MODE: the in-process worker starts and stops with the app; the supervisor
restarts a crashed loop; an invalid mode refuses to boot."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.main as app_main
import app.worker as worker_module
from app.adapters.registry import reset_registry
from app.config import get_settings, require_startup_settings
from app.worker import run_supervised


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://unused")
    monkeypatch.setenv("DESTINATION", "slack")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_CHANNEL", "#demo")
    monkeypatch.setenv("ADMIN_TOKEN", "t")
    monkeypatch.delenv("WORKER_MODE", raising=False)
    get_settings.cache_clear()
    reset_registry()

    async def _noop() -> None:
        pass

    monkeypatch.setattr(app_main, "init_pool", _noop)
    monkeypatch.setattr(app_main, "close_pool", _noop)
    yield
    reset_registry()
    get_settings.cache_clear()


class FakeSupervisor:
    def __init__(self) -> None:
        self.started = False
        self.cancelled = False
        self.args: tuple[Any, ...] = ()

    async def __call__(self, *args: Any, **kwargs: Any) -> None:
        self.started = True
        self.args = args
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def _wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)


def test_default_mode_is_inprocess_and_worker_starts_and_stops(monkeypatch):
    fake = FakeSupervisor()
    monkeypatch.setattr(app_main, "run_supervised", fake)

    with TestClient(app_main.app):
        _wait_for(lambda: fake.started)
        assert fake.started
        assert not fake.cancelled
        store, pipeline = fake.args
        assert hasattr(store, "claim") and callable(pipeline.deliver)

    assert fake.cancelled  # shutdown cancelled the task and waited for it


def test_separate_mode_does_not_start_the_worker(monkeypatch):
    monkeypatch.setenv("WORKER_MODE", "separate")
    get_settings.cache_clear()
    fake = FakeSupervisor()
    monkeypatch.setattr(app_main, "run_supervised", fake)

    with TestClient(app_main.app):
        time.sleep(0.05)
        assert not fake.started


def test_invalid_worker_mode_refuses_to_start(monkeypatch):
    monkeypatch.setenv("WORKER_MODE", "sideways")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="WORKER_MODE must be one of"):
        require_startup_settings()
    with pytest.raises(RuntimeError, match="WORKER_MODE"):
        with TestClient(app_main.app):
            pass


async def test_run_supervised_restarts_after_a_crash(monkeypatch, caplog):
    calls: list[int] = []

    async def flaky_run_forever(*args: Any, **kwargs: Any) -> None:
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            raise RuntimeError("DB pool not initialized")
        await asyncio.Event().wait()

    monkeypatch.setattr(worker_module, "run_forever", flaky_run_forever)

    task = asyncio.create_task(run_supervised(object(), object(), restart_delay=0))  # type: ignore[arg-type]
    for _ in range(200):
        if len(calls) >= 2:
            break
        await asyncio.sleep(0.005)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert calls == [1, 2]  # crashed once, restarted once, then ran until cancelled
    assert any("worker loop crashed" in r.getMessage() for r in caplog.records)
