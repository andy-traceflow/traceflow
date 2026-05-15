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
├── migrations/              # Numbered SQL — apply in order in Supabase SQL Editor
│   ├── 001_create_clients_and_configs.sql
│   ├── 002_create_client_field_mappings.sql   # Layer 2
│   ├── 003_create_client_webhook_configs.sql  # Layer 3
│   ├── 004_create_leads_messages_events.sql   # canonical schema
│   ├── 005_create_kb_tables.sql               # SIA Module C, pgvector
│   ├── 006_create_audit_log.sql               # trigger-based audit
│   ├── 007_create_sync_log.sql
│   ├── 008_create_user_permissions.sql
│   └── 009_create_calculator_tables.sql       # SIA Module B
├── src/
│   └── app/
│       ├── main.py          # FastAPI app, lifespan, router mounts
│       ├── config.py        # pydantic-settings
│       ├── db.py            # asyncpg pool + per-request tenant ContextVar
│       ├── middleware/      # tenant_resolver, auth (JWKS), signature_verify
│       ├── webhooks/        # shopify, twilio (stub), crm (stub), generic (Layer 3)
│       ├── adapters/        # base Protocol, monday, ghl (stub), registry
│       ├── models/          # Client, ClientConfig, Lead, Message, Event, KBEntry
│       ├── services/        # dedupe, notifications, webhook_signature, field_mappings,
│       │                    # permissions, audit, calculator
│       ├── routers/         # kb, kb_export, calculator
│       └── jobs/            # adapter_health (hourly cron)
├── tests/
│   ├── conftest.py
│   ├── test_tenant_isolation.py   # Non-negotiable RLS suite
│   ├── test_dedupe.py
│   ├── test_webhook_signature.py
│   ├── adapters/test_monday_adapter.py
│   ├── services/test_calculator.py
│   ├── services/test_field_mappings.py
│   └── sql/
│       └── bootstrap_supabase_stubs.sql       # CI-only auth.users + auth.uid() stubs
└── scripts/
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
# Install
pip install -e ".[dev]"

# Apply migrations to your Supabase project (or local Postgres with pgvector)
# via the Supabase Dashboard > SQL Editor — paste each file in order.

# Start the API
uvicorn app.main:app --reload --port 8000

# Run the pure unit tests (no DB needed)
pytest tests/test_dedupe.py tests/test_webhook_signature.py tests/services tests/adapters

# Run the full suite incl. tenant isolation (needs a Postgres + migrations applied)
TRACEFLOW_TEST_DB_URL=postgresql://... pytest -v
```

## CI

`.github/workflows/ci.yml` runs on every push and PR:

1. Spins up a `pgvector/pgvector:pg16` Postgres
2. Applies `tests/sql/bootstrap_supabase_stubs.sql` + all migrations in order
3. Runs `ruff check`
4. Runs `pytest` with `TRACEFLOW_TEST_DB_URL` set — the tenant isolation suite is strict here and any cross-tenant leak blocks the build

## License

Proprietary. All rights reserved.
