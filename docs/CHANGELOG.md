# CHANGELOG

> Append-only log of significant decisions, builds, and milestones for TraceFlow. Newest entries at the top. Each entry: date, type, summary, links.

**Types:** `decision` | `build` | `milestone` | `pivot` | `pause` | `learning`

---

## 2026-05-14 — Production accounts provisioned (in progress)

### build: GitHub account + private repo live
- **Account:** `andy-traceflow` on github.com, email `andy@traceflow.app`, 2FA enabled (authenticator app), recovery codes saved in 1Password
- **Repo:** [github.com/andy-traceflow/traceflow](https://github.com/andy-traceflow/traceflow) (private)
- **Initial commit:** `4929213` — 86 files (platform skeleton + docs + CI)
- **Local git config:** per-repo `user.name=Andy`, `user.email=andy@traceflow.app`. Global identity (`hiandysuarez`) untouched so personal projects keep their author.
- **CI:** first run triggered automatically on push. **Result NOT confirmed yet** — verify next session at github.com/andy-traceflow/traceflow/actions.
- **Status:** ✅ done.

### build: Supabase account + project provisioned
- **Project:** `traceflow` at `https://ienjxmyhttuzxoaeramo.supabase.co` (project ref: `ienjxmyhttuzxoaeramo`)
- **Region:** West US
- **pgvector:** extension enabled (required by migration 005 for KB embeddings)
- **Pricing:** Free tier for now. **Must upgrade to Pro ($25/mo) before client #1** — Free tier sleeps after 1 week of inactivity which kills production reliability.
- **Status:** project provisioned. **Pending next session:**
  - Enable 2FA on the Supabase account (user needs to install an authenticator app first)
  - Paste DB URL with password so migrations can be applied
  - Capture service role key + anon key for Render env vars

### learning: Claude Desktop sandbox redirects %APPDATA% on Windows
- **What:** While debugging "why does my gh CLI session see a different account than Andy's terminal," discovered Claude Desktop runs in a Windows UWP/MSIX sandbox that redirects `%APPDATA%` to `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\`. The two environments physically read different `hosts.yml` files.
- **Implication:** Any tool that stores credentials in `%APPDATA%` (gh CLI, possibly others) cannot share state between Claude Desktop's tool sessions and the user's PowerShell. Auth-requiring commands (`gh repo create`, `git push`, anything needing a personal token) must be run from the user's terminal directly.
- **Workaround pattern:** I do all local-only operations (file writes, `git init`, `git commit`, schema design). User runs the auth-requiring commands. We coordinate via copy-pasted outputs.
- **What to consider for future:** SSH-key-based git auth where the sandbox owns its own key pair would let me push directly. Personal Access Tokens passed via `GH_TOKEN` env var would also work. Both have tradeoffs. Decision deferred until friction warrants it.

### What's next session
1. Confirm GitHub CI passed (or fix it)
2. Enable Supabase 2FA
3. Apply the 9 migrations via asyncpg from my side (need DB URL)
4. Capture Supabase API keys
5. Render account + service + cron + env vars
6. End-to-end /health smoke test

---

## 2026-05-20 — Render blueprint readied, account handoff queued

### build: render.yaml hardened for blueprint provisioning
- **What:** Patched `render.yaml` so it deploys cleanly as a Render Blueprint without ad-hoc dashboard tweaks. Cron `adapter-health-check` was missing `ENVIRONMENT=production` (would have fallen back to `development` per `config.py`'s default). Added explicit `PYTHON_VERSION=3.12` to both services to match the Dockerfile and immunize against future Render default drift.
- **Structure unchanged:** 1 web service (`traceflow-api`, Oregon, Starter) + 1 cron (`adapter-health-check`, hourly) + 1 env var group (`traceflow-secrets`, all `sync: false` so values are dashboard-entered).
- **No Postgres on Render** — Supabase is the database. Render only hosts FastAPI + the health-check cron.

### decision: TraceFlow gets its own Render account on andy@traceflow.app
- **Why:** Matches the GitHub + Supabase identity separation pattern. Keeps TraceFlow billing/identity isolated from personal projects (`hsuarez.m4kr@gmail.com` workspace currently holds suspended Midas + Flux services).
- **Implication:** Render MCP from this session is tied to the personal workspace and **cannot** provision into the new account. Same handoff pattern as GitHub last session — Andy clicks through account creation + Blueprint, I prep the YAML.

### decision: Skip real env var values during Blueprint creation
- **Why:** Supabase API keys + DB URL still pending (waiting on Andy's 2FA setup → service key capture). Creating the Render Blueprint now with placeholder values lets us establish the service structure without blocking on Supabase work.
- **Trade-off:** First deploy will fail at startup (asyncpg can't connect with placeholder DSN) and the service will sit red until real values land. Build phase still succeeds (just `pip install -e .`), so this only blocks runtime, not provisioning.

### Browser handoff to Andy (in order)
1. Sign up at render.com using `andy@traceflow.app`, verify email
2. Enable TOTP 2FA, save recovery codes to 1Password
3. Account Settings → Connect GitHub → authorize Render app on **`andy-traceflow` GitHub account only**, scope to `traceflow` repo (not "all repos")
4. New → Blueprint → select `andy-traceflow/traceflow` @ `main` → Render auto-reads `render.yaml`
5. When prompted for env var group values, paste any non-empty placeholder (e.g. `PENDING`) into every field — values get replaced next session
6. Do NOT add custom domain yet — use `traceflow-api.onrender.com` for smoke tests until DNS work

### fix: first Render build failed on non-existent `types-jsonpath-ng` stub
- **Symptom:** `Because traceflow depends on types-jsonpath-ng (*) which doesn't match any versions, version solving failed.`
- **Root cause:** `types-jsonpath-ng` was added to `[project.optional-dependencies] dev` on speculation that mypy stubs existed for `jsonpath-ng`. They don't — PyPI returns 404 for the package. It was never published.
- **Surprise:** the failing dep was in the *optional* `dev` group, yet `buildCommand: pip install -e .` (main deps only) still failed. Render's Python buildpack resolves *all* groups during its lock pass to validate the dependency graph — even groups that aren't installed. (The resolver error message style initially looked like uv, but the second build failure proved it's actually Poetry; see below.)
- **Fix:** removed `types-jsonpath-ng` from `pyproject.toml`. Added `[[tool.mypy.overrides]] module = "jsonpath_ng.*"` so mypy treats the runtime library as untyped without warnings.
- **Lesson:** keep optional dependency groups clean enough to *resolve*, not just clean enough to *install*. The two are not the same on Render's Python buildpack.

### fix: second Render build failed because Poetry tries to install the root project
- **Symptom:** `Installing the current project: traceflow (0.1.0) — Error: The current project could not be installed: No file/folder found for package traceflow`.
- **Root cause:** Render's Python buildpack runs `poetry install` as a pre-step regardless of `[build-system] build-backend = "setuptools.build_meta"`. Poetry's default behavior is to install the project package itself, looking for a `traceflow/` directory matching the project name. Our layout is `src/app/`, so Poetry can't find it.
- **Surprise:** the explicit `buildCommand: pip install -e .` in render.yaml is *not* the build process — it runs *after* Render's auto-detected dependency tool (Poetry, in our case) finishes. If Poetry's step fails, our buildCommand never runs.
- **Fix:** added `[tool.poetry] package-mode = false` to `pyproject.toml`. This tells Poetry to skip installing the root project and act as a dependency-installer only. Our `pip install -e .` then runs after and installs the project properly via setuptools.
- **Lesson:** Render's `runtime: python` is not a blank slate that runs your buildCommand. It runs a full opinionated buildpack with auto-detected dep tools that have their own assumptions about layout. The buildCommand is appended, not authoritative. If those assumptions don't match your repo, expect to add tool-specific escape hatches (`package-mode = false`, `--no-root`, etc.) or switch to `runtime: docker` and own the whole pipeline.

### fix: third Render failure — service started with placeholder gunicorn command
- **Symptom:** `==> Running 'gunicorn your_application.wsgi' / bash: line 1: gunicorn: command not found / Exited with status 127`.
- **Root cause:** despite the Blueprint flow being used (per Andy), the `startCommand` and `buildCommand` from `render.yaml` did not propagate to the service. The dashboard ended up with Render's default Python placeholder (`gunicorn your_application.wsgi`) instead of our `uvicorn app.main:app …`. Exact mechanism unconfirmed — likely the Blueprint confirmation UI presented each field for review and the placeholder was accepted by reflex.
- **Fix:** patched both fields manually in the Render dashboard:
  - **`traceflow-api`** — Build: `pip install -e .` / Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT` / Health: `/health`
  - **`adapter-health-check`** — Build: `pip install -e .` / Start: `python -m app.jobs.adapter_health`
- **Service went live after the patch.** Build succeeded, startup completed. `asyncpg.create_pool()` is lazy so the placeholder `PENDING` Supabase DSN didn't crash startup — any DB-touching endpoint will 500 until real keys land.

### build: all 9 schema migrations applied to Supabase
- **What:** Wrote `scripts/apply_migrations.py` (durable, idempotent migration runner using asyncpg + a `schema_migrations` tracking table). Applied all 9 SQL files to the TraceFlow Supabase project (`ienjxmyhttuzxoaeramo`).
- **Verification:** 16 tables in `public` schema (15 from migrations + `schema_migrations`), all with RLS enabled. 14 have tenant-isolation policies; `audit_log` and `schema_migrations` intentionally have 0 policies (service-role-only access by design). Extensions live: `vector 0.8.0`, `pgcrypto 1.3`, `uuid-ossp 1.1`.
- **Why a runner script:** every future schema change needs the same flow. `python scripts/apply_migrations.py` with `SUPABASE_DB_URL` set is now the one-line repeatable interface.
- **Migration path used:** direct connection (`db.<ref>.supabase.co:5432`). Works from local because Andy's network has IPv6 reachability — Supabase Free's direct port is IPv6-only. **Render may not have IPv6** from its outbound network; if the FastAPI service fails to connect after redeploy, swap `SUPABASE_DB_URL` in Render to the pooler session-mode URL (`postgres.<ref>:PASSWORD@aws-0-<region>.pooler.supabase.com:5432/postgres`).

### security: rotation queue (for after Render integration is verified working)
- DB password `Hiandysuarez123!` — both weak and exposed in chat. Rotate via Supabase → Project Settings → Database → Reset password. Generate strong random.
- `service_role` JWT and `anon` JWT — both pasted in chat. Rotate via Project Settings → API → Reset (this rotates both keys simultaneously).
- All three rotations require Render env var updates → redeploy. Do as a single pass once `/health` + a DB-touching endpoint smoke-test cleanly.

### milestone: Render ↔ Supabase integration working end-to-end
- **What:** Service at `https://traceflow-api-8f3o.onrender.com` boots with `environment: production`, asyncpg pool initialized through the Supabase pooler. Latest commit `b8b57e6` deployed.
- **DSN that finally worked (pooler, session mode):** `postgresql://postgres.ienjxmyhttuzxoaeramo:<PWD>@aws-1-us-west-1.pooler.supabase.com:5432/postgres`
- **Three failure modes hit along the way (chronological):**
  1. Direct DSN with `[bracketed password]` — `urllib.parse` rejected the brackets as malformed IPv6 host literals (`ValueError: 'db.ienjxmyhttuzxoaeramo.supabase.co' does not appear to be an IPv4 or IPv6 address`). Lesson: brackets in Supabase UI's `[YOUR-PASSWORD]` are placeholder delimiters, NOT part of the URL syntax.
  2. Direct DSN with brackets removed — would've failed with IPv4/IPv6 mismatch (Supabase Free direct connection is IPv6-only, Render outbound is IPv4-only) but we skipped this hop by switching straight to pooler.
  3. Pooler DSN with guessed host (`aws-0-us-west-1.pooler.supabase.com`) — `asyncpg.exceptions.InternalServerError: Tenant or user not found`. The pooler subdomain prefix is project-specific (`aws-0-` vs `aws-1-`) and must be copied verbatim from Supabase's "Connect" modal, not guessed.
- **Correct host for this project:** `aws-1-us-west-1.pooler.supabase.com` (West US, cluster 1).
- **Where the connection string lives in the new Supabase UI:** the "Connect" button at the top of the dashboard (not under Project Settings → Database, which has been reorganized). Session mode pooler (port 5432) is the right choice for asyncpg because connection state must persist across queries — transaction mode pooler (port 6543) would silently break the `app.current_client_id` RLS plumbing.
- **Env var drift carried over from earlier Blueprint hiccup:** `ENVIRONMENT`, `BASE_URL`, `ALLOWED_ORIGINS`, and `ADMIN_JWT_SECRET` were all missing from the service when the Blueprint partially failed. All set manually in the dashboard now; documented under the existing drift decision above.

### milestone: Twilio account provisioned
- **Account:** created on `andy@traceflow.app`. 2FA + recovery codes pending Andy confirmation.
- **Phone number:** NOT purchased — per the LLR model, numbers are per-client and purchased at client onboarding, not platform-level.
- **Env vars:** `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` stay as `PENDING` in Render until first client is signed and the LLR pipeline goes live. Twilio webhook handler doesn't init the SDK at startup; creds are only needed at signature-verify time.

### decision: keep the current Render services (do not redo via Blueprint cleanly)
- **What's drifted from render.yaml:** `buildCommand` and `startCommand` on both services are dashboard-set, not YAML-set. Everything else (plan, region, cron schedule, env var group structure, healthCheckPath) matches.
- **Why not redo:** Render Blueprints are not live-sync — even a clean Blueprint provisioning doesn't keep the dashboard in lockstep with future YAML edits. The mental model "render.yaml is the source of truth" is aspirational on Render. Redoing now would cost ~15 min of clicks, lose the current deploy history, and could re-trigger whatever Blueprint quirk caused the issue in the first place. Trade-off is currently asymmetric — small drift now vs guaranteed cost to redo.
- **What to remember:**
  - If you change `buildCommand` or `startCommand` in render.yaml, **also change them in the dashboard** or the YAML change is silent
  - If drift grows to more than ~3 fields, redoing the Blueprint becomes worth it
  - Env var group `traceflow-secrets` is YAML-managed (currently all `PENDING` placeholders pending Supabase capture)

### What's next session
- Andy reports back: Render service URLs + screenshot of provisioned services
- Andy completes Supabase 2FA + paste DB URL (so migrations can run)
- I apply the 9 migrations via asyncpg
- Capture Supabase service role + anon keys → paste into Render env var group
- Trigger redeploy → `GET /health` smoke test from `traceflow-api.onrender.com`

---

## 2026-05-14 — Platform skeleton extracted + CI wired

### build: Multi-tenant platform code extracted from SEMCO source repos
- **What:** Refactored two single-client SEMCO repos (Shopify→Monday integration, AI KB backend) into the canonical TraceFlow multi-tenant codebase. 65 new files across `src/`, `migrations/`, `tests/`, `scripts/`, plus `pyproject.toml`, `Dockerfile`, `docker-compose.yml`, `render.yaml`, `.env.example`, `.gitignore`.
- **Sources:** Reference-only ZIPs in `~/Downloads/SEMCO-*-main.zip`. No git history carried over.
- **Schema (9 migrations):** `clients`, `client_configs`, `client_field_mappings` (Layer 2), `client_webhook_configs` (Layer 3), `leads`/`messages`/`events` (canonical), `kb_entries`/`kb_documents`/`kb_chunks` (pgvector for SIA Module C), `audit_log` + generic trigger, `sync_log`, `user_permissions`, `product_yields`/`calculator_configs` (SIA Module B engine, generic). Every tenant-scoped table has `client_id` + RLS policy.
- **Extracted patterns:**
  - Shopify HMAC webhook → `webhooks/shopify.py` (path-based tenant routing replaces store-key dict)
  - Full Monday adapter incl. parent + subitems → `adapters/monday.py`
  - Supabase JWKS-based JWT verification → `middleware/auth.py`
  - HMAC verifiers (base64 / hex / timestamped+replay) → `services/webhook_signature.py`
  - In-memory dedupe with TTL → `services/dedupe.py`
  - KB CRUD + generic CSV export → `routers/kb.py`, `routers/kb_export.py`
  - Audit log trigger pattern → `migrations/006_create_audit_log.sql`
  - Generic quote calculator engine → `services/calculator.py`
- **Dropped:** SEMCO sample-inventory loop, vertical-specific shipping rules, multi-color line-item expansion, monthly board rotation, Shopify inventory sync, `unanswered_questions` table (replaced by `leads.qualification_status='needs_review'`), Tidio/Lyro vendor coupling, React admin UI (Phase 3+), KB seed containing real customer PII.
- **Tests (5 modules, 47 unit tests + 5 RLS isolation tests):** `test_tenant_isolation.py` (the non-negotiable suite — per-table RLS-on + cross-tenant leak tests + no-context-denies-all), `test_dedupe.py`, `test_webhook_signature.py`, `test_calculator.py`, `test_field_mappings.py`, `test_monday_adapter.py` (HTTP layer mocked).
- **Source-company scrub:** Verified zero matches across `src/`, `migrations/`, `tests/`, `scripts/`, config files for `semco`, `x-bond`, `microcement`, `liquid membrane`, `natural shield`, `satin stone`, `titan shield`, `tidio`, `lyro`, `zoho`, customer PII (`808-439-2495`, etc.), and all source-vertical product/color/texture names. Remaining SEMCO references in the repo are intentional (PRD/playbooks/marketing-copy skill — Andy's founder moat).
- **Status:** Skeleton ready. Phase 0 actual build follows: Twilio missed-call flow, GHL adapter, qualifier prompt, SEMCO case study artifact.
- **Links:** None yet (pre-GitHub). Local tree at `traceflow/src/`, `traceflow/migrations/`, `traceflow/tests/`.

### build: CI workflow + async permission lookups
- **What:**
  1. Added `.github/workflows/ci.yml` — spins up `pgvector/pgvector:pg16` as a service container, applies the Supabase auth stub (`tests/sql/bootstrap_supabase_stubs.sql`) + all 9 migrations, then runs `ruff check` + full `pytest`. CI sets `TRACEFLOW_TEST_DB_URL` so the tenant isolation suite runs in strict mode and hard-fails on any cross-tenant leak.
  2. Refactored `services/permissions.py` from a sync-bridge (`asyncio.get_event_loop().run_until_complete()`) to a clean `async def get_user_permissions()`. Updated `middleware/auth.py` so `require_permission()` returns an async dependency and `require_admin` is `async def`. Updated `routers/calculator.py` to `await` the permissions call.
- **Why:** The sync-over-async bridge was brittle in async request contexts. CI provides the only enforcement of the isolation suite — locally it skips when no DB is available so contributors aren't blocked.
- **Status:** Workflow is in place; first run happens whenever the repo is pushed to GitHub.

---

## 2026-05-13 — Brand collision review

### decision: Keep TraceFlow name despite competitor at gettraceflow.com
- **What:** Discovered existing entity using "Traceflow" — a B2B SaaS for customer journey analytics at gettraceflow.com, owned by PaceForms. Site appears recently launched (broken CTAs, "Built with Traceflow" footer, absent from category roundups).
- **Trademark check:** USPTO search returned one pending application — Serial 99128754, wordmark TRACEFLOW, Status Live/Pending, Class 042 ("software engineering services for other software development"), owner Siva Rama Krishna Kayala Venkata (individual, not PaceForms entity).
- **Risk assessment:**
  - Different audiences (B2B SaaS analytics buyers vs surface contractors) = ~zero practical brand confusion
  - Different USPTO class (their pending 042 vs our intended 035 business services) = legally distinct categories
  - Their recitation is narrow ("software engineering services for other software development") and doesn't clearly cover their own actual SaaS product = weak filing
  - Pending, not registered = no granted rights yet
  - Filed by individual, not corporate entity = lower-resourced opposition risk
- **Decision:** Keep the name. Compete on execution. Sunk cost to pivot is small (~$20) but the collision is low-risk in our category and audience.
- **Protective actions agreed:**
  1. Build dated first-use-in-commerce evidence as we go (landing page screenshots, first LinkedIn post, signed contracts archived)
  2. File USPTO Class 035 application within 30 days of first paying client (~$350 DIY via Trademark Center)
  3. Monitor serial 99128754 quarterly via tsdr.uspto.gov for refusals, narrowing, or abandonment
  4. SEO play: pair "TraceFlow" with niche-specific terms in all content ("TraceFlow for contractors," "TraceFlow lead recovery") to own the contractor-related search space early
  5. If C&D letter ever arrives: do not respond solo; $300-500 trademark attorney consult first
- **Disclaimer logged:** This is founder judgment, not legal advice. When real money or threats appear, hire counsel.

---

## 2026-05-13 — Phase 0 kickoff

### decision: Brand name + domain locked
- **Name:** TraceFlow
- **Domain:** traceflow.app ($13/yr on Namecheap)
- **Rationale:** Tool-led horizontal naming (avoids vertical lock-in to surfaces/contractors). "Trace" = visibility/intelligence. "Flow" = automation. Scales to any future vertical.
- **Rejected alternatives:** ByteKeep (wrong tonality, sounds like dev tool not business outcome), Reclaim/Conduit/Relay/Manifold (all taken by funded competitors), .it.com (third-level domain, deliverability nightmare), .org (wrong signal for B2B SaaS).
- **Status:** Domain purchased. Google Workspace email being configured.

### decision: Email infrastructure
- **Provider:** Google Workspace at andy@traceflow.app
- **Cost:** $7/mo
- **DNS:** MX, SPF, verification TXT records configured on Namecheap. DKIM pending after first login.
- **Rationale:** Cold outreach deliverability requires real email infrastructure. Non-negotiable.

### decision: PRD v1.1 finalized
- **Doc:** `docs/PRD.md`
- **Key additions vs v1.0:**
  - Section 7: Platform Architecture & Operating Principles ("configuration over customization" thesis)
  - Section 11: Automation Roadmap merged with UI Maturity Model (four-phase progression)
  - Tightened off-boarding contract language (Section 8 + Appendix C)
  - Strategic fork at Month 12 (Path 1 lifestyle vs Path 2 SaaS) made explicit
- **Status:** Single source of truth for strategy.

### decision: Repo + context structure
- **Approach:** Monorepo at `traceflow/`
- **Obsidian:** stays as separate context tool; repo is canonical
- **Skill format:** Full Claude Code skill format with YAML frontmatter
- **Files created:** CLAUDE.md, README.md, CHANGELOG.md, docs/PRD.md, docs/architecture.md, docs/workflow-schema.md, 8 skill files, 3 playbooks, 1 initial ADR
- **Status:** Foundation complete. Ready for Phase 0 builds.

### decision: Tech stack confirmed (Phase 0)
- **Backend:** FastAPI + Supabase + Render + Anthropic API
- **SMS:** Twilio
- **Email:** Resend or Postmark (TBD)
- **Dev:** CachyOS + Claude Code CLI + GitHub Pro
- **Rationale:** Reuses SEMCO stack expertise. Multi-tenant from day one.

### decision: Solution productization
- **Externally marketed:** Lead Leak Recovery (LLR) only
- **Internally available:** Software Integration & Automation (SIA) — sold to existing clients or self-identified ops-pain prospects
- **Pricing:** Founding Partner $1,500 + $397/mo (clients 1-2). Standard $2,500 + $597/mo (clients 3+). SIA tiers $3,500–$7,500 setup + $797–$1,497/mo.

### decision: Target market locked
- **Primary ICP:** Surface contractors (countertop, flooring, tile, stone, pool resurfacing), $1M–$10M revenue, NV/AZ/CA/TX initially
- **Secondary (Phase 2):** Pool builders, HVAC, roofing, general home services $2M+
- **Disqualifiers:** Under $500K revenue, over $20M revenue, direct SEMCO competitors, recently burned by AAA

### milestone: AAA path chosen over Midas
- **Context:** Considered launching Midas (crypto trading SaaS) vs AAA service
- **Decision:** AAA first. Midas frozen until $3K MRR or M12 strategic fork
- **Rationale:** AAA has 60-90 day path to $1K MRR with high confidence. Midas has 12-18 month path with low confidence. AAA's earnings fund Midas if/when revisited.
- **Hard rule:** Zero Midas/Flux/Bytekeep work until $3K MRR threshold.

---

<!-- Template for future entries:

## YYYY-MM-DD — Session summary

### type: One-line summary
- **What:** ...
- **Why:** ...
- **Status:** ...
- **Links:** [related docs, PRs, etc]

-->
