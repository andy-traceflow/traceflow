# CLAUDE.md — SIA Kit

Read this first. It replaces the TraceFlow context entirely; that business
is retired and none of its domain survives in this repo.

## What this is

**SIA Kit** is the delivery toolkit behind a fixed-price productized
service: *one Shopify event → one destination system, deployed, monitored,
with a runbook.* Each client engagement is a **fork of this repository**,
configured by environment variables and one `mapping.yaml`, deployed to its
own Render service with its own Postgres. Delivered in 5 business days for
$900, so the template must be **boring, correct, and fast to configure**.

Destinations: Notion, Monday.com, HubSpot, Slack, Google Sheets.

## The three principles (in priority order)

1. **Durable first, process second.** `POST /webhooks/shopify/{topic}` does
   exactly verify → insert one `events` row → 200. All transformation and
   delivery happens in `app/worker.py`, reading from that table. Never put
   work in a `BackgroundTask` — Render instances restart and spin down, and
   in-process work dies with them.
2. **Config in env vars and one mapping file.** No database-backed
   configuration of any kind. `.env` + `mapping.yaml` fully describe a
   deployment. If you are tempted to add a settings table, the answer is
   an env var or a key in `mapping.yaml`.
3. **Fail closed, always.** No dev-mode bypasses, no "skip verification if
   unset". Missing secret, DB URL, `DESTINATION`, `ADMIN_TOKEN`, malformed
   mapping, missing destination credentials → the process refuses to start
   (`config.REQUIRED_AT_STARTUP`, `pipeline.build_pipeline()` in the lifespan).

## The fork-per-client model — there is no `client_id`

One deployment serves exactly one client. There is **no `client_id`
anywhere**: no tenant column, no RLS, no per-request tenant context, no
tenant resolver. If two clients need different behavior, they are two forks
with two `mapping.yaml`s — never a branch in code, never a row in a table.
The old rule "every table has `client_id`, every query filters by it" is
inverted here on purpose.

**This repository is the template. Nothing deploys `main`.** Each engagement
is a new repo created from it (GitHub "Use this template"), with its own
Render Blueprint (`render.yaml` with `CLIENT` replaced), its own Postgres,
its own env group. The Render service `traceflow-api` that used to track
`main` has auto-deploy off and belongs to the retired product; do not wire
this repo to any service.

## Map

```
mapping.yaml                 the per-engagement transform — the file a fork edits
migrations/001_create_events.sql   the durable store (only table)
src/app/
  main.py        3 routers (webhook, health, events) + startup checks
  worker.py      claim (FOR UPDATE SKIP LOCKED, leased) → transform → deliver → retry/dead
  pipeline.py    Pipeline(transform, deliver); PermanentDeliveryError / TransientDeliveryError / TransformError
  mapping.py     mapping.yaml schema (Pydantic, extra=forbid), JSONPath/dotted paths, transforms, build_record()
  config.py      Settings + REQUIRED_AT_STARTUP
  services/webhook_signature.py   pure HMAC verifiers (do not touch) + verify_shopify_signature dependency
  services/events.py    EventStore Protocol + PostgresEventStore — ALL events SQL lives here
  services/notifications.py   dead-letter alert → ALERT_WEBHOOK_URL
  adapters/base.py      Destination Protocol + the record contract (read it before touching adapters)
  adapters/registry.py  lazy construct-once; register_adapter / get_adapter
  adapters/{monday,hubspot,notion,slack,sheets}.py
tests/fakes.py          InMemoryEventStore mirroring Postgres semantics
tests/adapters/         every adapter via httpx.MockTransport
tests/test_events_store_db.py   Postgres-backed; skips without TEST_DB_URL, runs in CI
```

## Conventions

- **Retry semantics live in the worker, classification in the exception
  type.** Adapters raise `PermanentDeliveryError` / `TransientDeliveryError`
  for in-body errors and let `httpx.HTTPStatusError` propagate; the worker
  maps 4xx (except 408/429) → permanent, everything else → transient.
  Never swallow an error and return `None` from an adapter.
- **The record contract** (`adapters/base.py`): keys are the destination's
  display names; reserved `_name`, `_key`, `_line_items`. Adapters resolve
  names by introspecting the destination, never by hard-coding.
- **Dedupe is the unique constraint** `(source, webhook_id)` +
  `ON CONFLICT DO NOTHING`. Do not add an in-memory cache.
- **`next_retry_at` has two meanings** (see the migration): backoff when
  `received`, claim lease when `processing`.
- **Tests make no network calls.** Adapters: `httpx.MockTransport`. Store:
  `tests/fakes.InMemoryEventStore`, injected with `app.dependency_overrides`.
  The whole suite runs in a few seconds; keep it that way.
- **Logging:** stdlib `logging` with `extra={...}`; `app/log.py` renders
  JSON. Every event transition logs `event_id`, `webhook_id`, `status`,
  `attempts`, `duration_ms`.
- Python 3.11+, FastAPI, asyncpg, httpx, Pydantic v2, pytest. No new
  frameworks. `ruff check .` must pass.

## Never

- Never add a `client_id`, a tenant concept, or per-client code branches.
  Different client → different fork.
- Never process inside the webhook handler or in a `BackgroundTask`.
- Never add a bypass keyed on `ENVIRONMENT` or any other env var.
- Never read configuration from the database.
- Never change the three pure verifiers in `webhook_signature.py`.
- Never mark an event `delivered` before the destination has returned an id.

## Commands

```bash
pip install -e ".[dev]"
ruff check . && pytest -q
uvicorn app.main:app --reload --port 8000
python scripts/send_test_webhook.py http://localhost:8000
python -m app.worker --once
python -c "from app.mapping import load_mapping; print(load_mapping())"
```

`README.md` is the fork-and-deploy runbook; `RUNBOOK.md` is the
client-facing document; `MIGRATION.md` is the audit log of how this repo
was converted from TraceFlow.
