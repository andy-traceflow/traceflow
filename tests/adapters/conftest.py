"""Shared adapter-test plumbing: a recording MockTransport so no test touches the network."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.adapters.registry import reset_registry

Handler = Callable[[httpx.Request], httpx.Response]


class Recorder:
    """Wraps a handler, keeping every request (with parsed JSON body) for assertions."""

    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def bodies(self) -> list[Any]:
        out = []
        for r in self.requests:
            try:
                out.append(json.loads(r.content) if r.content else None)
            except json.JSONDecodeError:
                out.append(r.content.decode())
        return out

    def last_body(self) -> Any:
        return self.bodies()[-1]


def body_of(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content) if request.content else {}


def json_response(status: int, payload: Any, **headers: str) -> httpx.Response:
    return httpx.Response(status, json=payload, headers=headers)


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry()
    yield
    reset_registry()
