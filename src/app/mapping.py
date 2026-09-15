"""mapping.yaml — the one file that describes what a deployment sends where.

Loaded and validated with Pydantic at startup (web service and worker),
so a malformed mapping fails the deploy, not the first webhook.

Shape:

    source: shopify
    topic: orders/create
    destination: notion
    key: "Order ID"        # optional: field (by `to`) that identifies the record → upsert
    name: "$.name"         # optional: path for the record's human label (_name)
    fields:
      - from: "$.total_price"          # JSONPath ($...) or dotted path (a.b.c)
        to: "Total"                    # destination display name
        type: number                   # coercion; see FieldType
        transform: to_decimal          # named, list of named, or dict (see apply_transform)
        fallback: "$.billing_address.company"   # second path if `from` is empty
        default: 0                     # literal if both paths are empty
        required: false                # true → empty is a permanent failure
    line_items:
      enabled: true
      from: "$.line_items"
      to: subitems                     # → record["_line_items"]
      name: "$.title"
      fields: [...]                    # same FieldSpec shape, resolved per item

The output is the adapter record contract (see adapters/base.py): a dict
of display names plus `_name`, `_key`, `_line_items`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from jsonpath_ng import parse as jsonpath_parse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.models.event import Event
from app.pipeline import Transform, TransformError

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAPPING_FILENAME = "mapping.yaml"

FieldType = Literal[
    "title",
    "text",
    "rich_text",
    "number",
    "email",
    "phone_number",
    "url",
    "date",
    "select",
    "multi_select",
    "relation",
    "checkbox",
]


class MappingError(RuntimeError):
    """mapping.yaml could not be loaded or is invalid. Raised at startup."""


# ---------------------------------------------------------------------------
# Path resolution — JSONPath or dotted
# ---------------------------------------------------------------------------


@lru_cache(maxsize=256)
def compile_path(path: str) -> Any:
    """Return a compiled JSONPath for `$...` expressions, or None for dotted paths."""
    if path.startswith("$"):
        try:
            return jsonpath_parse(path)
        except Exception as e:  # jsonpath_ng raises a few different types
            raise MappingError(f"invalid JSONPath {path!r}: {e}") from e
    return None


def resolve_path(payload: Any, path: str) -> Any:
    """Resolve a `from:` expression against a payload.

    JSONPath: no match → None, one match → the value, many → a list.
    Dotted (`customer.email`, `line_items.0.sku`): walks dicts and list
    indexes, None on any miss.
    """
    expr = compile_path(path)
    if expr is not None:
        matches = [m.value for m in expr.find(payload)]
        if not matches:
            return None
        return matches[0] if len(matches) == 1 else matches

    current = payload
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            idx = int(part)
            current = current[idx] if idx < len(current) else None
        else:
            return None
        if current is None:
            return None
    return current


# ---------------------------------------------------------------------------
# Transforms
# ---------------------------------------------------------------------------


def _to_number(value: Any) -> float:
    return float(value)


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "paid"}


def _to_date(value: Any) -> str:
    """ISO datetime/date string (or datetime) → YYYY-MM-DD."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()


NAMED_TRANSFORMS: dict[str, Callable[[Any], Any]] = {
    "to_decimal": _to_number,
    "to_number": _to_number,
    "to_int": lambda v: int(float(v)),
    "to_str": str,
    "strip": lambda v: str(v).strip(),
    "upper": lambda v: str(v).upper(),
    "lower": lambda v: str(v).lower(),
    "title_case": lambda v: str(v).title(),
    "digits_only": lambda v: re.sub(r"\D", "", str(v)),
    "to_bool": _to_bool,
    "to_date": _to_date,
    "join": lambda v: ", ".join(str(x) for x in v) if isinstance(v, list) else v,
    "first": lambda v: v[0] if isinstance(v, list) and v else v,
}

DICT_TRANSFORM_TYPES = frozenset({"value_map", "regex_replace", "numeric_scale", "concatenate", "split"})


def apply_transform(value: Any, transform: str | list[str] | dict[str, Any] | None) -> Any:
    """Apply a value transformation to a single field value.

    Named (string): one of NAMED_TRANSFORMS. A list applies them in order.

    Dict form (carried over from the original field-mappings service):
      value_map      : {"type":"value_map","mapping":{"countertop":"Kitchen Counter"}}
      regex_replace  : {"type":"regex_replace","pattern":"...","replacement":"..."}
      numeric_scale  : {"type":"numeric_scale","factor":10.7639}  (sqm→sqft)
      concatenate    : {"type":"concatenate","fields":["first","last"],"separator":" "}
                       — special; expects a dict of values
      split          : {"type":"split","separator":" "}
    """
    if transform is None or value is None:
        return value

    if isinstance(transform, str):
        return NAMED_TRANSFORMS[transform](value)

    if isinstance(transform, list):
        for name in transform:
            value = apply_transform(value, name)
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


def coerce(value: Any, ftype: FieldType | None, field_name: str) -> Any:
    """Coerce a resolved value to the shape its declared `type` implies.

    Failure is a TransformError (permanent): the payload cannot become
    the record the mapping promises, and retrying will not change that.
    """
    if value is None or ftype is None:
        return value
    try:
        match ftype:
            case "number":
                return float(value)
            case "checkbox":
                return _to_bool(value)
            case "multi_select" | "relation":
                items = value if isinstance(value, list) else [value]
                return [str(v) for v in items]
            case "date":
                return value.isoformat() if isinstance(value, date | datetime) else str(value)
            case _:
                # Dicts pass through so a mapping can hand an adapter a
                # fully-formed value (e.g. Monday {"label": ...}).
                return value if isinstance(value, dict) else str(value)
    except (TypeError, ValueError) as e:
        raise TransformError(
            f"field {field_name!r}: cannot coerce {value!r} to {ftype}: {e}"
        ) from e


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def _validate_path(path: str | None) -> str | None:
    if path is not None:
        try:
            compile_path(path)
        except MappingError as e:
            raise ValueError(str(e)) from e
    return path


class FieldSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str = Field(alias="from", min_length=1)
    to: str = Field(min_length=1)
    type: FieldType | None = None
    transform: str | list[str] | dict[str, Any] | None = None
    fallback: str | None = None
    default: Any = None
    required: bool = False

    @field_validator("from_", "fallback")
    @classmethod
    def _paths_compile(cls, v: str | None) -> str | None:
        return _validate_path(v)

    @field_validator("transform")
    @classmethod
    def _transform_is_known(cls, v: Any) -> Any:
        names = [v] if isinstance(v, str) else v if isinstance(v, list) else []
        for name in names:
            if name not in NAMED_TRANSFORMS:
                raise ValueError(
                    f"unknown transform {name!r}; known: {', '.join(sorted(NAMED_TRANSFORMS))}"
                )
        if isinstance(v, dict) and v.get("type") not in DICT_TRANSFORM_TYPES:
            raise ValueError(
                f"dict transform needs a 'type' in {sorted(DICT_TRANSFORM_TYPES)}"
            )
        return v


class LineItemsSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    enabled: bool = False
    from_: str = Field(alias="from", default="$.line_items")
    to: Literal["subitems"] = "subitems"
    name: str | None = "$.title"
    fields: list[FieldSpec] = Field(default_factory=list)

    @field_validator("from_", "name")
    @classmethod
    def _paths_compile(cls, v: str | None) -> str | None:
        return _validate_path(v)

    @model_validator(mode="after")
    def _unique_targets(self) -> LineItemsSpec:
        _check_unique_targets(self.fields, where="line_items.fields")
        return self


class Mapping(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    source: str = "shopify"
    topic: str = Field(min_length=1)
    destination: str = Field(min_length=1)
    key: str | None = None
    name: str | None = None
    fields: list[FieldSpec] = Field(min_length=1)
    line_items: LineItemsSpec = Field(default_factory=LineItemsSpec)

    @field_validator("name")
    @classmethod
    def _name_compiles(cls, v: str | None) -> str | None:
        return _validate_path(v)

    @model_validator(mode="after")
    def _consistent(self) -> Mapping:
        _check_unique_targets(self.fields, where="fields")
        if self.key is not None and self.key not in {f.to for f in self.fields}:
            raise ValueError(f"key {self.key!r} must be the `to` name of one of the fields")
        return self


def _check_unique_targets(fields: list[FieldSpec], *, where: str) -> None:
    seen: set[str] = set()
    for f in fields:
        if f.to in seen:
            raise ValueError(f"{where}: duplicate `to` target {f.to!r}")
        seen.add(f.to)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def resolve_mapping_path(path: str | None = None) -> Path:
    """Explicit path, else `mapping.yaml` in the CWD, else in the repo root."""
    candidate = Path(path or DEFAULT_MAPPING_FILENAME)
    if candidate.is_absolute() or candidate.exists():
        return candidate
    return REPO_ROOT / candidate


def load_mapping(path: str | None = None) -> Mapping:
    """Read + validate mapping.yaml. Raises MappingError with a readable message."""
    file = resolve_mapping_path(path)
    if not file.is_file():
        raise MappingError(f"mapping file not found: {file}")
    try:
        data = yaml.safe_load(file.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise MappingError(f"{file}: invalid YAML: {e}") from e
    if not isinstance(data, dict):
        raise MappingError(f"{file}: top level must be a mapping, got {type(data).__name__}")
    try:
        mapping = Mapping.model_validate(data)
    except ValidationError as e:
        lines = [
            f"  {'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in e.errors()
        ]
        raise MappingError(f"{file} is invalid:\n" + "\n".join(lines)) from e
    logger.info(
        "mapping loaded",
        extra={
            "path": str(file),
            "source": mapping.source,
            "topic": mapping.topic,
            "destination": mapping.destination,
            "fields": len(mapping.fields),
            "line_items": mapping.line_items.enabled,
        },
    )
    return mapping


# ---------------------------------------------------------------------------
# Record building
# ---------------------------------------------------------------------------


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == []


def _build_fields(specs: list[FieldSpec], payload: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for spec in specs:
        value = resolve_path(payload, spec.from_)
        if _empty(value) and spec.fallback:
            value = resolve_path(payload, spec.fallback)
        if _empty(value) and spec.default is not None:
            value = spec.default
        try:
            value = apply_transform(value, spec.transform)
        except (TypeError, ValueError) as e:
            raise TransformError(f"field {spec.to!r}: transform failed on {value!r}: {e}") from e
        value = coerce(value, spec.type, spec.to)
        if _empty(value):
            if spec.required:
                paths = spec.from_ + (f" / {spec.fallback}" if spec.fallback else "")
                raise TransformError(f"required field {spec.to!r} resolved to nothing ({paths})")
            continue  # adapters never receive empty fields
        out[spec.to] = value
    return out


def build_record(mapping: Mapping, payload: Any) -> dict[str, Any]:
    """Apply the mapping to one payload → adapter record."""
    record = _build_fields(mapping.fields, payload)

    if mapping.name:
        name = resolve_path(payload, mapping.name)
        if not _empty(name):
            record["_name"] = str(name)
    if mapping.key:
        record["_key"] = mapping.key

    li = mapping.line_items
    if li.enabled:
        items = resolve_path(payload, li.from_)
        if isinstance(items, dict):
            items = [items]
        subs: list[dict[str, Any]] = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            sub = _build_fields(li.fields, item)
            if li.name:
                sub_name = resolve_path(item, li.name)
                if not _empty(sub_name):
                    sub["_name"] = str(sub_name)
            subs.append(sub)
        record["_line_items"] = subs

    return record


def transform_for(mapping: Mapping) -> Transform:
    """The worker's transform: guards source/topic, then build_record()."""

    def transform(event: Event) -> dict[str, Any]:
        if event.source != mapping.source or event.topic != mapping.topic:
            raise TransformError(
                f"event is {event.source}/{event.topic} but mapping.yaml handles "
                f"{mapping.source}/{mapping.topic} — check the registered webhook topic"
            )
        if not isinstance(event.payload, dict):
            raise TransformError("payload is not a JSON object")
        return build_record(mapping, event.payload)

    return transform
