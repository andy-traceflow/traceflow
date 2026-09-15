"""The transform → deliver pipeline the worker runs on each event, and the
exception types adapters use to tell the worker how to react.

`build_pipeline()` loads mapping.yaml, resolves the destination from
DESTINATION via the adapter registry, and cross-checks the two. It is
called at boot by both the web service and the worker, so every
configuration mistake refuses the process instead of dead-lettering
the first event.

This module imports nothing from app.adapters or app.mapping at module
level — both import the error types from here, so dependencies point
one way.
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
    """Construct the production pipeline from mapping.yaml + environment.

    Raises MappingError for a missing/invalid mapping, ValueError (incl.
    AdapterConfigError) for an unknown DESTINATION or missing credentials,
    and RuntimeError when mapping.yaml's `destination` disagrees with
    DESTINATION. Callers run this at boot: fail closed.
    """
    from app.adapters.registry import get_adapter  # local: keeps adapters → pipeline one-way
    from app.config import get_settings
    from app.mapping import load_mapping, transform_for

    settings = get_settings()
    mapping = load_mapping(settings.mapping_path or None)
    if mapping.destination != settings.destination:
        raise RuntimeError(
            f"mapping.yaml says destination: {mapping.destination!r} but DESTINATION="
            f"{settings.destination!r} — they must agree"
        )
    destination = get_adapter(settings.destination)
    return Pipeline(transform=transform_for(mapping), deliver=destination.upsert_record)
