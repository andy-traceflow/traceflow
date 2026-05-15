"""In-memory webhook deduplication with TTL.

Webhook providers retry on 5xx or timeout — if we accept the same event
twice we create duplicate leads. This module tracks recently-seen IDs
keyed by `(client_id, source, external_id)` and drops repeats within a
configurable TTL.

Per-process state is fine for single-replica deployments. For multi-
replica deployments, swap the storage for Redis behind the same API.
"""

from __future__ import annotations

import logging
import time
from uuid import UUID

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 3600  # 1 hour

_seen: dict[str, float] = {}


def _key(client_id: UUID, source: str, external_id: str | int) -> str:
    return f"{client_id}:{source}:{external_id}"


def is_duplicate(
    client_id: UUID,
    source: str,
    external_id: str | int,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> bool:
    """Returns True if this (client, source, id) tuple was seen within ttl_seconds.

    First call for a given tuple records the timestamp and returns False.
    """
    if external_id is None or external_id == "":
        # No ID = can't dedupe; let it through.
        return False

    now = time.time()
    _gc(now, ttl_seconds)

    k = _key(client_id, source, external_id)
    if k in _seen:
        logger.info(
            "duplicate event suppressed",
            extra={"client_id": str(client_id), "source": source, "external_id": str(external_id)},
        )
        return True

    _seen[k] = now
    return False


def _gc(now: float, ttl_seconds: int) -> None:
    """Drop entries older than TTL. O(N) over the cache; cheap at our scale."""
    expired = [k for k, t in _seen.items() if now - t > ttl_seconds]
    for k in expired:
        del _seen[k]


def reset() -> None:
    """Clear the cache. For tests only."""
    _seen.clear()
