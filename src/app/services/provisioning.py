"""Tenant provisioning — the single create-client code path.

Both the admin "promote" action (routers/admin/onboarding.py) and the CLI
(scripts/onboard_client.py) go through ``provision_client`` so there is exactly
one place that writes the clients + client_configs pair. This is the safe-write
pattern from admin/clients.py::update_client_config applied to creation:
service-role connection, one transaction, an ``extra="forbid"`` Pydantic spec
so a stray key is a validation error rather than a silent column, and NO
secrets (crm_credentials / webhook_signing_secrets are collected out-of-band).

Not yet automated here (Phase 2 per docs/playbooks/new-client-onboarding.md):
Twilio number allocation, webhook signing-secret generation, Render env sync.
Those stay manual until the provisioner grows them; ``provision_client`` only
creates the rows.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.db import get_service_connection
from app.models.client import ClientTier

# client_configs columns provision may set. Deliberately excludes secrets
# (crm_credentials, webhook_signing_secrets) and runtime counters. JSONB columns
# accept dicts directly because the pool registers JSON codecs (see db.py).
_CONFIG_COLUMNS = (
    "business_hours",
    "service_area_zips",
    "twilio_number",
    "vip_keywords",
    "vip_value_threshold",
    "crm_provider",
    "greeting_template",
    "brand",
    "notification_emails",
    "owner_alert_emails",
    "owner_alert_phones",
    "feature_flags",
    "business_profile",
)


class ProvisionSpec(BaseModel):
    """Validated input for creating one tenant.

    extra="forbid" mirrors ClientConfigUpdate: a typo'd or unexpected key is a
    422/ValidationError, never a silently-dropped field or an injected column.
    """

    model_config = ConfigDict(extra="forbid")

    # clients
    slug: str = Field(pattern=r"^[a-z0-9-]+$", min_length=2, max_length=64)
    business_name: str = Field(min_length=1)
    tier: ClientTier = ClientTier.standard
    timezone: str = "America/Los_Angeles"

    # client_configs (no secrets)
    business_hours: dict[str, Any] = Field(default_factory=dict)
    service_area_zips: list[str] = Field(default_factory=list)
    twilio_number: str | None = None
    vip_keywords: list[str] = Field(default_factory=list)
    vip_value_threshold: float | None = None
    crm_provider: str | None = Field(default=None, pattern=r"^(ghl|hubspot|monday|generic)$")
    greeting_template: str | None = None
    brand: dict[str, Any] = Field(default_factory=dict)
    notification_emails: list[str] = Field(default_factory=list)
    owner_alert_emails: list[str] = Field(default_factory=list)
    owner_alert_phones: list[str] = Field(default_factory=list)
    feature_flags: dict[str, Any] = Field(default_factory=dict)
    business_profile: dict[str, Any] = Field(default_factory=dict)


class SlugConflictError(Exception):
    """Raised when a client with the same slug (or twilio_number) already exists."""


def slugify(business_name: str) -> str:
    """Best-effort url-safe slug from a business name. Callers should still
    ensure uniqueness (provision raises SlugConflictError on collision)."""
    slug = re.sub(r"[^a-z0-9]+", "-", business_name.lower()).strip("-")
    return slug[:64] or "client"


async def _insert_client(conn: asyncpg.Connection, spec: ProvisionSpec) -> UUID:
    client_id = uuid4()
    await conn.execute(
        """
        INSERT INTO clients (id, slug, business_name, tier, timezone)
        VALUES ($1, $2, $3, $4, $5)
        """,
        client_id,
        spec.slug,
        spec.business_name,
        spec.tier.value,
        spec.timezone,
    )

    values = spec.model_dump(include=set(_CONFIG_COLUMNS))
    cols = ", ".join(_CONFIG_COLUMNS)
    placeholders = ", ".join(f"${i}" for i in range(2, len(_CONFIG_COLUMNS) + 2))
    await conn.execute(
        f"INSERT INTO client_configs (client_id, {cols}) VALUES ($1, {placeholders})",
        client_id,
        *(values[col] for col in _CONFIG_COLUMNS),
    )
    return client_id


async def provision_client(
    spec: ProvisionSpec, *, conn: asyncpg.Connection | None = None
) -> UUID:
    """Create a tenant (clients + client_configs) and return its id.

    Pass ``conn`` to run inside a caller's existing service-role transaction
    (e.g. promote, which also flips a submission row atomically). Omit it to
    acquire and commit a standalone service-role transaction.

    Raises SlugConflictError if the slug or twilio_number already exists.
    """
    try:
        if conn is not None:
            return await _insert_client(conn, spec)
        async with get_service_connection() as own_conn:
            return await _insert_client(own_conn, spec)
    except asyncpg.exceptions.UniqueViolationError as e:
        raise SlugConflictError(str(e)) from e


# ===========================================================================
# Portal account provisioning ("prompt them to create an account")
#
# Links a promoted client to a Supabase Auth user + a tenant-admin permission
# row. This is World B (ADR-0004): the client user pool is entirely separate
# from the platform admin_users table — nothing here touches admin_users.
#
# The Supabase call is isolated in _create_supabase_user so tests can stub it;
# the auth.users row it creates is what user_permissions.user_id references.
# ===========================================================================


class PortalUserError(Exception):
    """Raised when the Supabase Auth user could not be created."""


async def _create_supabase_user(email: str, name: str | None, *, invite: bool) -> UUID:
    """Create a Supabase Auth user via the Admin API, returning its id.

    invite=True uses the invite endpoint (creates the user AND emails a
    magic-link so they set their own password — no password ever touches us).
    invite=False creates the user without sending mail (e.g. tests/backfills).
    """
    settings = get_settings()
    if not (settings.supabase_url and settings.supabase_service_key):
        raise PortalUserError("SUPABASE_URL / SUPABASE_SERVICE_KEY not configured")

    endpoint = "/auth/v1/invite" if invite else "/auth/v1/admin/users"
    payload: dict[str, Any] = {"email": email}
    if not invite:
        payload["email_confirm"] = False
    if name:
        # invite carries metadata under `data`; admin/users under `user_metadata`.
        payload["data" if invite else "user_metadata"] = {"name": name}

    headers = {
        "apikey": settings.supabase_service_key,
        "Authorization": f"Bearer {settings.supabase_service_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{settings.supabase_url}{endpoint}", json=payload, headers=headers
        )
    if resp.status_code >= 400:
        raise PortalUserError(f"Supabase user create failed: {resp.status_code} {resp.text}")
    user_id = resp.json().get("id")
    if not user_id:
        raise PortalUserError("Supabase response missing user id")
    return UUID(str(user_id))


async def provision_portal_user(
    client_id: UUID,
    email: str,
    name: str | None = None,
    *,
    invite: bool = True,
    conn: asyncpg.Connection | None = None,
) -> UUID:
    """Grant a person tenant-admin access to one client's portal.

    Creates (and, by default, invites) the Supabase Auth user, then upserts a
    ``user_permissions`` row with ``is_admin=true`` for ``(client_id, user_id)``
    — the client-scoped admin the schema already models (migration 008). The
    client must already exist (FK); call this after ``provision_client``.

    Returns the Supabase user id. Raises PortalUserError on the auth call.
    """
    user_id = await _create_supabase_user(email, name, invite=invite)

    async def _grant(c: asyncpg.Connection) -> None:
        await c.execute(
            """
            INSERT INTO user_permissions
                (client_id, user_id, is_admin, can_edit_config, can_view_leads, can_export)
            VALUES ($1, $2, TRUE, TRUE, TRUE, TRUE)
            ON CONFLICT (client_id, user_id)
                DO UPDATE SET is_admin = TRUE, can_edit_config = TRUE
            """,
            client_id,
            user_id,
        )

    if conn is not None:
        await _grant(conn)
    else:
        async with get_service_connection() as own_conn:
            await _grant(own_conn)
    return user_id
