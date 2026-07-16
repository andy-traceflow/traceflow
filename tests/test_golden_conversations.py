"""Golden-conversation fixtures — deterministic qualification-flow outcomes.

Each fixture in tests/fixtures/conversations/ pins a captured state to its
expected hard-gate result and termination reason. This locks the deterministic
loop (services/qualification) against regression. The routing-side scenarios
(returning-caller routing, stale-open-lead resume, blocked-contact zero AI spend)
are covered in tests/services/test_classification.py and tests/test_twilio.py.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from app.models.client_config import ClientConfig
from app.models.qualification import default_schema
from app.services.qualification import (
    TerminationReason,
    check_hard_gates,
    should_terminate,
)

_FIXTURES = sorted((Path(__file__).parent / "fixtures" / "conversations").glob("*.json"))


def _config(**overrides: Any) -> ClientConfig:
    base = {"client_id": uuid4(), "ai_period_resets_at": datetime.now(UTC), "updated_at": datetime.now(UTC)}
    base.update(overrides)
    return ClientConfig(**base)


@pytest.mark.parametrize("path", _FIXTURES, ids=lambda p: p.stem)
def test_golden_conversation(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    schema = default_schema()
    config = _config(
        service_area_zips=data.get("service_area_zips", []),
        vip_value_threshold=data.get("vip_value_threshold"),
    )
    state = data["state"]
    expect = data["expect"]

    gate = check_hard_gates(schema, state, config)
    assert (gate.reason if gate else None) == expect["gate"]

    if gate is not None:
        termination: TerminationReason | None = TerminationReason.disqualified
    else:
        termination = should_terminate(schema, state, data["turn_count"])
    assert (termination.value if termination else None) == expect["termination"]
