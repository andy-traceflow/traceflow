# MIGRATION.md — TraceFlow → SIA Kit

Running audit log of the conversion from the multi-tenant TraceFlow SaaS to the
single-tenant SIA Kit integration template. One line per file deleted, moved, or
rewritten, with the reason. Appended phase by phase; each phase ends in one commit.

Branch: `sia-kit/migration` (forked from `main` @ `7889fe2`, the ADR-0006 checkpoint).

---

## Phase 0 — Inventory and safety (2026-09-15)

- Started on `main` with a dirty working tree (uncommitted ADR-0006 portal/onboarding
  wave, already live on prod). Per approval: checkpoint-committed it to `main` as
  `7889fe2`, then branched `sia-kit/migration`. No code changes in that commit.
- Baseline test run (`pytest tests/ -q`): **555 passed, 50 skipped, 0 failed, 6.94s.**
  All 50 skips are `TRACEFLOW_TEST_DB_URL not set` (RLS isolation + portal DB suites).
- Tracked files before Phase 1: 218.

---

## Phase 1 — Deletion

Legend: **D** deleted · **M** moved · **R** rewritten · **E** edited (minimal repair so
`app.main` still imports; the real rewrite happens in the phase noted) · **+** added.

### Multi-tenancy
| Op | Path | Reason |
|---|---|---|
| D | `src/app/middleware/tenant_resolver.py` | Path/payload → `client_id` resolution; no tenants |
| D | `src/app/models/client.py` | Tenant record |
| D | `src/app/models/client_config.py` | Per-tenant runtime config; config is env + `mapping.yaml` now |
| D | `migrations/001–026` (all 26 files) | Every file is tenant-shaped (RLS policies, `client_id` columns, tables for the retired domain). Editing them yields a schema for tables that no longer exist. Phase 3a writes a fresh `001_create_events.sql`; each fork gets a fresh DB |
| E | `src/app/db.py` | Removed `_current_tenant` ContextVar, `set_current_tenant/get_current_tenant`, `set_tenant_context()`, the `_demo` ContextVar + `set_demo/get_demo`, and `get_service_connection()` (demo/RLS-bypass split is meaningless single-tenant). `get_connection()` no longer does `SET ROLE authenticated` — no RLS to activate |
| D | `tests/test_tenant_isolation.py`, `tests/test_tenant_resolver.py` | Tests for deleted machinery |
| D | `tests/sql/bootstrap_supabase_stubs.sql` | Stubbed Supabase `auth` schema so RLS migrations could apply in CI |
| E | `tests/conftest.py` | Removed `client_a_id` / `client_b_id` fixtures; kept `db_url` + DSN env shim for Phase 7 DB tests |

### Retired domain (lead recovery / SMS / AI qualification)
| Op | Path | Reason |
|---|---|---|
| D | `src/app/prompts/` (package) | Greeting/intent/qualifier/summarize/context prompts — no AI in SIA Kit |
| D | `src/app/services/{qualification,classification,spam,sms,owner_alert,phone,ai,contacts}.py` | Lead-recovery pipeline |
| D | `src/app/webhooks/twilio.py`, `src/app/services/twilio_signature.py` | Twilio missed-call/SMS intake |
| D | `src/app/models/{lead,qualification,message,contact,crm_contact}.py` | Lead-shaped domain models |
| D | `src/app/models/event.py` | Old debug event stream; replaced wholesale by the durable `events` table in Phase 3a |
| D | `src/app/jobs/{daily_digest,monthly_report,revenue_sync}.py` | Per-tenant cron reports |
| D | `src/app/jobs/adapter_health.py` | "Ping every active client's adapter" cron; superseded by `/health` in Phase 6 |
| D | `src/app/webhooks/crm.py` | Stub, route `/webhooks/crm/{provider}/{client_id}` |
| D | `src/app/webhooks/generic.py` | Route `/webhooks/generic/{client_id}/{slug}`; `jsonpath_ng` usage noted for Phase 5 |
| D | `src/app/services/audit.py` | Writes `client_id`-scoped `audit_log` (migration 006) |
| D | `src/app/services/{provisioning,onboarding_mapping}.py` | Tenant provisioning — forking replaces it |
| D | `tests/prompts/` (5 files), `tests/jobs/` (3), `tests/services/{test_admin_auth,test_calculator,test_classification,test_contact_resolver,test_contacts,test_owner_alert,test_phone,test_qualification,test_spam}.py`, `tests/webhooks/test_twilio_crm_push.py`, `tests/{test_twilio,test_lead_model,test_golden_conversations,test_generic_webhook,test_onboarding_promote,test_portal_provisioning}.py`, `tests/fixtures/conversations/` | Tests/fixtures for deleted modules |

### Founder / marketing surfaces
| Op | Path | Reason |
|---|---|---|
| D | `src/app/routers/admin/` (package) | Founder console API |
| D | `src/app/services/{admin_auth,permissions}.py` | Admin JWT auth + role permissions |
| D | `src/app/middleware/auth.py`, `src/app/middleware/portal_auth.py` | Supabase-JWT → tenant resolution for the client portal; Phase 6 auth is one bearer token |
| D | `admin-ui/` (directory), `src/app/static/admin/` (built bundle) | Admin SPA |
| D | `src/app/routers/{calculator,kb,kb_export,demo}.py`, `src/app/services/calculator.py`, `src/app/models/kb.py`, `src/app/demo/` | ROI calculator, KB export, in-memory demo |
| D | `scripts/{create_admin,dev_admin_preview,export_contacts,onboard_client}.py`, `tests/scripts/` | Admin/tenant tooling. Kept `apply_migrations.py`, `inspect_monday_board.py` |
| D | `tests/{test_admin,test_demo,test_allowed_hosts,test_portal_auth}.py` | Tests for deleted surfaces |
| D | `docs/` (entire directory: PRD, architecture, playbooks, ADRs 0001–0006, changelog, timeline, retool/admin-ui notes) | All describe the retired business; git history retains them |
| D | `.claude/skills/{client-onboarding,content-creation,context-sync,marketing-copy,multi-tenant-arch,prompt-engineering}/` | Retired-business skills. Kept `adapter-pattern` + `fastapi-supabase` for the Phase 6 rewrite alongside `CLAUDE.md` |
| E | `.claude/launch.json` | Pointed at deleted `dev_admin_preview.py`; now launches uvicorn directly |

### Adapters (Phase 4 rewrites; Phase 1 only removes GHL)
| Op | Path | Reason |
|---|---|---|
| D | `src/app/adapters/ghl.py`, `tests/adapters/test_ghl_adapter.py` | GoHighLevel tied to the retired affiliate model |
| E | `src/app/adapters/registry.py` | Dropped GHL import/entry only |
| — | `src/app/adapters/{base,monday,hubspot}.py` + their tests | **Left importing deleted models on purpose** — they are Phase 4's inputs. `tests/adapters/test_{monday,hubspot}_adapter.py` fail at collection until Phase 4 |
| — | `src/app/services/notifications.py` | Imports deleted `ClientConfig`; nothing imports it; Phase 6 rewrites |

### Wiring / config / infra (minimal repair)
| Op | Path | Reason |
|---|---|---|
| R | `src/app/main.py` | Now wires exactly `shopify.router` + `health.router`. Removed tenant middleware, CORS (served the SPA), static mounts, admin/kb/calculator/demo/crm/generic/twilio routers, PyJWT diagnostics. Kept lifespan (Sentry + pool) and TrustedHost |
| + | `src/app/routers/health.py` | Liveness stub extracted from `main.py`; Phase 6 adds DB/destination/dead-count/last-delivery |
| E | `src/app/webhooks/shopify.py` | Removed tenant/lead/dedupe imports, `_process_order`, `_shopify_order_to_lead`, `BackgroundTasks`. Route changed to `/{topic:path}` (the `client_id` segment had no meaning). Kept `_resolve_*` helpers, JSON-error→200, cached-body read. Handler body is a placeholder until Phase 3c. **Signature verification is unwired between Phase 1 and Phase 2** — the branch is not deployable until Phase 2 lands |
| E | `src/app/models/__init__.py` | Re-exported every deleted model; now a docstring only |
| E | `src/app/__init__.py` | Docstring described TraceFlow; package name `app` unchanged |
| E | `src/app/services/webhook_signature.py` | Removed the Twilio import, `_verify_twilio_request`, and the Twilio dispatch branch only. Dispatcher / dev bypass / `_load_signing_secret` untouched — Phase 2 |
| E | `src/app/config.py` | Removed settings whose consumers are gone: Supabase REST keys, Anthropic/OpenAI, Twilio, Resend, `admin_jwt_secret`, `admin_login_rate_limit_enabled`, `allowed_origins`, `demo_mode`. Kept `environment`, `log_level`, `base_url`, `supabase_db_url`, `allowed_hosts`, `sentry_dsn` |
| E | `pyproject.toml` | Dropped deps with no remaining importer: `python-multipart`, `supabase`, `jinja2`, `pyjwt`, `cryptography`, `bcrypt`, `anthropic`, `tzdata`. Description updated |
| E | `render.yaml` | Removed the 4 cron services (deleted modules), `DEMO_MODE`, and retired secrets from the env group. Web service kept; Phase 3d adds the worker |
| E | `.github/workflows/ci.yml` | `pgvector` image → `postgres:16`; removed the Supabase-stub bootstrap step and the `pyyaml` install for the deleted onboarding script. Migration loop kept (applies nothing until Phase 3a) |

### Phase 1 result
- Tracked files: 218 → 46 (45 survivors + this file).
- `python -c "import app.main"` → OK. Routes: `POST /webhooks/shopify/{topic:path}`, `GET /health`.
- `ruff check .` → clean.
- `pytest tests/ -q --ignore=tests/adapters` → **33 passed, 0 skipped, 0.55s**
  (`test_webhook_signature` 14 · `test_dedupe` 7 · `test_field_mappings` 12).
- `pytest tests/ -q` (full) → **2 collection errors**, both `tests/adapters/test_{monday,hubspot}_adapter.py`
  importing deleted `app.models.*`. Intentional — those adapters and their tests are rewritten in Phase 4.
  The suite is red at this commit; it goes green again at the Phase 4 commit.
- Branch is **not deployable** at this commit: signature verification is unwired until Phase 2, and
  the webhook handler persists nothing until Phase 3c.

---

## Phase 2 — Preserve and harden the signature layer

The three pure verifiers (`verify_hmac_sha256_base64`, `verify_hmac_sha256_hex`,
`verify_timestamped_signature`) and `parse_signature_header` are **byte-for-byte unchanged**,
including `hmac.compare_digest` and the injectable `now`. Only the request-level dispatcher
was rewritten.

| Op | Path | Reason |
|---|---|---|
| R | `src/app/services/webhook_signature.py` | Dispatcher `verify_signature_for_request(request, client_id)` → FastAPI dependency `verify_shopify_signature(request) -> bytes`. **Deleted** `_load_signing_secret()` (HTTP round-trip to Supabase REST per webhook), the `ENVIRONMENT`-gated dev bypass, and `_infer_integration()` path sniffing. Secret is `SHOPIFY_WEBHOOK_SECRET` from env. Fail-closed contract: no secret → 500, no header → 401, mismatch → 401 — no environment skips it. Kept `_read_and_cache_body()`; the dependency returns the verified bytes so the handler never re-reads the stream. Dropped `httpx`/`uuid` imports |
| E | `src/app/middleware/signature_verify.py` | Re-export shim updated to the new surface (`verify_shopify_signature`, `SHOPIFY_HMAC_HEADER`) |
| E | `src/app/config.py` | Added `shopify_webhook_secret`; added `REQUIRED_AT_STARTUP` + `require_startup_settings()` which raises `RuntimeError` naming every missing fail-closed var |
| E | `src/app/main.py` | Lifespan calls `require_startup_settings()` first — a deploy without the secret refuses to boot |
| E | `src/app/webhooks/shopify.py` | Route declares `body: bytes = Depends(verify_shopify_signature)`; the `getattr(request.state, ...)` fallback read is gone (the dependency is the only body reader) |
| + | `tests/test_shopify_signature_dependency.py` | 12 route/lifespan tests: valid, tampered, wrong secret, missing/empty header, missing secret fails closed per-request and across every `ENVIRONMENT` value, bad-JSON 200 shortcut unreachable without a valid HMAC, startup gate raises and names the var, lifespan refuses to start |
| E | `.env.example`, `render.yaml` | Added `SHOPIFY_WEBHOOK_SECRET` (marked required). `.env.example` is otherwise still the old file — full rewrite is Phase 6 |

### Phase 2 result
- `diff` of the four pure-verifier function bodies, Phase-1 commit vs. working tree → **identical**.
- `ruff check .` → clean.
- `pytest tests/ -q --ignore=tests/adapters` → **45 passed, 0.91s** (33 carried + 12 new).
- Full `pytest` still has the 2 intentional adapter collection errors (Phase 4).
- The webhook route is now safe to expose: HMAC is enforced with no bypass. Still **not
  deployable** as a product — nothing is persisted until Phase 3c.

---

## Phase 3 — Replace the event pipeline

Durable first, process second. The handler's only job is verify → insert → 200; the worker
reads from the `events` table. Nothing runs in a `BackgroundTask` anymore.

| Op | Path | Reason |
|---|---|---|
| + | `migrations/001_create_events.sql` | The durable event store, per the Phase 3a schema plus one column: `shop_domain` (the spec asks to capture `X-Shopify-Shop-Domain` into the row and the given DDL had nowhere to put it). Adds a `CHECK` on `status`. Documents the dual meaning of `next_retry_at` (backoff when `received`, claim lease when `processing`). Wrapped in `BEGIN/COMMIT` as `scripts/apply_migrations.py` expects |
| D | `src/app/services/dedupe.py`, `tests/test_dedupe.py` | In-memory, per-process, TTL-based; wiped by restarts; and recorded the id *before* processing so a crash mid-process suppressed Shopify's retry forever. Replaced by `INSERT … ON CONFLICT (source, webhook_id) DO NOTHING RETURNING id` |
| + | `src/app/models/event.py` | `Event` (Pydantic) + `EventStatus` — the one persisted shape |
| + | `src/app/services/events.py` | `EventStore` Protocol + `PostgresEventStore`. All `events` SQL lives here. `claim()` is one `UPDATE … WHERE id IN (SELECT … FOR UPDATE SKIP LOCKED) RETURNING *` and takes a lease. Injected into the route via `Depends(get_event_store)` so tests override it |
| R | `src/app/webhooks/shopify.py` | Handler is exactly verify → `store.insert()` → 200. Captures `X-Shopify-Webhook-Id`, `X-Shopify-Topic` (authoritative; path segment is the fallback), `X-Shopify-Shop-Domain`. Duplicate → 200. **Insert failure → 503, not 200** — if the row cannot be written, Shopify must retry. A missing webhook id (never from real Shopify, but the request passed HMAC) falls back to `sha256:<body>` so it still dedupes. `_resolve_*` helpers unchanged |
| + | `src/app/pipeline.py` | `Pipeline(transform, deliver)` + the failure vocabulary adapters raise: `PermanentDeliveryError`, `TransientDeliveryError`, `TransformError`. `passthrough` transform. `build_pipeline()` raises until Phase 4 (registry) and Phase 5 (mapping) exist |
| + | `src/app/worker.py` | The delivery worker. `backoff_seconds()` 1m/5m/15m/1h/6h ±20% jitter; `classify_failure()` — 4xx (except 408/429), `ValueError`/Pydantic `ValidationError`, transform errors → permanent → straight to `dead`; 5xx/408/429/timeouts/transport errors/unknown → transient; 5th transient failure → `dead` + alert. `process_one()` never raises for delivery failures. `run_once()` drains the due set; `run_forever()` polls. CLI: `python -m app.worker` / `--once` / `--batch-size` / `--poll-interval`. Calls `require_startup_settings()` (fail closed on missing DB URL). Default alert sink is an ERROR log line — Phase 6 swaps in the Slack poster. Log lines already carry `event_id`, `webhook_id`, `status`, `attempts`, `duration_ms`; Phase 6 switches the formatter to JSON |
| E | `src/app/config.py` | `supabase_db_url` added to `REQUIRED_AT_STARTUP` (approved). A boot without a DB would 200 webhooks and persist nothing |
| E | `render.yaml` | Added `sia-kit-worker` as a Render background worker, with the cron `--once` alternative shown commented for cheap deployments |
| + | `tests/fakes.py` | `InMemoryEventStore` mirroring the Postgres semantics (dedupe, due set, lease) so worker/handler tests need no DB |
| + | `tests/test_shopify_webhook.py` | 8 handler tests: row captured with headers, same webhook id ×3 → one row, header topic beats path, path fallback, missing webhook id → body hash still dedupes, bad JSON → 200 and nothing stored, **store failure → 503**, bad signature never reaches the store |
| + | `tests/test_worker.py` | 25 tests: backoff schedule/jitter bounds/clamp, 16-case classification matrix, delivered path, transient → retry scheduled (the regression), not claimable until backoff elapses, redelivery absorbed while original stays retryable, permanent 4xx → dead with one alert and `attempts == 1`, transform error permanent, 5 transient → exactly one dead row + one alert, alert failure contained, lease reclaim, `run_once` drains and terminates |
| + | `tests/test_events_store_db.py` | 6 Postgres-backed tests (skip without `TRACEFLOW_TEST_DB_URL`; CI runs them): unique constraint, JSONB round-trip, two concurrent `claim()`s are disjoint and cover the set (`FOR UPDATE SKIP LOCKED`), failed delivery stays retryable + redelivery absorbed, lease expiry, terminal states never claimed |
| E | `tests/test_shopify_signature_dependency.py` | Fixture now also sets a dummy `SUPABASE_DB_URL` (newly required) and overrides the store with the in-memory fake, since accepted requests now persist |

### Phase 3 result
- `ruff check .` → clean. `python -m app.worker --help` → OK.
- `pytest tests/ -q --ignore=tests/adapters` → **76 passed, 6 skipped, 1.06s.** The 6 skips are
  `tests/test_events_store_db.py` (no `TRACEFLOW_TEST_DB_URL` on this machine — no local Postgres
  or Docker). CI applies `migrations/001_create_events.sql` and runs them.
- Full `pytest` still has the 2 intentional adapter collection errors (Phase 4).
- The receive side is now durable and deployable. The worker runs but `build_pipeline()` raises
  until Phase 4 supplies a destination — so at this commit events accumulate as `received` and
  nothing is delivered. That is the correct failure mode: nothing is lost.

---

## Phase 4 — Generalize the adapter layer

The lead-shaped `CRMAdapter` (push_lead / lookup_by_phone / fetch_recovered_value / config
parameter) becomes the two-method `Destination` Protocol. Adapters read their own env at
construction and fail closed; the record is a plain dict of destination display names.

| Op | Path | Reason |
|---|---|---|
| R | `src/app/adapters/base.py` | `Destination` Protocol exactly per spec (`name`, `upsert_record(record) -> str`, `health_check() -> bool`, `@runtime_checkable`). Documents the **record contract**: keys are display names; reserved `_name` / `_key` / `_line_items`. Helpers `require_env` (→ `AdapterConfigError`, a `ValueError`), `split_record`, `display_name`, `as_list` |
| R | `src/app/adapters/registry.py` | Same pattern (`register_adapter` / `get_adapter` / `list_providers`), now **lazy** construct-once: factories at import, instances on first `get_adapter()`. Import-time construction would have made importing the package fail without credentials. `reset_registry()` for tests. Note: the spec says registry tests "should survive" — there were none; `tests/adapters/test_registry.py` is new |
| R | `src/app/adapters/monday.py` | Ported. **Kept**: column ids resolved by display name on every call, `subtasks` → subitem-board discovery, `_serialize_column_value` verbatim, one subitem per `_line_items` entry, subitem failure does not abort the push. **Dropped**: `lookup_by_phone`, `fetch_recovered_value`, `LOOKUP_TIMEOUT` (2s SMS budget), `update_lead`, `parse_webhook`, `ClientConfig`/`field_mappings` coupling. Timeout 30s. **Added**: `_key` upsert via `items_page_by_column_values` → `change_multiple_column_values`; GraphQL errors (HTTP 200) mapped to `TransientDeliveryError` (complexity/rate-limit) or `PermanentDeliveryError`; HTTP errors now *raise* (`raise_for_status`) instead of returning `None`, so the worker can classify them. Env `MONDAY_API_KEY`, `MONDAY_BOARD_ID` |
| R | `src/app/adapters/hubspot.py` | Ported with the same treatment. Record keys are property internal names. `_key` → search → PATCH; HubSpot's own 409 (`Existing ID: n`) resolved to PATCH. Lists → `;`-joined (multi-checkbox), bools → `"true"/"false"`. Env `HUBSPOT_ACCESS_TOKEN`, `HUBSPOT_OBJECT` (default `contacts`) |
| D | `src/app/adapters/ghl.py` | (already deleted in Phase 1) |
| + | `src/app/adapters/notion.py` | New. Property types resolved by introspecting `GET /databases/{id}` (cached, refreshed on unknown property) — same "resolve by display name" idea as Monday. Encodes title, rich_text, number, select, multi_select, date, relation, email, phone_number, url, checkbox; title falls back to `_name`. `_key` → database query filter → PATCH. **429 handled in-adapter**: honors `Retry-After` up to 3 retries (injectable sleep), then `TransientDeliveryError`. Env `NOTION_API_KEY`, `NOTION_DATABASE_ID` |
| + | `src/app/adapters/slack.py` | New. `chat.postMessage` with Block Kit: header (`_name`), section fields chunked to Slack's 10-per-section limit, `_line_items` as a bullet list. Slack's in-body `ok:false` mapped: `ratelimited`/`internal_error` → transient, else permanent. Returns `ts`. Env `SLACK_BOT_TOKEN`, `SLACK_CHANNEL` |
| + | `src/app/adapters/sheets.py` | New. Columns resolved from the header row (row 1), cached + refreshed; row written in header order. Service-account auth: RS256 JWT assertion → access token, cached to expiry−60s (injectable `token_provider` for tests). Append-only: `_key`/`_line_items` ignored with a warning. Env `GOOGLE_SERVICE_ACCOUNT_JSON`, `GOOGLE_SHEET_ID`, `GOOGLE_SHEET_TAB` |
| E | `src/app/adapters/__init__.py` | Exports `Destination`, `AdapterConfigError`, `get_adapter`, `register_adapter`, `list_providers` |
| E | `src/app/pipeline.py` | `build_pipeline()` now resolves `DESTINATION` through the registry (`deliver = adapter.upsert_record`). Imports the registry locally so `adapters → pipeline` stays one-way |
| E | `src/app/config.py` | Added `destination`; added to `REQUIRED_AT_STARTUP` |
| E | `src/app/main.py`, `src/app/worker.py` | Both construct the adapter at boot (`get_adapter` / `build_pipeline` before `asyncio.run`) so missing credentials refuse the process rather than dead-lettering the first event |
| E | `pyproject.toml` | `pyjwt[crypto]` re-added (removed in Phase 1) — RS256 assertion for the Sheets service account |
| E | `render.yaml`, `.env.example` | `DESTINATION` + all adapter vars added to the env group / example (with where-to-get-it comments) |
| R | `tests/adapters/test_monday_adapter.py`, `test_hubspot_adapter.py` | Rewritten — every old test was lead-shaped. Now `httpx.MockTransport` end-to-end: 12 Monday (column resolution, subitems + subitem-board discovery, subitem failure tolerated, `_key` update path, complexity→transient, other→permanent, HTTP propagates, board-not-found, health, serialize, headers/timeout) + 7 HubSpot (flat properties + serialization, `_key` search→PATCH, 409→PATCH, 4xx propagates, empty record permanent, object type from env, health) |
| + | `tests/adapters/conftest.py`, `test_registry.py`, `test_notion_adapter.py`, `test_slack_adapter.py`, `test_sheets_adapter.py`, `tests/test_pipeline.py` | Recording `MockTransport`; registry (list, unknown, missing creds ×5, construct-once, replace, Protocol conformance ×5, helpers); Notion 11 (every type encoded, schema cached/refreshed, title precedence, nothing-matches permanent, bad number permanent, `_key` hit/miss with typed filters, 429 retried honoring Retry-After, persistent 429 transient, 4xx propagates, health); Slack 6 (blocks shape incl. line items, 10-field chunking, ratelimited transient, channel_not_found permanent, 5xx propagates, health); Sheets 7 (header-order row with blanks, header cached/refreshed, no header permanent, cells, bad JSON config, **real RS256 token mint verified against the public key + cached**, health); pipeline 4 |
| E | `tests/test_shopify_signature_dependency.py`, `tests/test_shopify_webhook.py` | Fixtures set `DESTINATION` (newly required) |

### Phase 4 result
- `ruff check .` → clean.
- `pytest tests/ -q` (**full suite, no `--ignore`**) → **138 passed, 6 skipped, 2.67s.** The suite is
  green at every commit from here on; the 6 skips remain the Postgres-backed store tests (CI).
- No network: every adapter test runs through `httpx.MockTransport`.
- The branch is now functional end-to-end: webhook → `events` row → worker → any of five
  destinations. The transform is still `passthrough` (raw Shopify payload keys as the record) —
  Phase 5 supplies `mapping.yaml`.

---

## Phase 5 — Replace DB-backed field mappings with a file

| Op | Path | Reason |
|---|---|---|
| D | `src/app/services/field_mappings.py` | Read `client_field_mappings` rows per tenant (table already gone with the migrations). **Carried over verbatim** into `app.mapping.apply_transform`: the dict-form transform vocabulary (`value_map`, `regex_replace`, `numeric_scale`, `concatenate`, `split`, unknown → warn + pass through). **Dropped**: `resolve_mappings` (DB), `FieldMapping` dataclass, `dotted_qualification_data` (flattened lead qualification fields — retired domain; the *idea* survives as dotted `from:` paths), `apply_inverse_transform` (only served the deleted `parse_webhook` paths) |
| D | `tests/services/test_field_mappings.py`, `tests/services/` | 12 tests; the 9 that cover surviving behavior are re-homed in `tests/test_mapping.py` (the 3 inverse-transform tests go with the function). Package dir was otherwise empty |
| + | `mapping.yaml` | Repo-root template per the spec, **plus** `key:` (approved — names the `to` field to upsert on; becomes `_key`) and `name:` (path for `_name`). Heavily commented — it is the file a fork edits |
| + | `src/app/mapping.py` | Pydantic v2 schema (`Mapping` / `FieldSpec` / `LineItemsSpec`, `extra="forbid"`) validated at startup; `load_mapping()` raises `MappingError` with a readable per-field message on missing file, bad YAML, non-object root, unknown keys, bad `type`, unknown transform, bad JSONPath, duplicate `to`, `key` not naming a field. `resolve_path()`: JSONPath via `jsonpath_ng` (kept) — 0 matches → None, 1 → value, n → list — or dotted paths. Named transforms (`to_decimal`, `to_int`, `to_str`, `strip`, `upper`, `lower`, `title_case`, `digits_only`, `to_bool`, `to_date`, `join`, `first`; chainable as a list) alongside the dict form. `coerce()` by declared `type`; failure → `TransformError` (permanent). `build_record()` → adapter record with `_name` / `_key` / `_line_items`; empty fields are omitted; `required: true` → `TransformError`. `transform_for()` guards source/topic — an event for a topic the mapping doesn't handle dead-letters with an alert naming both, rather than silently producing garbage |
| E | `src/app/pipeline.py` | `build_pipeline()` loads the mapping, cross-checks `mapping.destination == DESTINATION` (RuntimeError if not), then wires `transform_for(mapping)` + `adapter.upsert_record` |
| E | `src/app/main.py` | Lifespan calls `build_pipeline()` as the startup check (replaces the bare `get_adapter`) — a malformed mapping now fails the web deploy too, not just the worker |
| E | `src/app/config.py` | `mapping_path` (optional; default `mapping.yaml` in CWD, then repo root) |
| E | `pyproject.toml` | `pyyaml` added; `types-pyyaml` in dev |
| E | `Dockerfile` | Copies `mapping.yaml` into the image — it only copied `src/`, so a Docker deploy would have failed at boot |
| E | `.env.example` | `MAPPING_PATH`; `DESTINATION` comment notes it must match the file |
| + | `tests/fixtures/shopify_order.json`, `tests/fixtures/mapping.yaml` | Realistic `orders/create` payload (empty shipping company, populated billing company, null variant, null note) and a mapping exercising every schema feature |
| + | `tests/test_mapping.py` | 45 tests: fixture → exact expected record incl. `fallback:`, `default:`, dotted path, chained transforms, dict-form transforms, `_line_items` with per-item `_name`, omitted empties; fallback only when `from` empty; **repo-root `mapping.yaml` loads and maps the fixture**; `transform_for` topic/source guard; required-missing and uncoercible → `TransformError`; 14 invalid-mapping cases fail loudly; `resolve_path` JSONPath/dotted; the 9 carried-over `apply_transform` tests + 15 named-transform cases + chaining |
| R | `tests/test_pipeline.py` | Now covers mapping loading, destination cross-check, and transform → deliver through a registered fake adapter |

### Phase 5 result
- `ruff check .` → clean.
- `pytest tests/ -q` → **179 passed, 6 skipped, 2.98s** (6 = CI-only Postgres tests). No network.
- Configuration is now fully env vars + `mapping.yaml`. No `client_configs`, no `field_mappings`,
  no `client_webhook_configs` — and no code path reads configuration from the database.
