"""Field-mapping transform tests — pure, no DB."""

from __future__ import annotations

from app.services.field_mappings import apply_inverse_transform, apply_transform


def test_value_map_translates_known_values():
    t = {"type": "value_map", "mapping": {"consult": "Consultation"}}
    assert apply_transform("consult", t) == "Consultation"


def test_value_map_passes_through_unknown_values():
    t = {"type": "value_map", "mapping": {"consult": "Consultation"}}
    assert apply_transform("install", t) == "install"


def test_value_map_inverse():
    t = {"type": "value_map", "mapping": {"consult": "Consultation"}}
    assert apply_inverse_transform("Consultation", t) == "consult"


def test_numeric_scale_multiplies():
    t = {"type": "numeric_scale", "factor": 10.7639}
    assert apply_transform(10, t) == 107.639


def test_numeric_scale_inverse_divides():
    t = {"type": "numeric_scale", "factor": 10.7639}
    out = apply_inverse_transform(107.639, t)
    assert abs(out - 10) < 1e-6


def test_numeric_scale_handles_non_numeric_gracefully():
    t = {"type": "numeric_scale", "factor": 2}
    assert apply_transform("not a number", t) == "not a number"


def test_regex_replace():
    t = {"type": "regex_replace", "pattern": r"\D", "replacement": ""}
    assert apply_transform("(555) 123-4567", t) == "5551234567"


def test_concatenate_joins_fields():
    t = {"type": "concatenate", "fields": ["first", "last"], "separator": " "}
    assert apply_transform({"first": "Jane", "last": "Doe"}, t) == "Jane Doe"


def test_split_separates_on_token():
    t = {"type": "split", "separator": ","}
    assert apply_transform("a,b,c", t) == ["a", "b", "c"]


def test_none_value_passes_through():
    assert apply_transform(None, {"type": "value_map", "mapping": {}}) is None


def test_no_transform_passes_value_through():
    assert apply_transform("hello", None) == "hello"


def test_unknown_transform_type_passes_value_through():
    assert apply_transform("hello", {"type": "nonsense"}) == "hello"
