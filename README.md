# SIA Kit

The delivery template behind a fixed-price integration service: **one Shopify
event → one destination system, deployed, monitored, with a runbook.** Each
client engagement is a fork of this repository, configured by environment
variables and one `mapping.yaml`, deployed to its own Render service with its
own Postgres. One deployment serves exactly one client.

Destinations today: **Notion, Monday.com, HubSpot, Slack, Google Sheets.**

```
Shopify
  └─> POST /webhooks/shopify/{topic}
        ├─ HMAC verify (fail closed, always)
        ├─ persist raw event → events table (status='received')
        └─ return 200 in <5s
              │
              └─> worker (python -m app.worker) picks up 'received' events
                    ├─ transform via mapping.yaml
                    ├─ push to destination adapter
                    ├─ success → status='delivered'
                    └─ failure → retry 1m · 5m · 15m · 1h · 6h
                                 └─ exhausted / permanent → status='dead' → Slack alert
```

Three principles, in priority order:

1. **Durable first, process second.** The webhook handler only verifies,
   writes one row, and returns 200. Everything else happens in a separate
   worker reading from the `events` table, so a restart loses nothing.
2. **Config in env vars and one mapping file.** No database-backed settings.
   A `.env` plus `mapping.yaml` fully describe a deployment.
3. **Fail closed, always.** No dev-mode bypasses. Missing secret, missing DB,
   malformed mapping, missing destination credentials → the process refuses
   to start.

---

## Fork and deploy in 30 minutes

### 0. You need

- Python 3.11+
- A Postgres database (a free Supabase project is fine)
- A Render account
- The destination's credentials (see `.env.example` for exactly where each one comes from)
- Admin access to the client's Shopify store

### 1. Clone and sanity-check

```bash
git clone <your-fork> sia-kit-<client>
cd sia-kit-<client>
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest -q                                            # ~180 tests, no network, <5s
```

### 2. Create the database

Create a Supabase project (or any Postgres). Copy the connection URI
(Supabase: **Connect → Session pooler**, URI form, replace the password), then:

```bash
SUPABASE_DB_URL='postgresql://...' python scripts/apply_migrations.py
```

That creates the `events` table. Re-running is safe.

### 3. Set the environment

```bash
cp .env.example .env
```

Fill in the **required four** plus **one destination block**:

| Variable | Where it comes from |
|---|---|
| `SUPABASE_DB_URL` | Step 2 |
| `SHOPIFY_WEBHOOK_SECRET` | Shopify admin → Settings → Notifications → Webhooks → bottom of page ("signed with") |
| `DESTINATION` | `notion` \| `monday` \| `hubspot` \| `slack` \| `sheets` |
| `ADMIN_TOKEN` | `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `NOTION_API_KEY` + `NOTION_DATABASE_ID` (or the equivalent pair for your destination) | `.env.example` has per-destination instructions |
| `ALERT_WEBHOOK_URL` | Slack → Incoming Webhooks. Not required, but you want it. |
| `BASE_URL` | The Render URL once you have it; used in alert links |

Every required variable is checked at startup; a missing one names itself in
the boot error.

### 4. Edit `mapping.yaml`

This is the one file that changes per engagement. Set `destination:` to match
`DESTINATION`, then describe each field:

```yaml
fields:
  - from: "$.total_price"        # JSONPath into the Shopify payload
    to: "Total"                  # the destination's field name, exactly as shown there
    type: number
```

`to:` must match the destination **by display name** — a Notion property, a
Monday column title, a Sheets header cell, a HubSpot property internal name.
Adapters resolve names by introspecting the destination, so create the
columns first. The file is fully commented; the schema reference is the
docstring of `src/app/mapping.py`.

Validate without deploying:

```bash
python -c "from app.mapping import load_mapping; print(load_mapping())"
```

A malformed mapping fails the deploy, not the first order.

### 5. Smoke it locally

Terminal 1 — the API:

```bash
uvicorn app.main:app --port 8000
```

Terminal 2 — send a signed test order, then drain the queue once:

```bash
python scripts/send_test_webhook.py http://localhost:8000
python -m app.worker --once
```

The record should now exist in the destination. If not, the worker log
line says why, and `curl -H "Authorization: Bearer $ADMIN_TOKEN"
localhost:8000/events?status=dead` shows the stored error.

### 6. Deploy to Render

`render.yaml` is a Blueprint: **Render → New → Blueprint → your fork**. It
creates the web service, the worker, and an env group named
`sia-kit-secrets`. Fill the group with the same values as your `.env`
(`ENVIRONMENT=production` is set by the Blueprint). Deploy.

```bash
curl https://<service>.onrender.com/health
```

You want `"status": "ok"` with `"checks": {"database": true, "destination": true}`.

**Cheaper shape:** delete the `worker` block in `render.yaml` and uncomment
the `cron` block — it runs `python -m app.worker --once` every 5 minutes for
pennies, at the cost of up to 5 minutes of delivery latency.

### 7. Register the Shopify webhook

Shopify admin → **Settings → Notifications → Webhooks → Create webhook**:

- Event: **Order creation** (or whichever topic `mapping.yaml` names)
- Format: **JSON**
- URL: `https://<service>.onrender.com/webhooks/shopify/orders/create`
- API version: latest stable

Confirm `SHOPIFY_WEBHOOK_SECRET` on Render matches the "signed with" value on
that page — it is per store, not per webhook. Click **Send test
notification** on the new webhook; the response must be 200.

### 8. Verify with a test order

In the store, place a test order (Shopify's Bogus Gateway or a 100% discount
code), then:

```bash
curl -H "Authorization: Bearer $ADMIN_TOKEN" https://<service>.onrender.com/events?limit=3
```

The newest event should be `delivered` within seconds (always-on worker) or
one cron interval, with `external_id` set to the destination's record id —
and the record visible in the destination. Done. Hand the client
`RUNBOOK.md`.

---

## Operating a deployment

| Surface | What it is for |
|---|---|
| `GET /health` | Public. DB check, destination check (cached 60s), event counts, `dead_events`, `last_delivered_at`. 503 only when the database is unreachable. Point the retainer's uptime monitor here. |
| `GET /events?status=dead&limit=50` | Bearer `ADMIN_TOKEN`. Newest first, full payload and `last_error`. |
| `GET /events/{id}` | Same auth. One event. |
| `POST /events/{id}/replay` | Same auth. Resets a `dead` event to `received` with a fresh retry budget. Fix the cause first. |
| Slack alert | One message per dead-lettered event with the error and the replay command. |
| Logs | One JSON line per event transition: `event_id`, `webhook_id`, `status`, `attempts`, `duration_ms`. `LOG_FORMAT=text` for local reading. |

**Retry policy.** Transient failures (destination 5xx, 408/429, timeouts,
connection errors) retry at 1m, 5m, 15m, 1h, 6h (±20% jitter), then `dead`.
Permanent failures (other 4xx, a value that cannot be coerced, a field the
mapping requires but the payload lacks) go to `dead` immediately — retrying
would not help, and you get the alert right away.

**Dedupe.** Shopify redelivers on any non-200. `(source, webhook_id)` is
unique in the table, so a redelivery is absorbed while the original row keeps
its own retry state. Nothing is double-delivered.

**Rotating a credential.** Update the Render env var → redeploy → replay any
dead events from the outage. Renaming a column in the destination without
updating `mapping.yaml` dead-letters every event with a clear "no property
named X" error until you fix one or the other.

---

## Repository map

```
mapping.yaml                 the per-engagement transform (the file you edit)
migrations/001_create_events.sql
src/app/
  main.py                    FastAPI app: 3 routers + startup checks
  worker.py                  claim → transform → deliver → retry/dead
  pipeline.py                Pipeline(transform, deliver) + failure types
  mapping.py                 mapping.yaml schema, path resolution, transforms
  config.py                  Settings + REQUIRED_AT_STARTUP
  db.py                      asyncpg pool
  log.py                     JSON logging
  models/event.py            the one persisted shape
  webhooks/shopify.py        verify → insert → 200
  services/webhook_signature.py   HMAC verifiers + the route dependency
  services/events.py         EventStore (all SQL) — Postgres impl
  services/notifications.py  dead-letter alert → Slack webhook
  routers/health.py, events.py
  adapters/                  base.py (Destination Protocol + record contract), registry.py,
                             monday.py, hubspot.py, notion.py, slack.py, sheets.py
scripts/apply_migrations.py, send_test_webhook.py
tests/                       ~190 tests; adapters via httpx.MockTransport; DB tests run in CI
```

## Adding a destination

Implement the two-method `Destination` Protocol in `src/app/adapters/base.py`
(read the record contract there — display-name keys, `_name`/`_key`/
`_line_items`), read credentials in `__init__` with `require_env`, raise
`PermanentDeliveryError`/`TransientDeliveryError` for in-body errors and let
`httpx.HTTPStatusError` propagate for HTTP ones, register the class in
`adapters/registry.py`, and test it with `httpx.MockTransport` like the
others. `tests/adapters/test_registry.py` will check Protocol conformance.

## Tests and CI

```bash
ruff check .
pytest -q                     # unit suite, no network
TEST_DB_URL=postgresql://... pytest -q   # also runs the Postgres-backed store tests
```

CI (`.github/workflows/ci.yml`) applies the migrations to a fresh Postgres,
validates `mapping.yaml`, lints, and runs everything.
