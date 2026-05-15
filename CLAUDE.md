# CLAUDE.md — TraceFlow Master Context

> This file is read by Claude Code at the start of every session. It establishes who Andy is, what TraceFlow is, how we work together, and where to find deeper context. **Read this first, then load relevant skills as needed.**

---

## Who I am (the founder)

**Andy** — Marketing Coordinator at SEMCO Surfaces in Las Vegas by day; founder of TraceFlow by evenings/weekends. Substantial real-world technical experience despite the non-engineering title:
- Built SEMCO's Shopify → Monday.com webhook integration (GraphQL)
- Built SEMCO's Lyro AI chatbot with a 193-entry knowledge base from TDS sheets and install guides
- Built FastAPI/Supabase/Render automation pipeline for SEMCO ops
- Custom Liquid invoice templates with category grouping + per-sqft cost calculations across three Shopify stores

Pivoting toward AI Automation Engineer / Solutions Engineer territory but doing it as a founder rather than a job applicant.

**Working style preferences:**
- **Precision and version control.** Always ask for current code before suggesting changes. Don't overwrite previous progress.
- **Iterative.** Refines formulas, code, and design assets with granular feedback loops. Step-by-step beats giant rewrites.
- **Honest pushback expected.** If I'm about to do something wrong, say so. Don't capitulate to bad instincts.
- **Detail-oriented.** Bullets and structure when complexity warrants; prose when it's simpler.
- **Hands-on with design and branding.** May request IKEA-style instruction visuals occasionally.

---

## What TraceFlow is

**TraceFlow is a productized AI automation service for surface, countertop, flooring, and pool resurfacing contractors** ($1M–$10M revenue SMBs). Currently in Phase 0 (Foundation).

**Two underlying solutions, pitched contextually:**
1. **Lead Leak Recovery (LLR)** — recover 25%+ of missed-call revenue within 30 days
2. **Software Integration & Automation (SIA)** — save 15+ hours/week of admin work via integrated systems

**Externally we market ONE offer (LLR).** SIA is sold to existing clients or to prospects who self-identify with ops pain rather than lead pain during discovery.

**Business model (per client):**
- Setup fees: $1,500–$5,000 one-time
- Monthly retainer: $397–$1,497 recurring
- Affiliate income from GoHighLevel (40% recurring) and others, layered passively
- 85–90% gross margin before time

**Target by Month 6:** $3K–$5K MRR + $200–$500/mo passive (digital products + affiliate), 10–15 hours/week of active work.

**Phase 5 strategic fork at Month 12:** stay service-heavy lifestyle business OR pivot to productized SaaS. Decision deferred until real data is available.

---

## The platform thesis (the single most important operating principle)

**TraceFlow is software, configured per client. Not custom builds per client.**

This is the architectural decision that determines whether the business scales to $5K MRR or strangles at Client 5. It governs every technical choice we make. See `docs/architecture.md` for full detail, but the short version:

- **One Render service** runs the FastAPI app
- **One Supabase project** holds shared tables, isolated by Row Level Security
- **Every table has a `client_id` column.** Every query filters by it.
- **Adapters at the edges**, canonical schema in the middle, per-client configuration in the database
- **When in doubt, push variability into config rows, not code branches**

### The inviolable rule
**If you find yourself maintaining two versions of the same thing, you've already broken the model.**
- Two prompt files for two clients → wrong. One templated prompt with config variables.
- Two slightly different cron jobs → wrong. One job iterating over clients with their schedules.
- Two deploy targets → wrong. Feature flags in a single deploy.

When this happens, stop, refactor, push variability into the database, then continue.

---

## Tech stack (current, Phase 0)

**Backend platform:**
- FastAPI (Python 3.11+) on Render
- Supabase (Postgres + Row Level Security + pgvector + Auth)
- Anthropic API (primary), OpenAI API (fallback/cheap-tier)
- Twilio (SMS, missed call webhooks)
- Resend or Postmark (transactional email)

**Frontend / UI (defer until Phase 2+):**
- No client UI yet — clients receive Loom walkthroughs and email digests
- Eventually: Retool admin UI (Phase 2), then Next.js or Retool client portal (Phase 3)

**Dev environment:**
- CachyOS dual-boot (Linux primary for AI work) + Windows
- Claude Code CLI for assisted development
- GitHub Pro for private repos
- Cursor or VSCode

**Auxiliary tooling:**
- Stripe (billing)
- Calendly (sales calls)
- Loom (async client comms)
- Tally or Notion (onboarding forms)

---

## What to always do

1. **Ask for current code before suggesting changes** to anything that already exists.
2. **Check `docs/CHANGELOG.md`** if there's ambiguity about what's been decided or built.
3. **Push back on requests that violate the platform thesis** (custom code per client, premature UI building, scope creep).
4. **Surface tradeoffs explicitly** rather than picking silently.
5. **Use the relevant SKILL.md files** for the area being worked on. They contain the patterns, not just principles.
6. **Treat tenant isolation as non-negotiable.** Every query, every endpoint, every test.
7. **Preserve raw payloads.** Always store the original webhook/event data in addition to the parsed version. Debugging gold.

---

## What to never do

1. **Never write client-specific code branches** (`if client_id == 'xyz'`). Push variability into config.
2. **Never build client-facing UI before Client 8.** Premature UI is the #1 reason AAA founders don't sign clients in their first 90 days.
3. **Never assume tenant isolation is handled.** Confirm RLS policies and `client_id` filters on every new table or query.
4. **Never trap clients via infrastructure.** Off-boarding clause is sacred: clients export their data on demand, retain `client_id`-scoped business records. Founder retains the platform code, prompts, integrations.
5. **Never expand scope past LLR for marketing.** SIA is internal expansion, not external positioning.
6. **Never let "interesting" trump "shipped."** Midas/Flux/Bytekeep ideas stay on ice until $3K MRR is hit.
7. **Never overwrite Andy's working code without asking.** Iteration over replacement.

---

## Skill index

The `.claude/skills/` directory holds repeatable expertise. Load these as needed based on the task:

| Skill | When to load |
|---|---|
| `multi-tenant-arch` | Any work touching DB schema, RLS, tenant routing, or webhook handlers |
| `adapter-pattern` | Adding/modifying CRM, e-commerce, or messaging integrations |
| `prompt-engineering` | Building or tuning AI prompts for lead qualification, knowledge base responses, etc. |
| `client-onboarding` | Anything related to bringing a new client live — form fields, provisioning, handoff |
| `marketing-copy` | LinkedIn posts, landing page copy, outreach DMs, case studies |
| `content-creation` | YouTube scripts, blog posts, social content for top-of-funnel |
| `fastapi-supabase` | Backend-specific patterns: middleware, error handling, testing, deployment |
| `context-sync` | Updating CHANGELOG.md, PRD.md, ADRs, and the workflow-schema after meaningful work |

When unsure which skill to load, default to skim-reading the index in each, then load the relevant ones.

---

## Document index

`docs/` holds the living strategic + technical documentation. Source of truth.

| File | Purpose |
|---|---|
| `docs/PRD.md` | Living product requirements doc. Single source of truth for strategy, pricing, ICP, solutions |
| `docs/architecture.md` | System architecture deep-dive (extracted from PRD §7) |
| `docs/workflow-schema.md` | Full lifecycle workflow as structured schema (sales, onboarding, delivery, retention) |
| `docs/CHANGELOG.md` | Running log of decisions, builds, milestones — append-only |
| `docs/decisions/` | Architecture Decision Records (ADRs). Each major technical decision gets its own file |
| `docs/playbooks/` | Operational playbooks: discovery scripts, outreach templates, onboarding checklists |

---

## Current phase + active priorities

**Phase 0 — Foundation (Weeks 1–2 of timeline)**

Active priorities, in order:
1. **SEMCO case study** — the keystone artifact. Every downstream deliverable depends on it.
2. ~~**Multi-tenant platform refactor**~~ ✅ **Done 2026-05-14.** Skeleton extracted from the SEMCO source repos into `src/app/`, `migrations/`, `tests/`. CI is wired (`.github/workflows/ci.yml`) and the tenant isolation suite hard-fails on RLS leaks. See [docs/CHANGELOG.md](docs/CHANGELOG.md) for the full extraction report.
3. **Landing page** at `traceflow.app` — one offer (LLR), the SEMCO case study, Calendly link
4. **LinkedIn rewrite** — new positioning, featured section with case study
5. **Onboarding form** (Tally) — collect everything in `docs/playbooks/new-client-onboarding.md` §"What you need from the client"
6. **Contract templates** — LLR and SIA versions, including the v1.1 IP-separation clause
7. **First 100-prospect list** + outreach kickoff
8. **Phase 0 actual build (post-skeleton):** Twilio missed-call flow (`webhooks/twilio.py` is a stub), GoHighLevel adapter (`adapters/ghl.py` raises `NotImplementedError`), qualifier prompt + golden tests.

**Blocked or deferred:**
- All client-facing UI work (Phase 2+)
- SIA-specific marketing (internal only until Phase 1 complete)
- Midas/Flux/Bytekeep work (hard freeze until $3K MRR)

---

## How to behave during sessions

- Default to brevity. Andy reads carefully; doesn't need walls of text.
- Use tables when comparing options; prose when explaining one thing.
- When generating code: include the imports, include the imports' versions if it matters, and explain anything non-obvious in a one-line comment.
- When generating docs: respect the structure of existing files (don't introduce a new style every time).
- When uncertain about scope: ask one clarifying question, then proceed with stated assumptions.
- Update CHANGELOG.md after any session that produced meaningful artifacts, decisions, or code changes.

---

**End of CLAUDE.md.** Load relevant skill files next based on the task.
