"""Phone normalization — E.164 in every direction, None on garbage."""

from __future__ import annotations

import pytest

from app.services.phone import normalize


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Already E.164 → unchanged (formatting stripped).
        ("+15551112222", "+15551112222"),
        ("+1 (702) 517-8074", "+17025178074"),
        ("+44 20 7946 0958", "+442079460958"),
        # NANP national forms → +1.
        ("(702) 517-8074", "+17025178074"),
        ("702-517-8074", "+17025178074"),
        ("702.517.8074", "+17025178074"),
        ("7025178074", "+17025178074"),
        # 11-digit with leading country code.
        ("17025178074", "+17025178074"),
        ("1 702 517 8074", "+17025178074"),
    ],
)
def test_normalizes_to_e164(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


def test_idempotent() -> None:
    once = normalize("(702) 517-8074")
    assert once is not None
    assert normalize(once) == once


@pytest.mark.parametrize("raw", [None, "", "   ", "abc", "not a phone", "+", "12345"])
def test_unparseable_returns_none(raw: str | None) -> None:
    assert normalize(raw) is None


def test_wrong_nanp_length_returns_none() -> None:
    # 9 digits is neither a 10-digit national nor an 11-digit CC-prefixed number.
    assert normalize("702517807") is None


def test_default_region_drives_country_code() -> None:
    # Same 10-digit national number resolves to a different E.164 depending on
    # the client's default region — the country code is not hardcoded.
    assert normalize("2079460958", "GB") == "+442079460958"
    assert normalize("2079460958", "US") == "+12079460958"


def test_region_is_case_insensitive() -> None:
    assert normalize("7025178074", "us") == "+17025178074"
