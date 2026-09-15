"""Google Sheets destination — append one row per record.

Columns are resolved by the sheet's header row (row 1): each record key
is matched to a header cell and the row is written in header order,
blank where the record has no value for a column. The header is fetched
once and refreshed if a record names a column the cache does not know.
Append-only: `_key` and `_line_items` are ignored with a warning.

Auth is a Google service account. The JSON key file's contents go in
GOOGLE_SERVICE_ACCOUNT_JSON; the sheet must be shared with the service
account's email (Editor). A short-lived access token is minted from an
RS256-signed JWT assertion and cached until shortly before it expires.

Env: GOOGLE_SERVICE_ACCOUNT_JSON, GOOGLE_SHEET_ID (from the sheet URL),
GOOGLE_SHEET_TAB (default `Sheet1`).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote

import httpx
import jwt

from app.adapters.base import AdapterConfigError, optional_env, require_env, split_record
from app.pipeline import PermanentDeliveryError

logger = logging.getLogger(__name__)

SHEETS_API_BASE = "https://sheets.googleapis.com/v4/spreadsheets"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/spreadsheets"
DEFAULT_TIMEOUT = 30.0
TOKEN_LIFETIME_SECONDS = 3600
TOKEN_REFRESH_MARGIN_SECONDS = 60

TokenProvider = Callable[[], Awaitable[str]]


class SheetsAdapter:
    name = "sheets"

    def __init__(
        self,
        *,
        service_account_json: str | None = None,
        spreadsheet_id: str | None = None,
        sheet_name: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        token_provider: TokenProvider | None = None,
    ) -> None:
        raw = service_account_json or require_env("GOOGLE_SERVICE_ACCOUNT_JSON")
        try:
            creds = json.loads(raw)
            self._client_email: str = creds["client_email"]
            self._private_key: str = creds["private_key"]
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            raise AdapterConfigError(
                "GOOGLE_SERVICE_ACCOUNT_JSON must be the service-account key file's JSON "
                "(with client_email and private_key)"
            ) from e
        self.spreadsheet_id = spreadsheet_id or require_env("GOOGLE_SHEET_ID")
        self.sheet_name = sheet_name or optional_env("GOOGLE_SHEET_TAB", "Sheet1")
        self._token_provider = token_provider
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._headers: list[str] | None = None
        self._warned_key = False
        self._warned_line_items = False
        self._client = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, transport=transport)

    # ------------------------------------------------------------------
    # Destination interface
    # ------------------------------------------------------------------

    async def upsert_record(self, record: dict[str, Any]) -> str:
        fields, meta = split_record(record)
        if meta.get("_key") and not self._warned_key:
            logger.warning("sheets: append-only destination — _key is ignored")
            self._warned_key = True
        if meta.get("_line_items") and not self._warned_line_items:
            logger.warning("sheets: _line_items are not supported for this destination — ignored")
            self._warned_line_items = True

        headers = await self._header_row()
        if any(name not in headers for name in fields):
            headers = await self._header_row(refresh=True)
        for name in fields:
            if name not in headers:
                logger.warning("sheets: no header column %r — field skipped", name)

        row = [self._cell(fields.get(h)) for h in headers]
        data = await self._request(
            "POST",
            f"{SHEETS_API_BASE}/{self.spreadsheet_id}/values/{self._range('A1')}:append",
            params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
            json={"values": [row]},
        )
        updated = (data.get("updates") or {}).get("updatedRange")
        if not updated:
            raise PermanentDeliveryError(f"sheets: append returned no updatedRange: {data}")
        return str(updated)

    async def health_check(self) -> bool:
        try:
            await self._request(
                "GET",
                f"{SHEETS_API_BASE}/{self.spreadsheet_id}",
                params={"fields": "spreadsheetId"},
            )
            return True
        except Exception as e:
            logger.warning("sheets health_check failed", exc_info=e)
            return False

    # ------------------------------------------------------------------
    # Header row
    # ------------------------------------------------------------------

    def _range(self, a1: str) -> str:
        return quote(f"'{self.sheet_name}'!{a1}", safe="")

    async def _header_row(self, *, refresh: bool = False) -> list[str]:
        if self._headers is None or refresh:
            data = await self._request(
                "GET", f"{SHEETS_API_BASE}/{self.spreadsheet_id}/values/{self._range('1:1')}"
            )
            values = data.get("values") or []
            headers = [str(h).strip() for h in (values[0] if values else [])]
            if not any(headers):
                raise PermanentDeliveryError(
                    f"sheets: tab {self.sheet_name!r} has no header row — add column names in row 1"
                )
            self._headers = headers
            logger.info("sheets header loaded", extra={"columns": len(headers)})
        return self._headers

    @staticmethod
    def _cell(value: Any) -> Any:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, list | tuple):
            return ", ".join(str(v) for v in value)
        return value  # numbers stay numbers

    # ------------------------------------------------------------------
    # Auth + HTTP
    # ------------------------------------------------------------------

    async def _access_token(self) -> str:
        if self._token_provider is not None:
            return await self._token_provider()
        now = time.time()
        if self._token and now < self._token_expires_at:
            return self._token
        assertion = jwt.encode(
            {
                "iss": self._client_email,
                "scope": SCOPE,
                "aud": TOKEN_URL,
                "iat": int(now),
                "exp": int(now) + TOKEN_LIFETIME_SECONDS,
            },
            self._private_key,
            algorithm="RS256",
        )
        resp = await self._client.post(
            TOKEN_URL,
            data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion},
        )
        resp.raise_for_status()
        body = resp.json()
        self._token = str(body["access_token"])
        self._token_expires_at = (
            now + float(body.get("expires_in", TOKEN_LIFETIME_SECONDS)) - TOKEN_REFRESH_MARGIN_SECONDS
        )
        return self._token

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        token = await self._access_token()
        resp = await self._client.request(
            method, url, params=params, json=json, headers={"Authorization": f"Bearer {token}"}
        )
        resp.raise_for_status()
        return resp.json() if resp.content else {}
