"""The transform → deliver pipeline the worker runs on each event, and the
exception types adapters use to tell the worker how to react.

`build_pipeline()` resolves the destination from DESTINATION via the
adapter registry. Phase 5 replaces `passthrough` with the mapping.yaml
transform.

This module imports nothing from app.adapters at module level — adapters
import the error types from here, so the dependency points one way.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.models.event import Event


class DeliveryError(Exception):
    """Base for adapter-raised delivery failures."""


class PermanentDeliveryError(DeliveryError):
    """Retrying will not help (destination rejected the record). Goes straight to dead."""


class TransientDeliveryError(DeliveryError):
    """Retrying may help (destination unavailable, rate-limited, timed out)."""


class TransformError(Exception):
    """The mapping could not produce a record from this payload. Permanent."""


Transform = Callable[[Event], dict[str, Any]]
Deliver = Callable[[dict[str, Any]], Awaitable[str]]


@dataclass(frozen=True)
class Pipeline:
    transform: Transform
    deliver: Deliver


def passthrough(event: Event) -> dict[str, Any]:
    """Identity transform: the raw payload is the record."""
    if isinstance(event.payload, dict):
        return event.payload
    return {"payload": event.payload}


def build_pipeline() -> Pipeline:
    """Construct the production pipeline from environment configuration.

    Raises ValueError (incl. AdapterConfigError) when DESTINATION is unknown
    or its credentials are missing — callers run this at boot, fail closed.
    """
    from app.adapters.registry import get_adapter  # local: keeps adapters → pipeline one-way
    from app.config import get_settings

    destination = get_adapter(get_settings().destination)
    return Pipeline(transform=passthrough, deliver=destination.upsert_record)
