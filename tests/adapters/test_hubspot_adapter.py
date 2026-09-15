"""HubSpot adapter: create, _key search → PATCH, 409 conflict → PATCH, serialization."""

from __future__ import annotations

import httpx
import pytest

from app.adapters.hubspot import HubSpotAdapter
from app.pipeline import PermanentDeliveryError
from tests.adapters.conftest import Recorder, json_response


def hubspot_handler(*, search_hit: str | None = None, conflict_id: str | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/search"):
            results = [{"id": search_hit}] if search_hit else []
            return json_response(200, {"results": results})
        if request.method == "POST" and path == "/crm/v3/objects/contacts":
            if conflict_id:
                return json_response(
                    409,
                    {"status": "error", "message": f"Contact already exists. Existing ID: {conflict_id}"},
                )
            return json_response(201, {"id": "501"})
        if request.method == "PATCH":
            return json_response(200, {"id": path.rsplit("/", 1)[-1]})
        if request.method == "GET":
            return json_response(200, {"results": []})
        return json_response(404, {})

    return handler


def _adapter(rec: Recorder) -> HubSpotAdapter:
    return HubSpotAdapter(access_token="tok", transport=rec.transport)


RECORD = {
    "_name": "Jon Snow",
    "email": "jon@example.com",
    "firstname": "Jon",
    "order_total": 254.98,
    "empty": "",
    "missing": None,
    "tags": ["vip", "wholesale"],
    "opted_in": True,
}


async def test_create_sends_flat_properties_and_returns_id():
    rec = Recorder(hubspot_handler())
    assert await _adapter(rec).upsert_record(RECORD) == "501"

    create = rec.requests[-1]
    assert create.method == "POST"
    assert create.headers["Authorization"] == "Bearer tok"
    assert rec.last_body() == {
        "properties": {
            "email": "jon@example.com",
            "firstname": "Jon",
            "order_total": 254.98,
            "tags": "vip;wholesale",
            "opted_in": "true",
        }
    }


async def test_key_search_hit_patches_existing():
    rec = Recorder(hubspot_handler(search_hit="77"))
    assert await _adapter(rec).upsert_record({**RECORD, "_key": "email"}) == "77"

    methods = [(r.method, r.url.path) for r in rec.requests]
    assert methods == [
        ("POST", "/crm/v3/objects/contacts/search"),
        ("PATCH", "/crm/v3/objects/contacts/77"),
    ]
    search = rec.bodies()[0]
    assert search["filterGroups"][0]["filters"][0] == {
        "propertyName": "email", "operator": "EQ", "value": "jon@example.com"
    }


async def test_409_conflict_resolves_to_patch():
    rec = Recorder(hubspot_handler(conflict_id="88"))
    assert await _adapter(rec).upsert_record(RECORD) == "88"
    assert [(r.method, r.url.path) for r in rec.requests] == [
        ("POST", "/crm/v3/objects/contacts"),
        ("PATCH", "/crm/v3/objects/contacts/88"),
    ]


async def test_other_4xx_propagates():
    rec = Recorder(lambda r: json_response(400, {"message": "bad property"}))
    with pytest.raises(httpx.HTTPStatusError) as exc:
        await _adapter(rec).upsert_record(RECORD)
    assert exc.value.response.status_code == 400


async def test_empty_record_is_permanent():
    rec = Recorder(hubspot_handler())
    with pytest.raises(PermanentDeliveryError, match="no non-empty"):
        await _adapter(rec).upsert_record({"a": None, "b": "", "_name": "x"})
    assert rec.requests == []


async def test_object_type_from_env(monkeypatch):
    monkeypatch.setenv("HUBSPOT_OBJECT", "deals")
    rec = Recorder(hubspot_handler())
    adapter = HubSpotAdapter(access_token="tok", transport=rec.transport)
    try:
        await adapter.upsert_record({"dealname": "Big"})
    except httpx.HTTPStatusError:
        pass  # handler only knows /contacts; the path is what we're checking
    assert rec.requests[0].url.path == "/crm/v3/objects/deals"


async def test_health_check():
    assert await _adapter(Recorder(hubspot_handler())).health_check() is True
    assert await _adapter(Recorder(lambda r: json_response(401, {}))).health_check() is False
