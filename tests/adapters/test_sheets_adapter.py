"""Sheets adapter: header-row column resolution, append, service-account token minting."""

from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.adapters.base import AdapterConfigError
from app.adapters.sheets import SheetsAdapter
from app.pipeline import PermanentDeliveryError
from tests.adapters.conftest import Recorder, json_response

HEADERS = ["Order ID", "Customer", "Total", "Placed"]


def sheets_handler(*, header: list[str] | None = HEADERS):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path.endswith("/values/'Orders'!1:1"):
            return json_response(200, {"values": [header]} if header else {})
        if request.method == "POST" and path.endswith(":append"):
            return json_response(200, {"updates": {"updatedRange": "Orders!A5:D5"}})
        if request.method == "GET" and path == "/v4/spreadsheets/sheet-1":
            return json_response(200, {"spreadsheetId": "sheet-1"})
        return json_response(404, {"path": path})

    return handler


async def _fixed_token() -> str:
    return "fixed-token"


def _adapter(rec: Recorder) -> SheetsAdapter:
    return SheetsAdapter(
        service_account_json='{"client_email": "svc@proj.iam", "private_key": "unused"}',
        spreadsheet_id="sheet-1",
        sheet_name="Orders",
        transport=rec.transport,
        token_provider=_fixed_token,
    )


RECORD = {
    "_name": "Order #1001",
    "_key": "Order ID",
    "Order ID": "1001",
    "Customer": "Jon Snow",
    "Total": 254.98,
    "Unknown": "skipped",
    "_line_items": [{"x": 1}],
}


async def test_appends_row_in_header_order_with_blanks_for_missing():
    rec = Recorder(sheets_handler())
    assert await _adapter(rec).upsert_record(RECORD) == "Orders!A5:D5"

    append = rec.requests[-1]
    assert append.headers["Authorization"] == "Bearer fixed-token"
    assert append.url.path.endswith("/values/'Orders'!A1:append")
    assert dict(append.url.params) == {
        "valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"
    }
    assert rec.last_body() == {"values": [["1001", "Jon Snow", 254.98, ""]]}


async def test_header_is_cached_and_refreshed_on_unknown_column():
    rec = Recorder(sheets_handler())
    adapter = _adapter(rec)
    await adapter.upsert_record({"Order ID": "1"})
    await adapter.upsert_record({"Order ID": "2"})
    assert len([r for r in rec.requests if r.url.path.endswith("!1:1")]) == 1
    await adapter.upsert_record({"Order ID": "3", "New Col": "x"})
    assert len([r for r in rec.requests if r.url.path.endswith("!1:1")]) == 2


async def test_missing_header_row_is_permanent():
    rec = Recorder(sheets_handler(header=None))
    with pytest.raises(PermanentDeliveryError, match="no header row"):
        await _adapter(rec).upsert_record(RECORD)


def test_cell_serialization():
    c = SheetsAdapter._cell
    assert c(None) == ""
    assert c(True) == "TRUE"
    assert c(["a", "b"]) == "a, b"
    assert c(3) == 3


def test_bad_service_account_json_fails_construction():
    with pytest.raises(AdapterConfigError, match="GOOGLE_SERVICE_ACCOUNT_JSON"):
        SheetsAdapter(service_account_json="not json", spreadsheet_id="s")
    with pytest.raises(AdapterConfigError):
        SheetsAdapter(service_account_json='{"client_email": "x"}', spreadsheet_id="s")


async def test_mints_and_caches_a_service_account_token():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    base = sheets_handler()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
            claims = jwt.decode(
                form["assertion"][0], public_pem, algorithms=["RS256"], audience="https://oauth2.googleapis.com/token"
            )
            assert claims["iss"] == "svc@proj.iam"
            assert claims["scope"] == "https://www.googleapis.com/auth/spreadsheets"
            return json_response(200, {"access_token": "minted", "expires_in": 3600})
        return base(request)

    rec = Recorder(handler)
    adapter = SheetsAdapter(
        service_account_json=json.dumps({"client_email": "svc@proj.iam", "private_key": pem}),
        spreadsheet_id="sheet-1",
        sheet_name="Orders",
        transport=rec.transport,
    )
    await adapter.upsert_record({"Order ID": "1"})
    await adapter.upsert_record({"Order ID": "2"})

    token_calls = [r for r in rec.requests if r.url.host == "oauth2.googleapis.com"]
    assert len(token_calls) == 1  # cached for the second call
    api_calls = [r for r in rec.requests if r.url.host == "sheets.googleapis.com"]
    assert all(r.headers["Authorization"] == "Bearer minted" for r in api_calls)


async def test_health_check():
    assert await _adapter(Recorder(sheets_handler())).health_check() is True
    assert await _adapter(Recorder(lambda r: json_response(403, {}))).health_check() is False
