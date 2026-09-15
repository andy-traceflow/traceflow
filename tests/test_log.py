"""JSON log lines carry the structured fields from `extra=` at the top level."""

from __future__ import annotations

import json
import logging

from app.log import JsonFormatter, configure_logging


def _record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="app.worker", level=logging.INFO, pathname=__file__, lineno=1,
        msg="event %s", args=("delivered",), exc_info=None,
    )
    for k, v in extra.items():
        setattr(record, k, v)
    return record


def test_transition_line_is_one_json_object_with_required_fields():
    line = JsonFormatter().format(
        _record(
            event_id="e-1", webhook_id="wh-1", status="delivered", attempts=1,
            duration_ms=412, external_id="page-9",
        )
    )
    data = json.loads(line)
    assert "\n" not in line
    assert data["message"] == "event delivered"
    assert data["level"] == "INFO"
    assert data["logger"] == "app.worker"
    assert data["ts"].endswith("+00:00")
    assert {k: data[k] for k in ("event_id", "webhook_id", "status", "attempts", "duration_ms")} == {
        "event_id": "e-1", "webhook_id": "wh-1", "status": "delivered", "attempts": 1, "duration_ms": 412,
    }
    assert data["external_id"] == "page-9"
    # stdlib internals are not leaked
    assert "args" not in data and "msg" not in data and "levelno" not in data


def test_non_json_values_are_stringified():
    from uuid import UUID

    data = json.loads(JsonFormatter().format(_record(event_id=UUID(int=1))))
    assert data["event_id"] == "00000000-0000-0000-0000-000000000001"


def test_exception_is_included():
    try:
        raise ValueError("bad")
    except ValueError:
        import sys

        record = _record()
        record.exc_info = sys.exc_info()
    data = json.loads(JsonFormatter().format(record))
    assert "ValueError: bad" in data["exception"]


def test_configure_logging_routes_uvicorn_through_root():
    configure_logging("DEBUG", "json")
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert len(root.handlers) == 1 and isinstance(root.handlers[0].formatter, JsonFormatter)
    assert logging.getLogger("uvicorn.access").propagate is True
    assert logging.getLogger("uvicorn.access").handlers == []

    configure_logging("INFO", "text")
    assert not isinstance(logging.getLogger().handlers[0].formatter, JsonFormatter)
