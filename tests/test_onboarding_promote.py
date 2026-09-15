"""Onboarding: submission mapping + promote-to-tenant.

Pure tests (always run) cover the canonical mapper and ProvisionSpec
validation. DB-backed tests (skip without TRACEFLOW_TEST_DB_URL) cover
provision_client and the promote handler end-to-end.

Run the full set:
    TRACEFLOW_TEST_DB_URL=postgres://... pytest tests/test_onboarding_promote.py -v
"""

from __future__ import annotations

import json
from uuid import uuid4

import asyncpg
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.services.onboarding_mapping import map_submission
from app.services.provisioning import ProvisionSpec, provision_client

SAMPLE_PAYLOAD = {
    "business_name": "Acme Surfaces",
    "legal_name": "Acme Surfaces LLC",
    "dba": "Acme",
    "address": "123 Main St, Las Vegas, NV",
    "website_url": "https://acme.example",
    "google_business_profile_url": "https://g.page/acme",
    "timezone": "America/Los_Angeles",
    "tier": "founding_partner",
    "service_area_zips": ["89101", "89102"],
    "business_hours": {"mon": {"open": "08:00", "close": "17:00"}},
    "crm_provider": "GHL",  # case-normalized by the mapper
    "category": "surface refinishing",
    "tone_of_voice": "warm and direct",
    "owner": {"name": "Pat Owner", "phone": "+17025550001", "email": "pat@acme.example"},
    "notification_emails": ["ops@acme.example"],
    "modules": {"llr": True},
}


# ===========================================================================
# Pure: mapping + spec validation
# ===========================================================================


def test_map_submission_produces_valid_spec() -> None:
    mapped = map_submission(SAMPLE_PAYLOAD)
    spec = ProvisionSpec(**mapped)  # must not raise

    assert spec.slug == "acme-surfaces"
    assert spec.business_name == "Acme Surfaces"
    assert spec.tier.value == "founding_partner"
    assert spec.crm_provider == "ghl"  # normalized from "GHL"
    assert spec.brand["business_name"] == "Acme Surfaces"
    assert spec.brand["category"] == "surface refinishing"
    assert spec.business_profile["address"] == "123 Main St, Las Vegas, NV"
    assert spec.business_profile["legal_name"] == "Acme Surfaces LLC"
    assert spec.business_profile["owner"]["email"] == "pat@acme.example"
    assert spec.feature_flags == {"llr": True}


def test_map_submission_drops_unknown_crm() -> None:
    mapped = map_submission({**SAMPLE_PAYLOAD, "crm_provider": "salesforce"})
    assert map_submission(SAMPLE_PAYLOAD)  # sanity
    assert mapped["crm_provider"] is None


def test_map_submission_derives_slug_from_name() -> None:
    mapped = map_submission({"business_name": "Bob's Tile & Stone!!"})
    assert mapped["slug"] == "bob-s-tile-stone"


def test_provision_spec_forbids_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        ProvisionSpec(slug="x-y", business_name="X", surprise="nope")  # type: ignore[call-arg]


def test_provision_spec_rejects_bad_crm() -> None:
    with pytest.raises(ValidationError):
        ProvisionSpec(slug="x-y", business_name="X", crm_provider="salesforce")


# ===========================================================================
# DB-backed: provision + promote
# ===========================================================================


@pytest.fixture
async def raw_conn(db_url: str):
    conn = await asyncpg.connect(db_url)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def pool(db_url: str):
    from app import db

    await db.init_pool()
    try:
        yield
    finally:
        await db.close_pool()


@pytest.mark.asyncio
async def test_provision_client_creates_rows_with_profile(pool, raw_conn) -> None:
    spec = ProvisionSpec(**map_submission({**SAMPLE_PAYLOAD, "business_name": f"Acme {uuid4()}"}))
    client_id = await provision_client(spec)
    try:
        await raw_conn.execute("RESET ROLE")
        client = await raw_conn.fetchrow("SELECT slug, tier FROM clients WHERE id = $1", client_id)
        profile = await raw_conn.fetchval(
            "SELECT business_profile FROM client_configs WHERE client_id = $1", client_id
        )
        assert client is not None
        assert client["tier"] == "founding_partner"
        # raw_conn has no JSON codec → business_profile comes back as text.
        assert json.loads(profile)["address"] == "123 Main St, Las Vegas, NV"
    finally:
        await raw_conn.execute("RESET ROLE")
        await raw_conn.execute("DELETE FROM clients WHERE id = $1", client_id)


@pytest.mark.asyncio
async def test_promote_creates_client_and_flips_status(pool, raw_conn) -> None:
    from app.routers.admin.onboarding import promote_submission
    from app.services.admin_auth import AdminInfo

    mapped = map_submission({**SAMPLE_PAYLOAD, "business_name": f"Promo {uuid4()}"})
    await raw_conn.execute("RESET ROLE")
    submission_id = await raw_conn.fetchval(
        """
        INSERT INTO onboarding_submissions (source, business_name, mapped_config)
        VALUES ('native', $1, $2::jsonb)
        RETURNING id
        """,
        mapped["business_name"],
        json.dumps(mapped),
    )
    admin = AdminInfo(id=uuid4(), email="andy@traceflow.app", name="Andy", role="owner")
    client_id = None
    try:
        result = await promote_submission(submission_id, admin=admin)
        client_id = result.client_id

        row = await raw_conn.fetchrow(
            "SELECT status, promoted_client_id FROM onboarding_submissions WHERE id = $1",
            submission_id,
        )
        assert row["status"] == "promoted"
        assert row["promoted_client_id"] == client_id
        assert await raw_conn.fetchval("SELECT 1 FROM clients WHERE id = $1", client_id) == 1

        # Re-promote is a 409, not a duplicate client.
        with pytest.raises(HTTPException) as exc:
            await promote_submission(submission_id, admin=admin)
        assert exc.value.status_code == 409
    finally:
        await raw_conn.execute("RESET ROLE")
        if client_id is not None:
            await raw_conn.execute("DELETE FROM clients WHERE id = $1", client_id)
        await raw_conn.execute("DELETE FROM onboarding_submissions WHERE id = $1", submission_id)


@pytest.mark.asyncio
async def test_promote_invalid_mapped_config_is_422(pool, raw_conn) -> None:
    from app.routers.admin.onboarding import promote_submission
    from app.services.admin_auth import AdminInfo

    await raw_conn.execute("RESET ROLE")
    submission_id = await raw_conn.fetchval(
        """
        INSERT INTO onboarding_submissions (source, business_name, mapped_config)
        VALUES ('native', 'Bad', $1::jsonb)
        RETURNING id
        """,
        json.dumps({"slug": "no-business-name"}),  # missing required business_name
    )
    admin = AdminInfo(id=uuid4(), email="andy@traceflow.app", name="Andy", role="owner")
    try:
        with pytest.raises(HTTPException) as exc:
            await promote_submission(submission_id, admin=admin)
        assert exc.value.status_code == 422
    finally:
        await raw_conn.execute("RESET ROLE")
        await raw_conn.execute("DELETE FROM onboarding_submissions WHERE id = $1", submission_id)
