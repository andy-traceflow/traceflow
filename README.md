# TraceFlow

> AI Lead Recovery and Operations Automation for surface, countertop, flooring, and pool resurfacing contractors.

**Status:** Pre-launch (Phase 0)
**Domain:** [traceflow.app](https://traceflow.app)
**Founder:** Andy

---

## What this repo is

The TraceFlow platform: a multi-tenant FastAPI application that recovers missed-call leads, qualifies them via AI-driven SMS conversations, and routes them into client CRMs. Each client is a tenant in shared infrastructure, configured rather than custom-built.

This repo also contains the living strategic documentation, operating playbooks, and Claude Code skills that power the business.

## Repo structure

```
traceflow/
├── CLAUDE.md                # Master context for Claude Code
├── README.md                # This file
├── pyproject.toml           # Python project + deps
├── render.yaml              # Render service config (web + adapter health cron)
├── Dockerfile               # python:3.12-slim + uvicorn
├── docker-compose.yml       # Local dev
├── .env.example             # Platform-level secrets template
├── .github/
│   └── workflows/
│       └── ci.yml           # pgvector Postgres + migrations + ruff + pytest
├── .claude/
│   └── skills/              # Repeatable Claude Code skills (multi-tenant-arch, adapter-pattern, etc.)
├── docs/
│   ├── PRD.md               # Product requirements (single source of truth)
│   ├── architecture.md      # Target architecture deep-dive
│   ├── workflow-schema.md   # YAML lifecycle schemas
│   ├── CHANGELOG.md         # Decision log
│   ├── decisions/           # Architecture Decision Records
│   └── playbooks/           # Discovery, outreach, onboarding playbooks
├── migrations/              # Numbered SQL — applied in order by scripts/apply_migrations.py
│   ├── 001_create_clients_and_configs.sql
│   ├── 002_create_client_field_mappings.sql   # Layer 2
│   ├── 003_create_client_webhook_configs.sql  # Layer 3
│   ├── 004_create_leads_messages_events.sql   # canonical schema
│   ├── 005_create_kb_tables.sql               # SIA Module C, pgvector
│   ├── 006_create_audit_log.sql               # trigger-based audit
│   ├── 007_create_sync_log.sql
│   ├── 008_create_user_permissions.sql
│   ├── 009_create_calculator_tables.sql       # SIA Module B
│   ├── 010_force_rls_on_tenant_tables.sql     # defense in depth: RLS applies to owners too
│   └── 011_null_safe_tenant_policies.sql      # NULLIF guards against '' → uuid cast errors
├── src/
│   └── app/
│       ├── main.py          # FastAPI app, lifespan, router mounts
│       ├── config.py        # pydantic-settings
│       ├── db.py            # asyncpg pool + SET ROLE authenticated + JSON/JSONB codec
│       ├── middleware/      # tenant_resolver, auth (JWKS), signature_verify
│       ├── webhooks/        # shopify, twilio (stub), crm (stub), generic (Layer 3) ✅
│       ├── adapters/        # base Protocol, monday, ghl (stub), registry
│       ├── models/          # Client, ClientConfig, Lead, Message, Event, KBEntry
│       ├── services/        # dedupe, notifications, webhook_signature, field_mappings,
│       │                    # permissions, audit, calculator
│       ├── routers/         # kb, kb_export, calculator
│       └── jobs/            # adapter_health (hourly cron)
├── tests/                   # 124 tests — pure unit + live-DB integration
│   ├── conftest.py          # mirrors TRACEFLOW_TEST_DB_URL → SUPABASE_DB_URL for app boot
│   ├── test_tenant_isolation.py   # Non-negotiable RLS suite (31 tests)
│   ├── test_tenant_resolver.py    # Path regex extraction (28 tests)
│   ├── test_generic_webhook.py    # Layer 3 integration via TestClient (16 tests)
│   ├── test_dedupe.py
│   ├── test_webhook_signature.py
│   ├── adapters/test_monday_adapter.py
│   ├── services/test_calculator.py
│   ├── services/test_field_mappings.py
│   └── sql/
│       └── bootstrap_supabase_stubs.sql       # CI: creates auth.users + anon/authenticated/service_role roles
└── scripts/
    ├── apply_migrations.py         # Idempotent migration runner (tracking via schema_migrations)
    ├── inspect_monday_board.py     # Onboarding helper: list a board's column IDs
    └── onboard_client.py           # Phase 1 tenant provisioner (YAML in, DB rows out)
```

## Getting started (for Claude Code sessions)

1. **Read `CLAUDE.md`** — the master context file
2. **Skim `docs/PRD.md`** — strategy and product spec (sections 7 + 8 most relevant for code work)
3. **Load relevant `.claude/skills/<skill>/SKILL.md`** files based on the task
4. **Check `docs/CHANGELOG.md`** for recent decisions and changes

## Running locally

```bash
# Install (project + dev tools — pytest, ruff, mypy)
pip install -e ".[dev]"

# Apply migrations against your Supabase project (or any Postgres with pgvector).
# Idempotent: skips already-applied migrations via a `schema_migrations` table.
SUPABASE_DB_URL=postgresql://postgres:PWD@db.<ref>.supabase.co:5432/postgres \
    python scripts/apply_migrations.py

# Start the API locally
uvicorn app.main:app --reload --port 8000

# Run the pure unit tests (no DB needed — TRACEFLOW_TEST_DB_URL unset → skip cleanly)
pytest tests/test_dedupe.py tests/test_webhook_signature.py tests/test_tenant_resolver.py \
       tests/services tests/adapters

# Run the full suite incl. tenant isolation + generic webhook integration
# (needs a Postgres with migrations applied and the Supabase-standard roles —
#  see tests/sql/bootstrap_supabase_stubs.sql for what CI does to a vanilla DB)
TRACEFLOW_TEST_DB_URL=postgresql://... pytest -v
```

**Two env vars, one purpose:** `SUPABASE_DB_URL` is read by the production app and the migration runner. `TRACEFLOW_TEST_DB_URL` is read by the test suite. `tests/conftest.py` mirrors `TRACEFLOW_TEST_DB_URL` into `SUPABASE_DB_URL` at test collection time so the FastAPI app's lifespan can boot against the test DB — you only need to set the one for testing.

**Connection string format on Supabase:** integration tests run fine against the **direct** connection (`db.<ref>.supabase.co:5432`) if your local network has IPv6. The deployed FastAPI on Render uses the **session-mode pooler** (`aws-X-<region>.pooler.supabase.com:5432` with user `postgres.<ref>`) because Render's outbound network is IPv4-only and Supabase Free's direct port is IPv6-only.

## CI

`.github/workflows/ci.yml` runs on every push and PR:

1. Spins up a `pgvector/pgvector:pg16` Postgres
2. Applies `tests/sql/bootstrap_supabase_stubs.sql` + all migrations in order
3. Runs `ruff check`
4. Runs `pytest` with `TRACEFLOW_TEST_DB_URL` set — the tenant isolation suite is strict here and any cross-tenant leak blocks the build

## License

Proprietary. All rights reserved.
