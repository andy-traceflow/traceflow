"""In-memory stand-in for an asyncpg service connection (no database).

``get_service_connection()`` yields a ``FakeConn`` for demo-role requests
(see ``db.get_service_connection`` + the ``_demo`` ContextVar). It routes the
admin surface's SQL **by shape** (substring match) to the canned multi-tenant
dataset in ``fixtures.py`` — the same technique ``tests/test_admin.py`` and the
local ``dev_admin_preview.py`` harness use, generalized from one client to the
whole roster.

Reads cover every admin query. Writes mutate the shared in-memory dataset so
the local preview round-trips edits; in the deployed public demo, writes never
reach here because the demo role is blocked at the HTTP layer
(``forbid_demo_writes``). Anything unmatched is a safe no-op / empty result.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from . import fixtures

# dev_admin_preview.py sets this so its owner login resolves against admin_users.
# The deployed demo never queries admin_users through here (require_admin_user
# short-circuits the demo role before touching any connection), so it stays None.
_PREVIEW_ADMIN_ROW: dict[str, Any] | None = None


def set_preview_admin_row(row: dict[str, Any] | None) -> None:
    global _PREVIEW_ADMIN_ROW
    _PREVIEW_ADMIN_ROW = row


# Lead-list projection columns (mirrors leads.py:list_leads SELECT).
_LIST_COLS = (
    "id", "created_at", "contact_name", "phone", "email", "classification",
    "qualification_status", "qualification_score", "service_type", "budget_range",
    "timeframe", "outcome", "recovered_value", "external_id", "pushed_to_crm_at",
    "is_test",
)


def _norm(sql: str) -> str:
    """Collapse whitespace so substring routing survives multiline SQL."""
    return " ".join(sql.split())


def _contact_matches(contact: dict[str, Any], search: str) -> bool:
    """Mirror the contacts list search (phone/name ILIKE)."""
    if not search:
        return True
    return search in (contact["phone"] or "").lower() or search in (contact["name"] or "").lower()


def _messages_for(bundle: dict[str, Any], lead_id: Any) -> list[dict[str, Any]]:
    messages: dict[Any, list[dict[str, Any]]] = bundle["messages"]
    return messages.get(lead_id, [])


def _apply_config_update(config: dict[str, Any], sql: str, args: tuple[Any, ...]) -> None:
    """Parse 'UPDATE client_configs SET a = $2, b = $3 WHERE ...' and apply the
    values (args[1:]) so the preview round-trips config edits. client_id is $1."""
    set_clause = sql.split("SET", 1)[1].split("WHERE", 1)[0]
    cols = [part.split("=")[0].strip() for part in set_clause.split(",")]
    for col, value in zip(cols, args[1:], strict=False):
        config[col] = value


class FakeConn:
    """Routes admin SQL by shape to the canned dataset. Just enough for /api/admin."""

    def __init__(self) -> None:
        self.clients: dict[UUID, dict[str, Any]] = fixtures.clients()

    def _bundle(self, client_id: Any) -> dict[str, Any] | None:
        return self.clients.get(client_id)

    def _find_lead(self, client_id: Any, lead_id: Any) -> dict[str, Any] | None:
        bundle = self._bundle(client_id)
        if bundle is None:
            return None
        return next((ld for ld in bundle["leads"] if ld["id"] == lead_id), None)

    # -- fetchrow ----------------------------------------------------------
    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        sql = _norm(sql)
        if "FROM admin_users WHERE email" in sql:
            row = _PREVIEW_ADMIN_ROW
            return dict(row) if row and args[0] == row["email"] else None
        if "FROM admin_users" in sql:
            row = _PREVIEW_ADMIN_ROW
            return dict(row) if row and args[0] == row["id"] else None
        if "JOIN client_configs cc ON" in sql:  # _CONFIG_SELECT
            bundle = self._bundle(args[0])
            return dict(bundle["config"]) if bundle else None
        if "SELECT * FROM contacts WHERE id" in sql:  # get_contact (contact_id=$1, client_id=$2)
            bundle = self._bundle(args[1])
            if bundle is None:
                return None
            return next(
                (dict(c) for c in bundle.get("contacts", []) if c["id"] == args[0]), None
            )
        if "SELECT * FROM leads" in sql:  # get_lead / repush (lead_id=$1, client_id=$2)
            lead = self._find_lead(args[1], args[0])
            return dict(lead) if lead else None
        if "event_type = 'intent_classified'" in sql:  # client_id=$1, lead_id=$2
            bundle = self._bundle(args[0])
            if bundle and bundle["leads"] and args[1] == bundle["leads"][0]["id"]:
                return {
                    "payload": {"intent": "sales", "proceeded": True},
                    "created_at": fixtures.NOW - timedelta(hours=3),
                }
            return None
        if "SELECT * FROM client_configs" in sql:  # repush config (client_id=$1)
            bundle = self._bundle(args[0])
            return dict(bundle["config"]) if bundle else None
        if "ai_interaction_cap_monthly" in sql and "FROM client_configs" in sql:
            bundle = self._bundle(args[0])
            return dict(bundle["config"]) if bundle else None
        if "INSERT INTO client_field_mappings" in sql:  # upsert (write)
            return self._upsert_mapping(args)
        if "FROM client_field_mappings" in sql:  # delete pre-fetch (client_id=$1)
            bundle = self._bundle(args[0])
            if bundle is None:
                return None
            return next(
                (dict(m) for m in bundle["mappings"]
                 if m["integration"] == args[1] and m["canonical_field"] == args[2]),
                None,
            )
        return None

    # -- fetch -------------------------------------------------------------
    _CONTACT_LIST_COLS = (
        "id", "phone", "name", "contact_type", "contact_type_source",
        "call_count", "lead_count", "last_seen_at", "summary",
    )
    _CONTACT_LEAD_COLS = (
        "id", "created_at", "qualification_status", "classification", "service_type",
        "qualification_score", "value_score", "outcome", "recovered_value",
    )

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        sql = _norm(sql)
        if "FROM contacts" in sql:  # list_contacts (client=$1, type=$2, search=$3, limit=$4, offset=$5)
            bundle = self._bundle(args[0])
            if bundle is None:
                return []
            ctype, search, limit, offset = args[1], (args[2] or "").lower(), args[3], args[4]
            rows = [
                {k: c[k] for k in self._CONTACT_LIST_COLS}
                for c in bundle.get("contacts", [])
                if (ctype == "all" or c["contact_type"] == ctype)
                and _contact_matches(c, search)
            ]
            rows.sort(key=lambda r: r["last_seen_at"], reverse=True)
            return rows[offset:offset + limit]
        if "FROM leads" in sql and "contact_id = $1" in sql:  # get_contact leads (contact=$1, client=$2)
            bundle = self._bundle(args[1])
            if bundle is None:
                return []
            rows = [
                {k: ld.get(k) for k in self._CONTACT_LEAD_COLS}
                for ld in bundle["leads"] if ld.get("contact_id") == args[0]
            ]
            rows.sort(key=lambda r: r["created_at"], reverse=True)
            return rows
        if "FROM clients c" in sql and "LEFT JOIN client_configs" in sql:  # list_clients
            return [
                dict(b["list_row"])
                for b in sorted(self.clients.values(), key=lambda b: b["list_row"]["business_name"])
            ]
        if "FROM leads l" in sql:  # list_leads (client_id=$1, classification=$2, include_test=$3)
            bundle = self._bundle(args[0])
            if bundle is None:
                return []
            classification, include_test = args[1], args[2]
            limit, offset = args[3], args[4]
            rows = [
                {
                    **{k: ld[k] for k in _LIST_COLS},
                    "message_count": len(_messages_for(bundle, ld["id"])),
                    "last_message_at": (
                        max((m["created_at"] for m in _messages_for(bundle, ld["id"])), default=None)
                    ),
                }
                for ld in bundle["leads"]
                if (classification == "all" or ld["classification"] == classification)
                and (include_test or not ld["is_test"])
            ]
            rows.sort(key=lambda r: r["created_at"], reverse=True)
            return rows[offset:offset + limit]
        if "FROM messages" in sql:  # get_conversation (client_id=$1, lead_id=$2)
            bundle = self._bundle(args[0])
            return [dict(m) for m in _messages_for(bundle, args[1])] if bundle else []
        if "GROUP BY 1" in sql and "FROM events e" in sql:  # routing_activity
            bundle = self._bundle(args[0])
            return [dict(b) for b in bundle["routing_buckets"]] if bundle else []
        if "FROM events e" in sql:  # routing_log (client_id=$1, events=$2, limit=$3)
            bundle = self._bundle(args[0])
            if bundle is None:
                return []
            limit = args[2] if len(args) > 2 else len(bundle["routing_log"])
            return [dict(r) for r in bundle["routing_log"][:limit]]
        if "FROM client_field_mappings" in sql:  # list_field_mappings (client_id=$1, integration=$2)
            bundle = self._bundle(args[0])
            if bundle is None:
                return []
            integration = args[1] if len(args) > 1 else None
            return [
                dict(m) for m in bundle["mappings"]
                if integration is None or m["integration"] == integration
            ]
        return []

    # -- fetchval ----------------------------------------------------------
    async def fetchval(self, sql: str, *args: Any) -> Any:
        sql = _norm(sql)
        if "count(*)" in sql and "FROM contacts" in sql:  # list_contacts total
            bundle = self._bundle(args[0])
            if bundle is None:
                return 0
            ctype, search = args[1], (args[2] or "").lower()
            return sum(
                1 for c in bundle.get("contacts", [])
                if (ctype == "all" or c["contact_type"] == ctype) and _contact_matches(c, search)
            )
        if "SELECT 1 FROM contacts WHERE id" in sql:  # retype existence (contact=$1, client=$2)
            bundle = self._bundle(args[1])
            return 1 if bundle and any(c["id"] == args[0] for c in bundle.get("contacts", [])) else None
        if "SELECT 1 FROM clients" in sql:  # client_id=$1
            return 1 if self._bundle(args[0]) else None
        if "SELECT 1 FROM leads" in sql:  # lead_id=$1, client_id=$2
            return 1 if self._find_lead(args[1], args[0]) else None
        if "count(*)" in sql and "FROM leads" in sql:  # client_id=$1, class=$2, include_test=$3
            bundle = self._bundle(args[0])
            if bundle is None:
                return 0
            classification, include_test = args[1], args[2]
            return sum(
                1 for ld in bundle["leads"]
                if (classification == "all" or ld["classification"] == classification)
                and (include_test or not ld["is_test"])
            )
        if "count(*)" in sql and "FROM messages" in sql:  # client_id=$1, lead_id=$2
            bundle = self._bundle(args[0])
            return len(_messages_for(bundle, args[1])) if bundle else 0
        return None

    # -- execute (writes; reached only by the local owner preview) ---------
    async def execute(self, sql: str, *args: Any) -> None:
        sql = _norm(sql)
        if sql.startswith("UPDATE client_configs SET ai_interactions_used = 0"):
            bundle = self._bundle(args[0])
            if bundle:
                bundle["config"]["ai_interactions_used"] = 0
        elif sql.startswith("UPDATE client_configs SET"):
            bundle = self._bundle(args[0])
            if bundle:
                _apply_config_update(bundle["config"], sql, args)
        elif "UPDATE clients SET timezone" in sql:  # tz=$2, client_id=$1
            bundle = self._bundle(args[0])
            if bundle:
                bundle["config"]["timezone"] = args[1]
                bundle["list_row"]["timezone"] = args[1]
        elif "UPDATE leads SET is_test" in sql:  # is_test=$1, lead_id=$2, client_id=$3
            lead = self._find_lead(args[2], args[1])
            if lead:
                lead["is_test"] = args[0]
        elif "UPDATE leads" in sql and "external_id" in sql:  # repush: ext=$1, lead=$2, client=$3
            lead = self._find_lead(args[2], args[1])
            if lead:
                lead["external_id"] = args[0]
                lead["pushed_to_crm_at"] = datetime.now(UTC)
        elif "UPDATE leads" in sql and "outcome" in sql:  # outcome=$1,val=$2,src=$3,lead=$4,client=$5
            lead = self._find_lead(args[4], args[3])
            if lead:
                lead["outcome"] = args[0]
                lead["recovered_value"] = args[1]
                lead["outcome_source"] = args[2]
                lead["outcome_recorded_at"] = datetime.now(UTC)
        elif "DELETE FROM client_field_mappings" in sql:  # client=$1, integration=$2, canonical=$3
            bundle = self._bundle(args[0])
            if bundle:
                bundle["mappings"] = [
                    m for m in bundle["mappings"]
                    if not (m["integration"] == args[1] and m["canonical_field"] == args[2])
                ]
        # Anything else (audit_log INSERT, admin_users last_login_at, …) is a no-op.

    def _upsert_mapping(self, args: tuple[Any, ...]) -> dict[str, Any] | None:
        """INSERT ... ON CONFLICT for client_field_mappings (write)."""
        bundle = self._bundle(args[0])
        if bundle is None:
            return None
        row = {
            "integration": args[1],
            "canonical_field": args[2],
            "external_field": args[3],
            "external_field_type": args[4],
            "transform": args[5],
            "notes": args[6],
            "updated_at": datetime.now(UTC),
        }
        bundle["mappings"] = [
            m for m in bundle["mappings"]
            if not (m["integration"] == args[1] and m["canonical_field"] == args[2])
        ] + [row]
        return dict(row)


@asynccontextmanager
async def fake_service_connection() -> AsyncIterator[FakeConn]:
    """Drop-in for db.get_service_connection() when the demo role is active."""
    yield FakeConn()
