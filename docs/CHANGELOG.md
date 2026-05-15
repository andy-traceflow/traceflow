# CHANGELOG

> Append-only log of significant decisions, builds, and milestones for TraceFlow. Newest entries at the top. Each entry: date, type, summary, links.

**Types:** `decision` | `build` | `milestone` | `pivot` | `pause` | `learning`

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
