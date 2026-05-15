"""Per-(client, user) permission lookups for client portal RBAC.

Missing row → defaults. The tenant context determines which client_id
gets checked. Used by auth.require_permission() and require_admin() as
async FastAPI dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.db import get_connection, get_current_tenant


@dataclass
class UserPermissions:
    can_edit_kb: bool = False
    can_delete_kb: bool = False
    can_export: bool = True
    can_view_leads: bool = True
    can_edit_config: bool = False
    is_admin: bool = False


_DEFAULTS = UserPermissions()


async def get_user_permissions(user_id: str) -> UserPermissions:
    """Fetch the current tenant's permissions for `user_id`.

    Returns defaults when the user has no row or no tenant context is set
    (e.g. admin-context calls outside a request scope).
    """
    if not user_id:
        return _DEFAULTS

    client_id = get_current_tenant()
    if client_id is None:
        return _DEFAULTS

    async with get_connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT can_edit_kb, can_delete_kb, can_export, can_view_leads,
                   can_edit_config, is_admin
            FROM user_permissions
            WHERE client_id = $1 AND user_id = $2
            """,
            client_id,
            user_id,
        )

    if row is None:
        return _DEFAULTS

    return UserPermissions(
        can_edit_kb=bool(row["can_edit_kb"]),
        can_delete_kb=bool(row["can_delete_kb"]),
        can_export=bool(row["can_export"]),
        can_view_leads=bool(row["can_view_leads"]),
        can_edit_config=bool(row["can_edit_config"]),
        is_admin=bool(row["is_admin"]),
    )
