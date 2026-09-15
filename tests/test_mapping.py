"""mapping.yaml: loading/validation, path resolution, transforms, record building."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from app.mapping import (
    Mapping,
    MappingError,
    apply_transform,
    build_record,
    load_mapping,
    resolve_path,
    transform_for,
)
from app.models.event import Event
from app.pipeline import TransformError

FIXTURES = Path(__file__).parent / "fixtures"
ORDER = json.loads((FIXTURES / "shopify_order.json").read_text())


@pytest.fixture
def mapping() -> Mapping:
    return load_mapping(str(FIXTURES / "mapping.yaml"))


# ---------------------------------------------------------------------------
# The fixture → expected destination payload (the Phase 7 acceptance test)
# ---------------------------------------------------------------------------


def test_fixture_mapping_produces_expected_record(mapping):
    record = build_record(mapping, ORDER)
    assert record == {
        "_name": "#1001",
        "_key": "Order ID",
        "Order ID": "820982911946154508",
        "Total": 254.98,
        "Email": "jon@example.com",
        "Company": "Night's Watch LLC",  # fallback: shipping company is ""
        "Placed": "2026-09-15",
        "Payment": "paid",
        "Phone": "15551234567",  # dotted path + [strip, digits_only]
        "Tags": ["wholesale", "vip"],  # dict-form split → multi_select list
        "Currency": "US Dollar",  # dict-form value_map
        "Source": "Shopify",  # default
        "Paid?": True,  # checkbox coercion of "paid"
        # "Note" omitted: null, no fallback, no default
        "_line_items": [
            {"_name": "Quartz Slab", "Quantity": 2.0, "SKU": "QZ-CAL-3CM", "Variant": "Calacatta"},
            {"_name": "Edge Profile", "Quantity": 1.0, "SKU": "EDGE-BULL"},  # Variant null → omitted
        ],
    }


def test_fallback_is_only_used_when_from_is_empty(mapping):
    order = json.loads(json.dumps(ORDER))
    order["shipping_address"]["company"] = "Ship Co"
    assert build_record(mapping, order)["Company"] == "Ship Co"


def test_repo_root_mapping_yaml_is_valid():
    """The template's own mapping.yaml must always load — forks start from it."""
    m = load_mapping()
    assert m.source == "shopify"
    assert m.destination == "notion"
    assert m.key == "Order ID"
    record = build_record(m, ORDER)
    assert record["Order ID"] == "820982911946154508"
    assert record["Company"] == "Night's Watch LLC"
    assert len(record["_line_items"]) == 2


# ---------------------------------------------------------------------------
# transform_for: the worker-facing callable
# ---------------------------------------------------------------------------


def _event(**overrides) -> Event:
    base = dict(
        id=uuid4(), source="shopify", topic="orders/create", webhook_id="wh",
        payload=ORDER, received_at=datetime.now(UTC),
    )
    base.update(overrides)
    return Event(**base)


def test_transform_for_builds_record_from_event(mapping):
    assert transform_for(mapping)(_event())["Order ID"] == "820982911946154508"


def test_transform_rejects_mismatched_topic(mapping):
    with pytest.raises(TransformError, match="orders/updated.*mapping.yaml handles shopify/orders/create"):
        transform_for(mapping)(_event(topic="orders/updated"))


def test_transform_rejects_non_object_payload(mapping):
    with pytest.raises(TransformError, match="not a JSON object"):
        transform_for(mapping)(_event(payload=[1, 2]))


def test_required_field_missing_is_a_transform_error(mapping):
    order = {**ORDER, "id": None}
    with pytest.raises(TransformError, match="required field 'Order ID'"):
        build_record(mapping, order)


def test_uncoercible_number_is_a_transform_error(mapping):
    order = {**ORDER, "total_price": "free"}
    with pytest.raises(TransformError, match="'Total'"):
        build_record(mapping, order)


# ---------------------------------------------------------------------------
# Validation — every one of these must fail the deploy
# ---------------------------------------------------------------------------


def _write(tmp_path: Path, text: str) -> str:
    p = tmp_path / "mapping.yaml"
    p.write_text(text, encoding="utf-8")
    return str(p)


VALID = """
topic: orders/create
destination: slack
fields:
  - from: "$.id"
    to: "Order ID"
"""


def test_minimal_mapping_loads(tmp_path):
    m = load_mapping(_write(tmp_path, VALID))
    assert m.source == "shopify" and m.key is None and m.line_items.enabled is False


def test_missing_file():
    with pytest.raises(MappingError, match="not found"):
        load_mapping("/nowhere/mapping.yaml")


def test_invalid_yaml(tmp_path):
    with pytest.raises(MappingError, match="invalid YAML"):
        load_mapping(_write(tmp_path, "fields: [unclosed"))


def test_top_level_not_a_mapping(tmp_path):
    with pytest.raises(MappingError, match="top level must be a mapping"):
        load_mapping(_write(tmp_path, "- just\n- a list\n"))


@pytest.mark.parametrize(
    ("yaml_text", "message"),
    [
        (VALID.replace("topic: orders/create\n", ""), "topic"),
        (VALID.replace("fields:", "feilds:"), "fields"),
        (VALID + "    typo: 1\n", "typo"),
        (VALID + "key: Nope\n", "key 'Nope' must be the `to` name"),
        (VALID + "  - from: \"$.x\"\n    to: \"Order ID\"\n", "duplicate `to` target 'Order ID'"),
        (VALID + "    type: money\n", "type"),
        (VALID + "    transform: to_gold\n", "unknown transform 'to_gold'"),
        (VALID + "    transform: {mapping: {}}\n", "needs a 'type'"),
        (VALID.replace('"$.id"', '"$.id["'), "invalid JSONPath"),
        (VALID + "line_items:\n  enabled: true\n  to: rows\n", "line_items.to"),
        ("topic: t\ndestination: d\nfields: []\n", "at least 1"),
    ],
    ids=[
        "missing-topic", "misspelled-fields", "unknown-key", "key-not-a-field",
        "duplicate-to", "bad-type", "unknown-transform", "dict-transform-no-type",
        "bad-jsonpath", "bad-line-items-to", "no-fields",
    ],
)
def test_invalid_mappings_fail_loudly(tmp_path, yaml_text, message):
    with pytest.raises(MappingError, match=message):
        load_mapping(_write(tmp_path, yaml_text))


# ---------------------------------------------------------------------------
# resolve_path
# ---------------------------------------------------------------------------


def test_resolve_jsonpath_single_and_multi():
    assert resolve_path(ORDER, "$.customer.email") == "jon@example.com"
    assert resolve_path(ORDER, "$.line_items[*].sku") == ["QZ-CAL-3CM", "EDGE-BULL"]
    assert resolve_path(ORDER, "$.line_items[0].sku") == "QZ-CAL-3CM"
    assert resolve_path(ORDER, "$.nope.deeper") is None


def test_resolve_dotted():
    assert resolve_path(ORDER, "customer.email") == "jon@example.com"
    assert resolve_path(ORDER, "line_items.1.sku") == "EDGE-BULL"
    assert resolve_path(ORDER, "line_items.9.sku") is None
    assert resolve_path(ORDER, "customer.phone.digits") is None
    assert resolve_path(ORDER, "nope") is None


# ---------------------------------------------------------------------------
# apply_transform — dict form carried over from field_mappings.py, plus named
# ---------------------------------------------------------------------------


def test_value_map_translates_known_values():
    t = {"type": "value_map", "mapping": {"consult": "Consultation"}}
    assert apply_transform("consult", t) == "Consultation"


def test_value_map_passes_through_unknown_values():
    t = {"type": "value_map", "mapping": {"consult": "Consultation"}}
    assert apply_transform("install", t) == "install"


def test_numeric_scale_multiplies():
    assert apply_transform(10, {"type": "numeric_scale", "factor": 10.7639}) == 107.639


def test_numeric_scale_handles_non_numeric_gracefully():
    assert apply_transform("not a number", {"type": "numeric_scale", "factor": 2}) == "not a number"


def test_regex_replace():
    t = {"type": "regex_replace", "pattern": r"\D", "replacement": ""}
    assert apply_transform("(555) 123-4567", t) == "5551234567"


def test_concatenate_joins_fields():
    t = {"type": "concatenate", "fields": ["first", "last"], "separator": " "}
    assert apply_transform({"first": "Jane", "last": "Doe"}, t) == "Jane Doe"


def test_split_separates_on_token():
    assert apply_transform("a,b,c", {"type": "split", "separator": ","}) == ["a", "b", "c"]


def test_none_value_passes_through():
    assert apply_transform(None, {"type": "value_map", "mapping": {}}) is None
    assert apply_transform(None, "to_decimal") is None


def test_no_transform_passes_value_through():
    assert apply_transform("hello", None) == "hello"


def test_unknown_dict_transform_type_passes_value_through():
    assert apply_transform("hello", {"type": "nonsense"}) == "hello"


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("to_decimal", "254.98", 254.98),
        ("to_int", "3.0", 3),
        ("to_str", 42, "42"),
        ("strip", "  x ", "x"),
        ("upper", "ab", "AB"),
        ("lower", "AB", "ab"),
        ("title_case", "jon snow", "Jon Snow"),
        ("digits_only", "+1 (555) 123", "1555123"),
        ("to_bool", "yes", True),
        ("to_bool", 0, False),
        ("to_date", "2026-09-15T10:42:17-07:00", "2026-09-15"),
        ("to_date", "2026-09-15T17:42:17Z", "2026-09-15"),
        ("join", ["a", "b"], "a, b"),
        ("first", ["a", "b"], "a"),
        ("first", "solo", "solo"),
    ],
)
def test_named_transforms(name, value, expected):
    assert apply_transform(value, name) == expected


def test_named_transforms_chain_in_order():
    assert apply_transform("  Jon Snow ", ["strip", "upper"]) == "JON SNOW"
