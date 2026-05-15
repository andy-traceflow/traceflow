"""Webhook dedupe service tests."""

from __future__ import annotations

import time
from uuid import uuid4

import pytest

from app.services.dedupe import DEFAULT_TTL_SECONDS, is_duplicate, reset


@pytest.fixture(autouse=True)
def _reset_cache():
    reset()
    yield
    reset()


def test_first_call_is_not_a_duplicate():
    client = uuid4()
    assert is_duplicate(client, "shopify", 12345) is False


def test_repeat_within_ttl_is_a_duplicate():
    client = uuid4()
    is_duplicate(client, "shopify", 12345)
    assert is_duplicate(client, "shopify", 12345) is True


def test_different_clients_dont_collide():
    a, b = uuid4(), uuid4()
    is_duplicate(a, "shopify", 12345)
    assert is_duplicate(b, "shopify", 12345) is False


def test_different_sources_dont_collide():
    client = uuid4()
    is_duplicate(client, "shopify", 12345)
    assert is_duplicate(client, "twilio", 12345) is False


def test_empty_id_is_never_a_duplicate():
    client = uuid4()
    assert is_duplicate(client, "shopify", "") is False
    assert is_duplicate(client, "shopify", None) is False  # type: ignore[arg-type]


def test_expiry_clears_the_entry(monkeypatch):
    """Entries older than TTL are evicted on next call."""
    client = uuid4()
    is_duplicate(client, "shopify", 12345, ttl_seconds=1)

    real_time = time.time

    def fast_forward() -> float:
        return real_time() + 2.0

    monkeypatch.setattr(time, "time", fast_forward)
    # Same key after TTL → not a duplicate
    assert is_duplicate(client, "shopify", 12345, ttl_seconds=1) is False


def test_default_ttl_is_one_hour():
    assert DEFAULT_TTL_SECONDS == 3600
