"""Destination adapter Protocol and the record contract.

One deployment pushes to exactly one destination, selected by the
DESTINATION env var and constructed once by the registry. Adapters read
their own credentials from the environment at construction and raise
AdapterConfigError if anything is missing — the lifespan handler
constructs the adapter at boot, so a misconfigured deploy never starts.

The record contract
-------------------
`upsert_record()` receives a plain dict produced by the mapping layer.
Keys are the destination's field display names ("Order ID", "Total");
values are JSON scalars, ISO-8601 date strings, or lists (multi-select,
relations). Adapters resolve display names to whatever the destination
really wants — Monday column ids, Notion property types, Sheets column
positions — by introspecting the destination, never by hard-coding.

Reserved keys, all optional, all underscore-prefixed so they cannot
collide with a real field name:

  _name        str         human label: Monday item name, Slack header,
                           Notion title when no field targets the title property
  _key         str         name of the field to match on for update-instead-
                           of-create. Append-only destinations (Slack, Sheets)
                           ignore it.
  _line_items  list[dict]  sub-records with the same shape. Monday → subitems,
                           Slack → bullet list; others ignore with a warning.

Failure signalling
------------------
Let httpx.HTTPStatusError propagate (via raise_for_status) for HTTP
failures — the worker classifies 4xx as permanent and 5xx/408/429 as
transient. Where a destination reports errors inside a 200 body (GraphQL,
Slack), raise PermanentDeliveryError or TransientDeliveryError explicitly.
"""

from __future__ import annotations

import os
from typing import Any, Protocol, runtime_checkable

RESERVED_KEYS = frozenset({"_name", "_key", "_line_items"})


class AdapterConfigError(ValueError):
    """A required credential or setting for the configured destination is missing."""


@runtime_checkable
class Destination(Protocol):
    name: str

    async def upsert_record(self, record: dict[str, Any]) -> str:
        """Create or update the record downstream. Returns the external id."""
        ...

    async def health_check(self) -> bool:
        """Verify credentials and connectivity."""
        ...


# ---------------------------------------------------------------------------
# Helpers shared by adapters
# ---------------------------------------------------------------------------


def require_env(name: str) -> str:
    """Read a required env var at construction time; fail closed if missing."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise AdapterConfigError(f"{name} is required for the configured destination")
    return value


def optional_env(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


def split_record(record: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Separate destination fields from reserved `_`-prefixed metadata."""
    fields = {k: v for k, v in record.items() if not k.startswith("_")}
    meta = {k: v for k, v in record.items() if k in RESERVED_KEYS}
    return fields, meta


def display_name(record: dict[str, Any], fallback: str = "Untitled") -> str:
    """`_name`, else the first non-empty string field, else the fallback."""
    name = record.get("_name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    for key, value in record.items():
        if not key.startswith("_") and isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list | tuple | set):
        return list(value)
    return [value]
