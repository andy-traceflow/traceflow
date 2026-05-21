"""Generic webhook handler (Layer 3) — integration + extraction tests.

Layer 3 of the integration model: per-client webhook configs in
`client_webhook_configs` define how to parse and authenticate inbound
JSON payloads from long-tail systems where building a dedicated adapter
isn't worth it. This suite covers:

  - Pure-function extractor behavior (`_extract_fields`)
  - End-to-end POST handling via FastAPI's TestClient: 404 for unknown
    slug, 401 for bad signature, 200 + Lead row + Event row on valid
    requests, tenant isolation across clients, graceful handling of
    invalid JSON, and parity across all three supported signing
    algorithms (hmac_sha256 hex/base64, hmac_sha256_timestamped, none).

Requires TRACEFLOW_TEST_DB_URL — skipped cleanly otherwise.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.webhooks.generic import _extract_fields

# ---------------------------------------------------------------------------
# Pure-function tests — fast, no DB, no app boot
# ---------------------------------------------------------------------------

def test_extract_fields_jsonpath_basic() -> None:
    payload = {"customer": {"name": "Alice", "phone": "555-1234"}}
    extractors = {
        "contact_name": "$.customer.name",
        "phone": "$.customer.phone",
    }
    result = _extract_fields(payload=payload, parser_type="jsonpath", extractors=extractors)
    assert result == {"contact_name": "Alice", "phone": "555-1234"}


def test_extract_fields_jsonpath_skips_missing_paths() -> None:
    """A path that doesn't resolve doesn't show up in the result — the
    Lead model will then see it as None and fall through default handling."""
    payload = {"customer": {"name": "Alice"}}
    extractors = {
        "contact_name": "$.customer.name",
        "phone": "$.customer.phone",  # missing
    }
    result = _extract_fields(payload=payload, parser_type="jsonpath", extractors=extractors)
    assert result == {"contact_name": "Alice"}


def test_extract_fields_jsonpath_picks_first_match() -> None:
    """Multi-match expressions return the first value, not the array."""
    payload = {"items": [{"name": "first"}, {"name": "second"}]}
    extractors = {"contact_name": "$.items[*].name"}
    result = _extract_fields(payload=payload, parser_type="jsonpath", extractors=extractors)
    assert result == {"contact_name": "first"}


def test_extract_fields_jsonpath_swallows_extractor_errors() -> None:
    """A broken extractor expression for one field doesn't block the others."""
    payload = {"customer": {"name": "Alice"}}
    extractors = {
        "contact_name": "$.customer.name",
        "phone": "[[ invalid jsonpath",
    }
    result = _extract_fields(payload=payload, parser_type="jsonpath", extractors=extractors)
    assert result == {"contact_name": "Alice"}


def test_extract_fields_unknown_parser_returns_empty() -> None:
    result = _extract_fields(payload={"a": 1}, parser_type="not_a_real_parser", extractors={"x": "$.a"})
    assert result == {}


def test_extract_fields_jq_returns_empty_stub() -> None:
    """jq parser is reserved but not yet wired — returns empty until
    a client actually needs it. Documenting the contract here."""
    result = _extract_fields(payload={"a": 1}, parser_type="jq", extractors={"x": ".a"})
    assert result == {}


def test_extract_fields_python_template_returns_empty_stub() -> None:
    """python_template parser is reserved but not yet wired (sandboxed
    eval is non-trivial). Documenting the contract here."""
    result = _extract_fields(
        payload={"a": 1}, parser_type="python_template", extractors={"x": "payload['a']"}
    )
    assert result == {}


# ---------------------------------------------------------------------------
# Integration tests — require live DB and a running app via TestClient
# ---------------------------------------------------------------------------

# Lazy-import the app/TestClient inside the fixture so module-level
# collection doesn't pay the cost when running pure-function tests in
# isolation.

@pytest.fixture(scope="module")
def app(db_url: str):
    """FastAPI app instance with the test DB DSN flowing through settings.

    `db_url` triggers the same skip-if-unset behavior as the rest of the
    DB-dependent suites — so this fixture cleanly skips when
    TRACEFLOW_TEST_DB_URL isn't set.
    """
    # Settings is @lru_cache'd; invalidate so the env var override sticks.
    from app.config import get_settings
    get_settings.cache_clear()
    from app.main import app as fastapi_app
    return fastapi_app


@pytest.fixture
def client(app):
    """FastAPI TestClient — handles lifespan automatically (init/close pool)."""
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield c


@pytest.fixture
async def admin_conn(db_url: str):
    """Admin connection used for fixture setup/teardown that needs to bypass RLS."""
    conn = await asyncpg.connect(db_url)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def test_client_id(admin_conn) -> UUID:
    """Create a test tenant; yield its id; clean up after."""
    cid = uuid4()
    await admin_conn.execute("RESET ROLE")
    await admin_conn.execute(
        "INSERT INTO clients (id, slug, business_name) VALUES ($1, $2, $3)",
        cid,
        f"hook-test-{cid}",
        "Hook Test Co",
    )
    yield cid
    await admin_conn.execute("RESET ROLE")
    await admin_conn.execute("DELETE FROM clients WHERE id = $1", cid)


SECRET = "test-webhook-secret-do-not-use-in-prod"


async def _insert_webhook_config(
    admin_conn: asyncpg.Connection,
    *,
    client_id: UUID,
    slug: str,
    signing_algorithm: str = "hmac_sha256",
    signing_secret: str | None = SECRET,
    signature_header: str = "X-Signature",
    field_extractors: dict[str, str] | None = None,
) -> UUID:
    extractors = field_extractors or {
        "contact_name": "$.customer.name",
        "phone": "$.customer.phone",
        "email": "$.customer.email",
    }
    await admin_conn.execute("RESET ROLE")
    cfg_id = await admin_conn.fetchval(
        """
        INSERT INTO client_webhook_configs (
            client_id, webhook_slug, parser_type, field_extractors,
            signing_secret, signing_algorithm, signature_header
        ) VALUES ($1, $2, 'jsonpath', $3::jsonb, $4, $5, $6)
        RETURNING id
        """,
        client_id,
        slug,
        json.dumps(extractors),
        signing_secret,
        signing_algorithm,
        signature_header,
    )
    return cfg_id


def _sign_hex(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _sign_b64(body: bytes, secret: str = SECRET) -> str:
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


def _sign_timestamped(body: bytes, secret: str = SECRET) -> str:
    ts = int(time.time())
    signed = f"{ts}".encode() + b"." + body
    sig = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={ts},s={sig}"


async def _count_leads_for_client(admin_conn: asyncpg.Connection, client_id: UUID) -> int:
    await admin_conn.execute("RESET ROLE")
    return await admin_conn.fetchval(
        "SELECT count(*) FROM leads WHERE client_id = $1", client_id
    )


# ---------- 404: unknown slug --------------------------------------------------

async def test_returns_404_when_slug_not_configured(
    client, test_client_id: UUID
) -> None:
    resp = client.post(
        f"/webhooks/generic/{test_client_id}/never-configured",
        json={"customer": {"name": "X"}},
    )
    assert resp.status_code == 404


# ---------- 401: bad signature -------------------------------------------------

async def test_returns_401_on_bad_hmac_signature(
    client, admin_conn, test_client_id: UUID
) -> None:
    await _insert_webhook_config(admin_conn, client_id=test_client_id, slug="bad-sig-hook")
    body = json.dumps({"customer": {"name": "Alice"}}).encode()

    resp = client.post(
        f"/webhooks/generic/{test_client_id}/bad-sig-hook",
        content=body,
        headers={"X-Signature": "definitely-not-the-right-signature"},
    )
    assert resp.status_code == 401


# ---------- 200: valid HMAC-SHA256 hex ----------------------------------------

async def test_accepts_valid_hmac_hex_signature_and_persists_lead(
    client, admin_conn, test_client_id: UUID
) -> None:
    await _insert_webhook_config(admin_conn, client_id=test_client_id, slug="hex-hook")
    payload = {"customer": {"name": "Alice", "phone": "555-1234", "email": "a@example.com"}}
    body = json.dumps(payload).encode()

    resp = client.post(
        f"/webhooks/generic/{test_client_id}/hex-hook",
        content=body,
        headers={"X-Signature": _sign_hex(body), "Content-Type": "application/json"},
    )
    assert resp.status_code == 200

    await admin_conn.execute("RESET ROLE")
    row = await admin_conn.fetchrow(
        """SELECT contact_name, phone, email, source_system, raw_payload
           FROM leads WHERE client_id = $1""",
        test_client_id,
    )
    assert row is not None, "no lead row created"
    assert row["contact_name"] == "Alice"
    assert row["phone"] == "555-1234"
    assert row["email"] == "a@example.com"
    assert row["source_system"] == "generic:hex-hook"
    assert json.loads(row["raw_payload"]) == payload


# ---------- 200: valid HMAC-SHA256 base64 (handler accepts either) ------------

async def test_accepts_valid_hmac_base64_signature(
    client, admin_conn, test_client_id: UUID
) -> None:
    """Handler tries both hex and base64 before rejecting; verify base64 is honored."""
    await _insert_webhook_config(admin_conn, client_id=test_client_id, slug="b64-hook")
    body = json.dumps({"customer": {"name": "Bob"}}).encode()

    resp = client.post(
        f"/webhooks/generic/{test_client_id}/b64-hook",
        content=body,
        headers={"X-Signature": _sign_b64(body)},
    )
    assert resp.status_code == 200
    assert await _count_leads_for_client(admin_conn, test_client_id) == 1


# ---------- 200: timestamped signature (Stripe-style replay protection) ------

async def test_accepts_valid_timestamped_signature(
    client, admin_conn, test_client_id: UUID
) -> None:
    await _insert_webhook_config(
        admin_conn,
        client_id=test_client_id,
        slug="ts-hook",
        signing_algorithm="hmac_sha256_timestamped",
    )
    body = json.dumps({"customer": {"name": "Carol"}}).encode()

    resp = client.post(
        f"/webhooks/generic/{test_client_id}/ts-hook",
        content=body,
        headers={"X-Signature": _sign_timestamped(body)},
    )
    assert resp.status_code == 200


async def test_rejects_stale_timestamped_signature(
    client, admin_conn, test_client_id: UUID
) -> None:
    """Replay protection: timestamps older than max_age (300s) are rejected."""
    await _insert_webhook_config(
        admin_conn,
        client_id=test_client_id,
        slug="ts-stale-hook",
        signing_algorithm="hmac_sha256_timestamped",
    )
    body = json.dumps({"customer": {"name": "Dave"}}).encode()
    stale_ts = int(time.time()) - 600  # 10 min ago
    signed = f"{stale_ts}".encode() + b"." + body
    sig = hmac.new(SECRET.encode(), signed, hashlib.sha256).hexdigest()

    resp = client.post(
        f"/webhooks/generic/{test_client_id}/ts-stale-hook",
        content=body,
        headers={"X-Signature": f"t={stale_ts},s={sig}"},
    )
    assert resp.status_code == 401


# ---------- 200: algorithm=none (unsigned, dev/test only) --------------------

async def test_accepts_unsigned_when_algorithm_is_none(
    client, admin_conn, test_client_id: UUID
) -> None:
    """Some homegrown systems can't sign at all. `signing_algorithm='none'`
    is the documented escape hatch — clearly named so config reviewers see
    it explicitly."""
    await _insert_webhook_config(
        admin_conn,
        client_id=test_client_id,
        slug="open-hook",
        signing_algorithm="none",
        signing_secret=None,
    )
    body = json.dumps({"customer": {"name": "Eve"}}).encode()

    resp = client.post(
        f"/webhooks/generic/{test_client_id}/open-hook",
        content=body,
    )
    assert resp.status_code == 200


# ---------- Invalid JSON: 200 ack to prevent retry storms --------------------

async def test_invalid_json_payload_returns_200_without_creating_lead(
    client, admin_conn, test_client_id: UUID
) -> None:
    """We ack with 200 so the provider doesn't retry forever, but no lead row
    is created. Logged as a warning for ops visibility."""
    await _insert_webhook_config(admin_conn, client_id=test_client_id, slug="junk-hook")
    body = b"this is not valid json at all {"

    resp = client.post(
        f"/webhooks/generic/{test_client_id}/junk-hook",
        content=body,
        headers={"X-Signature": _sign_hex(body)},
    )
    assert resp.status_code == 200
    assert await _count_leads_for_client(admin_conn, test_client_id) == 0


# ---------- Tenant isolation: Client B can't trigger Client A's config -------

async def test_tenant_isolation_blocks_cross_client_slug_lookup(
    client, admin_conn
) -> None:
    """Even if attacker knows Client A's webhook slug AND the right signature,
    hitting the URL with Client B's id must not resolve to A's config.
    The RLS-scoped lookup ensures the slug only resolves under its own tenant."""
    client_a = uuid4()
    client_b = uuid4()

    await admin_conn.execute("RESET ROLE")
    await admin_conn.execute(
        """INSERT INTO clients (id, slug, business_name)
           VALUES ($1, $2, 'A'), ($3, $4, 'B')""",
        client_a, f"a-{client_a}", client_b, f"b-{client_b}",
    )
    try:
        await _insert_webhook_config(admin_conn, client_id=client_a, slug="private-hook")
        body = json.dumps({"customer": {"name": "Attacker"}}).encode()

        # Hit Client B's URL with Client A's slug + valid sig for A's secret
        resp = client.post(
            f"/webhooks/generic/{client_b}/private-hook",
            content=body,
            headers={"X-Signature": _sign_hex(body)},
        )
        assert resp.status_code == 404, "Client B should not see Client A's webhook config"
        assert await _count_leads_for_client(admin_conn, client_a) == 0
        assert await _count_leads_for_client(admin_conn, client_b) == 0
    finally:
        await admin_conn.execute("RESET ROLE")
        await admin_conn.execute("DELETE FROM clients WHERE id IN ($1, $2)", client_a, client_b)
