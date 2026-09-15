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
