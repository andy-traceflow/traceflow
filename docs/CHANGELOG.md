# CHANGELOG

> Append-only log of significant decisions, builds, and milestones for TraceFlow. Newest entries at the top. Each entry: date, type, summary, links.

**Types:** `decision` | `build` | `milestone` | `pivot` | `pause` | `learning`

---

## 2026-06-01 — Caller-classification runtime tier (LLR stops treating every missed call as a lead)

### milestone: the classification runtime tier is built end-to-end
Missed calls are now classified before *and* after the greeting, routed by what the caller IS, and reported on — so spam, vendors, and existing customers no longer enter the sales pipeline as "leads." Built in four reviewable slices; full offline suite **269 passed / 40 skipped**, ruff clean. Everything ships behind the existing graceful-degradation invariant: no Anthropic key / no Twilio creds / no CRM / any lookup failure → the caller is treated as a recoverable `potential_lead` and is never dropped.

### build: Slice 1 — pre-send classification + routing (`services/classification.py`)
- `classify_caller` decision tree: active-conversation short-circuit → vendor allowlist → CRM lookup (existing_customer / known_non_lead / re-engagement) → unknown. Every failing or ambiguous path degrades to `potential_lead`. Config-driven (no per-client code branches): a client with no CRM and an empty allowlist behaves exactly like the pre-classification system.
- New `leads.classification` column (migration 014), **orthogonal** to `qualification_status`: classification = what the caller IS (`potential_lead` / `existing_customer` / `known_non_lead` / `spam`); qualification_status = how far a genuine lead got. `classification_config` JSONB + `vendor_allowlist` + `existing_customer_alert_contact` (migration 013), mirrored on `ClientConfig`.
- Per-adapter `lookup_by_phone(phone, config) -> CRMContact | None` (GHL + Monday); canonical `CRMContact` in `models/crm_contact.py`. Adapter and caller each enforce a ~2s timeout, so a slow CRM can never delay the missed-call SMS.

### build: Slice 2 — post-reply intent classification, the safety net (`prompts/intent.py`)
- The first inbound reply on an `unqualified` lead runs `classify_intent` (Haiku) before the qualifier. Routes: `sales` / intent unavailable → qualifier (`qualifying`); `existing_customer` → owner alert + `support_touch`; `non_lead` → `non_lead_contact` (silent, surfaces in the digest); `spam` → `spam`; `ambiguous` → one clarifying question, re-runs on the next reply.
- Migration 015 adds the `support_touch` + `non_lead_contact` terminal statuses. Billing: +1 `ai_interaction` per real classification; the degraded/no-key path doesn't bill (no call was made).

### build: Slice 3 — spam scoring (`services/spam.py`)
- Twilio Lookup v2 `line_type_intelligence` → coarse `SpamRisk` (`nonFixedVoip` = high; toll-free/premium/etc. = moderate; everything else = low), scored **only** for unknown callers — a CRM-known caller is never scored as spam. Conservative default: `spam_risk_threshold="moderate"` carries a *high* floor (only `nonFixedVoip` dropped); `"strict"` opts into dropping moderate; `low` is never dropped. Any failure (no creds / timeout / HTTP error / non-JSON) → no signal → `potential_lead`. No migration (config + enums already existed).

### build: Slice 4 — recovery metrics + nightly digest (`jobs/daily_digest.py`)
- Per-tenant owner email digest. **Recovery rate = replied ÷ captured, computed over `classification='potential_lead'` ONLY** (spam/existing/vendor excluded from the denominator, per `workflow-schema.md` `digest_inclusion`). Sections: recovery-rate hero, captured/replied/qualified/pending, estimated pipeline ($ from budget-bucket midpoints), the genuine-lead table, and a "handled automatically" block that surfaces the silent dispositions (spam / `non_lead_contact` / `support_touch`) to the owner.
- One hourly Render cron (`daily-digest`), self-gated to each client's **06:00 local** (zoneinfo + new `tzdata` dependency); idempotent via a `daily_digest_sent` event. Enumerates tenants via `get_service_connection()` — **not** `get_connection()`, whose forced-RLS + no-tenant context returns zero rows; per-client work stays RLS-scoped via `set_tenant_context`. Zero AI and zero Twilio spend (pure SQL + one email) → runs fully in Phase 0. See ADR-0002.

### decision: recovery-rate definition (the spec left the formula open)
"Recovered" = a genuine lead that left `unqualified` (the caller texted back) — matches the PRD's "recovery = SMS exchange initiated"; `qualified`/`high_value` is reported separately as conversion. The digest is skipped on zero-activity days so it stays signal, not noise.

### learning: latent RLS bug in `jobs/adapter_health.py` (flagged, not fixed in this batch)
While verifying the digest's tenant enumeration, found that `adapter_health._check_all_clients()` lists clients via `get_connection()` with no tenant set → under forced RLS (migrations 010/011) the `authenticated` role matches **zero rows** → the hourly CRM health check has been silently no-opping. Fix is to switch its enumeration to `get_service_connection()` (exactly what the digest does). Tracked as a separate task.

### Status + live-readiness (operational, not code)
- Landed on branch **`feat/caller-classification-runtime`** (not `main`) — main auto-deploys and these migrations aren't applied to Supabase yet. This commit also lands the previously-uncommitted Phase 0 AI pipeline + admin layer (greeting/qualifier/owner-alert/admin, migration 012) that was sitting in the working tree.
- Before go-live: apply migrations **012–015** (`scripts/apply_migrations.py`); set `RESEND_API_KEY` (digest) and, when ready, `ANTHROPIC_API_KEY` (flips intent + qualifier from fallback to live) in Render; then merge to `main` to deploy.
- Smoke-test the digest: `python -m app.jobs.daily_digest --force`.

---

## 2026-05-26 — Landing page live at traceflow.app

### milestone: traceflow.app is serving production traffic with TLS
First public surface of TraceFlow is live. `https://traceflow.app` resolves to a Render static site with a valid Let's Encrypt cert. Apex + `www` both work; `www` redirects to apex via Render's primary-domain setting. Content is the scaffolding — unstyled baseline only; full design pass is the next session.

### build: landing page scaffolded as a sibling repo
- **What:** New repo [`andy-traceflow/traceflow-landing`](https://github.com/andy-traceflow/traceflow-landing) (private). Six files: `index.html`, `styles.css`, `script.js`, `render.yaml`, `README.md`, `.gitignore`. Initial commit `29097bf`. Plain HTML/CSS/JS — no build step, no framework, no deps.
- **Why a separate repo, not a subfolder of `traceflow`:** Backend and marketing surface are different deploy lifecycles, different runtimes, different blast radii. Putting marketing copy edits in the same repo as the multi-tenant pipeline would mix unrelated concerns and turn `git log` into noise. Cost of a second repo on GitHub Pro is zero.
- **Why plain HTML over Astro/Next:** the page is 7 sections of mostly-static content with one CTA. A framework would add a build step, a `node_modules`, and a deploy-fail surface to gain nothing. Reconsider if the site grows past ~5 pages.
- **Content sourced from `Traceflow/case-study.md`** + the LLR positioning in `docs/PRD.md`. Sections: hero, problem (revenue leaks), how LLR works (4 steps), case study (stats grid + narrative), pricing (Founding Partner only), CTA, footer. Calendly link is a placeholder `calendly.com/andy-traceflow/15min` — replace once Calendly is provisioned.
- **`styles.css` is intentionally bare** — system font, 72ch container, neutral defaults, no design opinion. Style pass is a separate change.
- **Status:** ✅ live at `https://traceflow.app`. Next: design pass.

### build: Render static site provisioned + custom domain wired
- **Service:** `traceflow-11sh` on the `andy@traceflow.app` Render workspace (separate from the personal workspace that hosts the backend). Static site, publish dir `./`, branch `main`, auto-deploy on push.
- **Render URL:** `traceflow-11sh.onrender.com` — internal canonical, custom domain points here.
- **DNS at Namecheap:**
  - `A @ → 216.24.57.1` (Render's static-site load balancer IP)
  - `CNAME www → traceflow-11sh.onrender.com`
  - Default Namecheap parking records (CNAME on `www` to parkingpage.namecheap.com, URL Redirect on `@`) deleted. Existing Google Workspace MX/TXT records left untouched.
- **TLS:** Let's Encrypt, auto-provisioned by Render ~10 min after DNS verification. Both apex and `www` covered.
- **Primary domain:** apex (`traceflow.app`); `www` 301-redirects to it via Render's primary-domain setting.

### learning: Render's UI shows the apex A record IP in small text, not as a primary option
The "Add Custom Domain" wizard surfaces a CNAME target for *both* domains by default. The apex A record alternative (`216.24.57.1`) is rendered as one-line gray text below the CNAME row ("For `A` records, use this target value: …") — easy to miss. Namecheap doesn't support CNAME at the zone apex, so the A record is the only correct choice on Namecheap. **Implication:** when documenting Render+Namecheap setup for clients later (or for ourselves), screenshot the wizard and highlight the A-record line — it's the single most overlooked detail.

### learning: first Render static site was pointed at the wrong repo
- **Symptom:** `traceflow-11sh.onrender.com` returned "Not Found" for `/` even though the service was Live and the publish dir was `./`.
- **Root cause:** the static site was connected to `andy-traceflow/traceflow` (the FastAPI backend repo) instead of a landing-specific repo. Render dutifully served the backend repo's root as static files — no `index.html` exists there, so every request hit Render's default 404.
- **Fix:** create `andy-traceflow/traceflow-landing`, push the scaffolded files, change the service's connected repo in Render → Settings → Build & Deploy, trigger a manual deploy. Custom domain stayed attached to the service across the swap (domains bind to service IDs, not repos), so no DNS rework.
- **Lesson:** Render's "connect a GitHub repo" step is exactly that — a pointer. There's no validation that the repo is appropriate for the service type. A static site can be pointed at a Python web service repo and silently fail with 404s. **Always verify the connected repo, branch, and root file list match the expected service shape before debugging deeper.**

### learning: ISP DNS resolver returned NODATA, not NXDOMAIN, for the new records
- **Symptom:** after Namecheap was updated and Render verified the domain, the browser still showed `DNS_PROBE_FINISHED_NXDOMAIN`. `nslookup traceflow.app 1.1.1.1` returned `216.24.57.1` correctly, but `nslookup traceflow.app` (default resolver) returned the unusual error `*** No internal type for both IPv4 and IPv6 Addresses (A+AAAA) records available for traceflow.app`.
- **Diagnosis:** that error is Windows' `nslookup` wording for a DNS NODATA response — the resolver acknowledges the name exists in some form but claims no A/AAAA records exist for it. Likely cause: the ISP's resolver had cached a negative response from before the records were added, and was serving the cached "no records" answer past its TTL.
- **Fix:** Chrome Secure DNS → set to Cloudflare (1.1.1.1). Bypasses the ISP resolver at the application layer. Permanent fix is to switch Windows system DNS to 1.1.1.1, but the Chrome-level toggle was enough to confirm the issue and unblock testing.
- **Lesson:** when verifying a fresh custom domain, never trust your local resolver. The authoritative answer is whatever a clean resolver like `1.1.1.1` or `8.8.8.8` returns. Local + ISP caches can lag arbitrarily long, especially on newly-created records that previously returned NXDOMAIN.

### decision: traceflow.app points at the landing static site, NOT the backend API
- **What:** the apex domain serves the marketing site. The backend FastAPI service stays on its `*.onrender.com` URL for now.
- **Why:** there is no client-facing UI in Phase 0/1 per the platform thesis. The only domain visitors should see is the landing page. The backend is a webhook receiver — Twilio/Shopify/CRM systems hit it directly via their configured URLs, no humans involved. No need to spend a subdomain on it yet.
- **Future:** `api.traceflow.app` can be wired to the backend when (a) we want a stable URL for client webhook configs that survives if we ever migrate Render services, or (b) we ship any API surface a human touches. Neither is required before Client 1.

### What's next session
- Style pass on the landing page (design direction TBD — Andy to define)
- Replace Calendly placeholder URL with real scheduling link once Calendly is provisioned
- Add favicon + OG image
- Consider Plausible or similar lightweight analytics

---

## 2026-05-21 — Phase 0 build: GoHighLevel adapter + LLR pipeline + admin monitoring layer

### milestone: Phase 0 framework complete

End-to-end, the system can now: receive a missed call (Twilio), send a greeting SMS (AI-generated or static fallback), run a multi-turn AI qualification conversation with structured field extraction, push the qualified lead to the client's CRM (Monday or GoHighLevel), and alert the owner if the lead matches a VIP keyword or budget-floor value trigger. Plus a founder-only admin backend so the pipeline can be monitored and corrected from a Retool UI.

**Remaining live-readiness steps are operational, not code:**
1. Apply migration 012 to Supabase (`is_test` on `leads`).
2. Set `ADMIN_JWT_SECRET` in Render env vars.
3. Build the Retool admin UI (~weekend of click-through against the panel spec + `docs/retool-notes.md`).
4. Set `ANTHROPIC_API_KEY` in Render — flips every AI touchpoint from fallback to live.

After those four, Client 1 onboarding can begin. Counting from 2026-05-13's Phase 0 kickoff: framework built in 8 days.

### build: Monday adapter made stateless (column cache removed)
- **What:** Removed `MondayAdapter._column_cache` — a per-board column-ID map held as mutable instance state on the shared registry singleton. `_ensure_columns_cached` became `_discover_columns`, which *returns* the resolved `{parent, subitem, subitem_board_id}` map; `push_lead`/`update_lead` thread it through explicitly.
- **Why:** `registry.py` states adapters "hold no per-request state" — the cache violated that. Not a cross-tenant leak (Monday board IDs are globally unique), but a real staleness bug: a client editing their field mappings without rotating boards got stale column IDs until the process restarted. Also unbounded — no eviction.
- **Trade-off:** discovery now runs on every push/update (1–2 extra GraphQL calls) — immaterial at Phase 0 volume. Also fixed a latent `KeyError` in `update_lead` on the board-not-found path.
- **Tests:** `test_monday_adapter.py` 9/9 green.

### build: GoHighLevel adapter implemented
- **What:** `adapters/ghl.py` was a `NotImplementedError` stub; now real. `push_lead` creates a contact, `update_lead` patches one, `health_check` pings the location, `parse_webhook` minimally wraps the payload. httpx against the v2 LeadConnector API (`services.leadconnectorhq.com`) — no SDK, consistent with the Monday adapter. Stateless.
- **Design:** credentials are a per-client `crm_credentials: {api_key, location_id}` — a GHL Private Integration Token scoped to one location (no OAuth; marketplace-app infra is deferred). Field mappings drive placement: `external_field_type='standard'` → top-level contact key, `'custom_field'` → a `customFields` array entry.
- **Confirm at first GHL onboarding:** the `Version` header value (`2021-07-28`) and the custom-field value key (`field_value`) — GHL has varied both across API revisions and the docs SPA didn't expose the exact contact schema.
- **Tests:** `test_ghl_adapter.py` — 10 tests, HTTP layer mocked.

### build: Twilio missed-call webhook handler — the core LLR flow
- **What:** `webhooks/twilio.py` missed-call route implemented. Missed-call webhook → dedupe on `CallSid` → immediate 200 → background task: create a `twilio_missed_call` Lead, send the client's greeting SMS to the caller, record the `Message` + events. Greeting uses `ClientConfig.greeting_template` (`{business_name}` substituted) or a default.
- **New `services/sms.py`:** `send_sms` via the Twilio REST API (httpx; mirrors `notifications.send_email` — no-ops without creds, never raises into the pipeline). Platform Twilio account, per-client `From` number.
- **New `services/twilio_signature.py`:** X-Twilio-Signature verification (HMAC-SHA1 over URL + sorted params), wired into `webhook_signature.verify_signature_for_request`. The Twilio branch uses the platform auth token (not a per-client secret) and rebuilds the signed URL from `base_url` so a proxy rewriting scheme/host can't break verification. Closes the prior "fail closed in production" gap — Twilio webhooks can now be received in prod.
- **Dependency added:** `python-multipart` — `request.form()` requires it and Twilio webhooks are always `application/x-www-form-urlencoded`. The pre-existing Twilio stub already called `request.form()`; it would have failed at runtime. Surfaced by the new route tests.
- **Tests:** `test_twilio.py` — 17 tests (signature, SMS no-op guards, greeting rendering, route dedupe/scheduling, `_process_missed_call` orchestration); DB + SMS mocked.

### build: AI greeting generation
- **What:** The missed-call greeting SMS is now generated by the Anthropic API. `webhooks/twilio.py` calls `generate_greeting(config)`; on success the AI text is the SMS body, on any failure it falls back to the existing static `_render_greeting` template — the lead always gets a text ("never silently fail an interaction", per the prompt-engineering skill).
- **New `prompts/greeting.py`:** a Jinja2 `GREETING_TEMPLATE`, a `PROMPT_VERSIONS` map, and `generate_greeting` → `(text, version)` or `None`. Variables (`business_name`, `category`, `service_area`, `tone_of_voice`) filled from `client_configs`; per-client version pinning via `ClientConfig.prompt_versions`. Follows the repo `prompt-engineering` skill's reference pattern.
- **New `services/ai.py`:** a process-wide cached `AsyncAnthropic` client — the qualifier and other prompt modules reuse it.
- **Model `claude-haiku-4-5`** — per the prompt-engineering skill's taxonomy: greetings are cheap, single-turn, speed-critical (a fast text-back is the LLR value prop). A one-line constant.
- **Cost tracking:** an AI greeting increments `client_configs.ai_interactions_used`; the `Message` row records `ai_generated` + `prompt_version` (`greeting:v1`). The greeting already runs on the cheapest model, so the AI cap never blocks it.
- **v1 scope:** dropped the skill's `call_time`/`business_hours_status` prompt variables — they need a `timezone` field on `ClientConfig` + time helpers that don't exist yet.
- **Tests:** `tests/prompts/test_greeting.py` — 8 tests (template render + `generate_greeting` with the Anthropic client mocked). `test_twilio.py` updated to cover the AI and template-fallback paths.

### build: AI qualification loop
- **What:** The `sms-reply` webhook (previously a stub) now drives a multi-turn SMS qualification conversation. Inbound SMS → dedupe on `MessageSid` → 200 → background task: find the active lead for `(client_id, From)`, persist the inbound message, bump `unqualified→qualifying`, replay the SMS history to the qualifier, apply extracted fields to the lead, send the reply, record the outbound message.
- **New `prompts/qualifier.py`:** the `QUALIFIER_SYSTEM` Jinja2 template + `qualifier_turn(config, history) → (reply, extracted, version) | None`. Model `claude-sonnet-4-6` per the prompt-engineering skill (multi-turn, stateful). The leading assistant turn (the greeting) is dropped from the replay so the message list starts with a user turn, as the API requires.
- **Field extraction via a structured `update_lead` tool** — one optional param per canonical field, with enums on `budget_range`/`timeframe`/`qualification_status` matching the DB CHECK constraints, so the model can't emit a constraint-violating value. The model also sets the terminal `qualification_status` (`qualified`/`needs_review`/`spam`) when the conversation concludes. Extracted fields are validated through `LeadUpdate` before the `UPDATE leads`.
- **No active lead** for an inbound number → logged and ignored (cold inbound SMS without a prior missed call is a later enhancement). **Qualifier unavailable** (no key / failure) → the inbound message is still saved and the lead is flagged `needs_review` — a lead is never dropped.
- **Cost tracking:** each qualifier turn increments `client_configs.ai_interactions_used`; outbound messages record `prompt_version` (`qualifier:v1`).
- **Tests:** `tests/prompts/test_qualifier.py` — 8 tests (system render, history mapping, `qualifier_turn` with the Anthropic client mocked incl. tool-use extraction). `test_twilio.py` extended with sms-reply route + `_process_sms_reply` orchestration tests.
- **Onboarding note:** each client's Twilio number needs its inbound-SMS webhook pointed at `/webhooks/twilio/sms-reply/{client_id}` (Twilio console config).

### build: Owner alert system (VIP triggers)
- **What:** A lead that matches a client's VIP signals now fires an immediate owner alert. New `services/owner_alert.py` — `find_vip_reason` (deterministic trigger evaluation) and `alert_owner` (dispatch).
- **Triggers:** (1) keyword — case-insensitive match of `client_configs.vip_keywords` against the inbound SMS text; (2) value — the lead's `budget_range` tier *floor* meets `vip_value_threshold` (conservative mapping: `5k-15k`→$5k, `15k-50k`→$15k, `50k+`→$50k — a lead alerts only when its budget is definitely at/above the threshold).
- **Dispatch:** email via the existing `notify_owner_vip` + SMS to `owner_alert_phones` (PRD specifies text/email).
- **Wiring:** evaluated after each qualifier turn in `_process_sms_reply` — where the budget and conversation text are freshest. Deduped via an `owner_alert_sent` event so the owner is alerted at most once per lead. The alert records the event but does not change `qualification_status` — owner-alerting stays decoupled from the qualifier.
- **Tests:** `tests/services/test_owner_alert.py` — 9 tests (keyword/value triggers, dispatch). `test_twilio.py` extended with a VIP-trigger case.

### build: Admin backend (Retool monitoring layer)
- **What:** Backend prep for the Retool admin app — the "monitoring layer" needed before Client 1 goes live. The Retool UI itself is a separate weekend of click-through; this is the application-logic + auth + migration the Retool spec needs to talk to.
- **`verify_admin_token`** (`middleware/auth.py`) — HS256 against `ADMIN_JWT_SECRET`; accepts either the bare secret as a static bearer token (simplest for a single-founder admin) or an HS256 JWT signed with it (rotatable via `exp`). Realizes the path `auth.py` documented but had never implemented.
- **`get_service_connection`** (`db.py`) — a service-role connection that bypasses RLS, for admin operations that cross tenants or write to `audit_log` (which has no tenant policy by design). Documented as bypass-RLS; use sparingly.
- **Fix in `services/audit.py`:** `record_audit_event` was previously calling `get_connection` (authenticated role) → `audit_log` RLS would have denied every write. The helper was dormant so it never bit, but now that admin operations actually use it, switched to `get_service_connection`.
- **Migration 012:** `is_test BOOLEAN NOT NULL DEFAULT FALSE` on `leads` + a partial index. Powers Panel 4's "Mark as test" without an extra endpoint (Retool does a direct UPDATE).
- **`POST /api/admin/leads/{lead_id}/repush`** (`routers/admin.py`) — the one application-logic admin endpoint. If `external_id` is null, runs `push_lead` (handles the original-push-failed case); else runs `update_lead` with the current canonical fields (syncs CRM to qualifier extractions; **never duplicates** a CRM record). Audit-logs as `operation='sync'`, `actor='founder_retool'`.
- **`docs/retool-notes.md`** — setup notes covering the Postgres connection, the bearer-token auth, the client-switcher convention, the form-reset gotcha, and the `audit_log.operation` CHECK constraint gotcha (the panel spec's `'update_config'` / `'manual_ai_usage_reset'` values would fail — mapped to `'update'` with the specific action encoded in `target_table` + `snapshot`).
- **Tests:** `tests/test_admin.py` — 11 tests (verifier unit tests across all five paths; endpoint tests for auth failures, lead-not-found, no-provider, push-when-no-external-id, update-when-set, audit-recorded).

### Status + what's next
- **Status:** ✅ Full suite 154 passed / 40 skipped (DB-dependent, skip without a local DB), ruff clean across all changed files.
- **Framework complete:** the full LLR pipeline (missed call → greeting → qualification → owner alerts) and the admin/monitoring backend are both in place. What remains before going live is the Retool UI itself (panels 0–5, ~weekend of click-through against the queries in the panel spec) and turning on `ANTHROPIC_API_KEY` to flip every AI fallback into the live path.
- `parse_webhook` on both CRM adapters is still a deliberate minimal stub — the CRM *inbound* webhook (`webhooks/crm.py`) and bidirectional sync remain Phase 2.
- Deferred enhancements: cold inbound SMS without a prior missed call, real-API golden evals for the prompts, numeric `qualification_score`, and the AI `vip_classifier` (deterministic VIP triggers ship now; the AI refinement is later).

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

### fix: tenant isolation was silently broken — RLS was being bypassed at the role level
- **What:** ran `tests/test_tenant_isolation.py` against the live Supabase DB for the first time. 3 of 25 tests failed: Client B could see Client A's `leads`; same on `kb_entries`; "no tenant context → deny all reads" returned rows. The plumbing existed (RLS enabled on every table, policies present) but it wasn't actually enforcing anything.
- **Root cause #1 (the big one):** Supabase's `postgres` role has `bypassrls=true` set at the role level (`SELECT rolbypassrls FROM pg_roles WHERE rolname='postgres'` returns `t`). PostgreSQL skips RLS entirely for any role with that attribute, *regardless* of `ENABLE ROW LEVEL SECURITY` or `FORCE ROW LEVEL SECURITY` on the table. The production FastAPI service was connecting as `postgres` (via `SUPABASE_DB_URL`), so every query — including the per-request `app.current_client_id` setting — was effectively unfiltered admin access.
- **Root cause #2 (the smaller one):** when policies tried to cast an empty-string `app.current_client_id` to UUID, the cast raised `invalid input syntax for type uuid: ""` instead of gracefully filtering to zero rows. So the "no tenant context" defense path crashed rather than denying.
- **Fix:**
  - `migration 010_force_rls_on_tenant_tables.sql` — `ALTER TABLE ... FORCE ROW LEVEL SECURITY` on all 14 tenant-scoped tables. Necessary but not sufficient on its own (BYPASSRLS at the role level still wins).
  - `migration 011_null_safe_tenant_policies.sql` — rewrote every tenant_isolation policy to wrap `current_setting('app.current_client_id', true)` in `NULLIF(..., '')` so empty strings collapse to NULL before the UUID cast.
  - `src/app/db.py` — `get_connection()` now opens an explicit transaction and runs `SET ROLE authenticated` before yielding. `authenticated` has `bypassrls=false` and full DML grants on all our tables (verified via `information_schema.role_table_grants`). The role switch + tenant setting are both transaction-bounded, so they revert cleanly when the request ends — no state can leak into the next request that acquires the same pool connection.
  - `tests/test_tenant_isolation.py` — fixture setup/teardown stays on `postgres` (admin ops); test bodies switch into `authenticated` to actually exercise RLS.
- **Verification:** all 25 tests in `test_tenant_isolation.py` now pass against the live Supabase DB. Full suite: 102/102 green.
- **Lesson:** RLS on Supabase is enforced at *two* levels — the table (`ENABLE ROW LEVEL SECURITY` + optional `FORCE ROW LEVEL SECURITY`) AND the role (`bypassrls` attribute on the connecting role). You must control both. The default `postgres` connection is unsafe for any code that depends on RLS. Always `SET ROLE authenticated` (or a custom non-bypassing role) before running tenant-scoped queries from a backend service.

### build: tenant_resolver middleware checklist item complete
- **Coverage now in place:**
  - `tests/test_tenant_isolation.py` (25 tests) — end-to-end RLS enforcement at the DB layer, hardened against the BYPASSRLS issue
  - `tests/test_tenant_resolver.py` (28 tests) — pure-function path regex extraction for every webhook URL shape we publish (Twilio SMS/voice, Shopify, CRM-per-provider, generic-with-endpoint-segment); positive and negative cases, case sensitivity, trailing slashes, partial UUIDs
- **Status of the checklist item ("Tenant resolver middleware working — sets `app.current_client_id`"):** ✅ verified working end-to-end. Middleware extracts client_id from path → sets ContextVar → `db.get_connection()` opens a transaction, switches role, sets the session variable → RLS policies filter using the variable → cross-tenant queries return zero rows.

### build: tenant isolation test suite checklist item complete + CI bootstrap fixed
- **Found:** CI had been silently failing for ~4 pushes today. `tests/sql/bootstrap_supabase_stubs.sql` created the `auth.users` table but **didn't create the `authenticated` / `anon` / `service_role` roles** that Supabase ships by default. Two failure points in CI:
  - Migration 008 (`CREATE POLICY ... TO authenticated`) — role didn't exist → migration step crashed
  - Production code's `SET ROLE authenticated` in `db.py` (added today) would have crashed in CI even if migration 008 had been fixed
- **Fixed:** bootstrap now creates all three roles with the right attributes (no bypassrls on `anon`/`authenticated`, BYPASSRLS on `service_role`), grants schema usage, and sets `ALTER DEFAULT PRIVILEGES` so all tables created by subsequent migrations automatically get DML grants for these roles. Mirrors what Supabase does on managed projects.
- **Test coverage gaps closed:**
  - Added `kb_chunks` and `user_permissions` to `TENANT_SCOPED_TABLES` — parametrized RLS-enabled + policy-exists checks now cover all 13 tenant-scoped tables instead of 11
  - Added direct cross-tenant isolation tests for `messages` and `events` — both are high-traffic, customer-content-carrying tables where a leak would be especially damaging
- **Status of the checklist item ("Tenant isolation test suite"):** ✅ closed. Full suite: 108/108 passing locally against live Supabase. CI should now go green on this push.

### build: generic webhook handler (Layer 3) checklist item complete + JSONB codec fix
- **What:** new `tests/test_generic_webhook.py` covers the full Layer 3 path end-to-end via FastAPI's TestClient (16 tests). 7 are pure-function tests on the JSONPath extractor; 9 are integration tests that POST to `/webhooks/generic/{client_id}/{slug}` with various sig states and assert on the resulting `leads` + `events` rows.
- **Integration scenarios covered:** 404 for unknown slug, 401 for bad HMAC, 200 + Lead persisted for valid HMAC hex *and* base64 *and* timestamped (Stripe-style), 401 on stale timestamp (replay protection), 200 + no Lead on invalid JSON (ack to prevent retry storm), 200 with `signing_algorithm='none'`, and **tenant isolation** (Client B's URL with Client A's slug and a valid signature for A's secret resolves to 404, not a leak).
- **Real bug surfaced + fixed:** the handler treated `field_extractors` as a dict but asyncpg returns JSONB columns as raw JSON strings by default → `AttributeError: 'str' object has no attribute 'items'` on every signed request. Latent since the platform skeleton landed. Fixed by registering JSON/JSONB type codecs at pool init (`init=_register_codecs`) so JSONB reads come back as dicts and writes accept dicts directly. Also removed the now-redundant `json.dumps(...)` wrappers from 5 callsites (`generic.py` × 2, `shopify.py` × 2, `adapter_health.py` × 1) that would have double-encoded under the new codec.
- **Test infrastructure changes:** `tests/conftest.py` now mirrors `TRACEFLOW_TEST_DB_URL` into `SUPABASE_DB_URL` at collection time so FastAPI's lifespan can initialize the connection pool against the test DB. Previously there was no way to bring up the full app stack from inside the test suite.
- **Status of the checklist item ("Generic webhook handler (Layer 3) built and tested"):** ✅ closed. Full suite: 124/124 green.

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
