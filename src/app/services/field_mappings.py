"""Layer 2 of the integration model: per-client canonical → external field translation.

Adapters call `resolve_mappings(client_id, integration)` once per
operation and translate canonical Lead fields to whatever the external
system calls them. Value transformations are applied via `apply_transform()`.

The mappings table is the single source of truth — field names are
never hardcoded inside adapters.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.db import get_connection

logger = logging.getLogger(__name__)


@dataclass
class FieldMapping:
    canonical_field: str
    external_field: str
    external_field_type: str  # 'standard' | 'custom_field' | 'custom_property' | 'column'
    transform: dict[str, Any] | None = None


async def resolve_mappings(
    client_id: UUID,
    integration: str,
) -> dict[str, FieldMapping]:
    """Returns {canonical_field: FieldMapping} for this client + integration."""
    async with get_connection() as conn:
        rows = await conn.fetch(
            """
            SELECT canonical_field, external_field, external_field_type, transform
            FROM client_field_mappings
            WHERE client_id = $1 AND integration = $2
            """,
            client_id,
            integration,
        )
    return {
        r["canonical_field"]: FieldMapping(
            canonical_field=r["canonical_field"],
            external_field=r["external_field"],
            external_field_type=r["external_field_type"],
            transform=r["transform"],
        )
        for r in rows
    }


def apply_transform(value: Any, transform: dict[str, Any] | None) -> Any:
    """Apply a value transformation to a single field value.

    Supported transform types:
      value_map      : {"type":"value_map","mapping":{"countertop":"Kitchen Counter"}}
      regex_replace  : {"type":"regex_replace","pattern":"...","replacement":"..."}
      numeric_scale  : {"type":"numeric_scale","factor":10.7639}  (sqm→sqft)
      concatenate    : {"type":"concatenate","fields":["first","last"],"separator":" "}
                       — special; expects a dict of all canonical values
      split          : {"type":"split","separator":" "}
    """
    if transform is None or value is None:
        return value

    t = transform.get("type")

    if t == "value_map":
        return transform.get("mapping", {}).get(value, value)

    if t == "regex_replace":
        pattern = transform.get("pattern", "")
        replacement = transform.get("replacement", "")
        return re.sub(pattern, replacement, str(value))

    if t == "numeric_scale":
        factor = float(transform.get("factor", 1.0))
        try:
            return float(value) * factor
        except (TypeError, ValueError):
            return value

    if t == "concatenate":
        if not isinstance(value, dict):
            return value
        sep = transform.get("separator", " ")
        fields = transform.get("fields", [])
        return sep.join(str(value.get(f, "")) for f in fields)

    if t == "split":
        sep = transform.get("separator", ",")
        return str(value).split(sep)

    logger.warning("unknown transform type", extra={"type": t})
    return value


def apply_inverse_transform(value: Any, transform: dict[str, Any] | None) -> Any:
    """Best-effort inverse of apply_transform — used in parse_webhook paths.

    Not all transforms are losslessly invertible (e.g. concatenate). For
    those, we return the value unchanged and rely on the adapter to
    handle it explicitly.
    """
    if transform is None or value is None:
        return value

    t = transform.get("type")

    if t == "value_map":
        mapping = transform.get("mapping", {})
        # Build inverse lookup; first match wins on duplicates.
        inverse = {v: k for k, v in mapping.items()}
        return inverse.get(value, value)

    if t == "numeric_scale":
        factor = float(transform.get("factor", 1.0))
        if factor == 0:
            return value
        try:
            return float(value) / factor
        except (TypeError, ValueError):
            return value

    # regex_replace, concatenate, split: not generally invertible
    return value
