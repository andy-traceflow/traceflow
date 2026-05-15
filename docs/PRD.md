# AAA Productized Service — Product Requirements Document

**Owner:** Andy (Founder)
**Version:** 1.1
**Last updated:** May 2026
**Status:** Pre-launch

### Changelog (v1.0 → v1.1)
- **NEW Section 7:** Platform Architecture & Operating Principles — codifies "configuration over customization" thesis, multi-tenant routing, three-layer integration model (Adapters / Field Mappings / Webhook Configs), canonical schema, and tenant isolation
- **Updated Section 8 (was 7):** Tech Stack — added `client_field_mappings` and `client_webhook_configs` tables; tightened off-boarding contract language to explicitly separate client data from Founder IP
- **Restructured Section 11 (was 10):** Automation Roadmap merged with UI Maturity Model (four-phase progression: No UI → Internal Admin UI → Client Portal → Self-Serve)
- **Updated Section 15 (was 14):** Added strategic fork at Month 12 (Path 1: service-heavy lifestyle business vs. Path 2: productized SaaS)
- **Updated Appendix C:** Tightened off-boarding contract clause with explicit IP retention language

---

## 1. Executive Summary

A productized AI automation service targeting surface, countertop, flooring, and pool resurfacing contractors ($1M–$10M revenue SMBs). The business sells two underlying solutions but markets one outcome at a time based on the prospect's most acute pain point, determined during discovery.

- **Solution 1 — Lead Leak Recovery (LLR):** stop revenue from leaking through missed calls, slow follow-ups, and after-hours leads.
- **Solution 2 — Software Integration & Automation (SIA):** save 10–20 hours/week of admin/operations work by connecting and automating the contractor's existing tools.

External marketing leads with LLR because the ROI is faster and the pitch is simpler. SIA is sold either as an expansion to LLR clients or to prospects who self-identify as having an ops/admin pain rather than a lead pain.

**Founder advantage:** real, lived implementation experience at SEMCO Surfaces (Shopify → Monday.com webhook, Lyro chatbot with 193-entry KB, FastAPI/Supabase/Render stack). This is the moat — most AAA competitors in this niche cannot demonstrate domain knowledge or shipped systems.

**Target by Month 6:** $3K–$5K MRR + $200–$500/mo passive (digital products + affiliate), 10–15 hours/week of active work.

---

## 2. Business Model Overview

### Revenue structure (per client)

| Component | Type | Example |
|---|---|---|
| Setup fee | One-time | $1,500–$5,000 |
| Monthly retainer | Recurring | $397–$1,497/mo |
| Affiliate commissions | Recurring (passive) | ~$119/mo per GHL client |
| Usage overages | Variable | Rare; only if AI calls exceed cap |

### Cost structure (per client)

| Cost | Borne by | Typical monthly |
|---|---|---|
| CRM (GoHighLevel, HubSpot, etc.) | **Client** | $50–$300 |
| Twilio SMS | **Client** | $10–$50 |
| Their existing phone/website | **Client** | already in budget |
| Your hosting (FastAPI/Render) | **You** | $5–$15 amortized |
| Your DB (Supabase) | **You** | $5–$10 amortized |
| AI API (Anthropic/OpenAI) | **You** | $15–$40 |
| **Net cost to you per client** | | **~$30–$60/mo** |

Target gross margin per client: **85–90%** before time invested.

### Three layers of income

1. **Active service revenue** (retainers + setup fees) — primary
2. **Affiliate revenue** (recurring kickbacks from recommended tools) — passive, disclosed
3. **Digital products** (Gumroad/Lemon Squeezy: templates, playbooks) — passive, launched Phase 3

---

## 3. Target Customer Profile (ICP)

### Primary ICP

- **Industry:** Surface contractors — countertops, flooring, tile, stone, pool resurfacing
- **Revenue:** $1M–$10M annual
- **Team size:** 5–50 employees
- **Geography:** Start NV/AZ/CA/TX (proximity to SEMCO, language alignment). Expand nationally after Month 6.
- **Tech maturity:** Has at least a website and a phone system. May or may not have a real CRM. Often using Excel + email + texts for ops.
- **Decision maker:** Owner OR Operations Manager. Bypass marketing managers unless they're the only contact.

### Secondary ICP (Phase 2 expansion)

- Pool builders and pool service companies
- HVAC contractors
- Roofing contractors
- General home services with $2M+ revenue

### Disqualifiers

- Under $500K revenue — can't afford retainer
- Over $20M revenue — likely already has internal IT or enterprise vendor lock-in
- Direct SEMCO competitors (geographic conflict; protect day job)
- Has hired an AAA in the past 6 months and is unhappy (poisoned well)

---

## 4. Discovery Framework — Which Solution to Pitch

The discovery call is 25 minutes. The goal is to determine the dominant pain point and pitch the matching solution.

### Discovery script (5 sections, ~5 min each)

**1. Business snapshot** (rapport + qualification)
- How long have you been in business?
- What's your typical job size / average ticket?
- How do leads usually come in — phone, web form, referrals, Google?

**2. Lead flow questions** (signals for LLR)
- How many calls do you estimate you miss per week?
- When a lead comes in after hours, what happens to it?
- How fast do you typically respond to a new inquiry?
- What's your rough close rate on inbound leads?
- Who handles initial lead intake — owner, office manager, sales?

**3. Operations questions** (signals for SIA)
- Walk me through what happens after someone says "yes, send me a quote."
- How do you track who's been quoted, who's pending, who's booked?
- Where does customer info live — CRM, spreadsheet, paper, email threads?
- How much time does [the person who handles admin] spend on data entry / status updates per week?
- What's the most repetitive task in your week?

**4. Tech inventory** (scoping)
- What software do you currently use? (CRM, accounting, scheduling, etc.)
- What does your website do — just info, or does it capture leads?
- Are you on Shopify, Squarespace, WordPress, custom?

**5. Decision close**
- If we could [solve dominant pain point], what would that be worth to you per month?
- Who else is involved in a decision like this?
- What's your timeline — this month, this quarter, exploring?

### Pitch decision matrix

| Pain signals | Pitch |
|---|---|
| 10+ missed calls/week, slow follow-up, after-hours leak | **LLR** |
| Decent lead handling, but owner/manager doing 15+ hrs/week of admin | **SIA** |
| Customer info scattered across email/text/notes, no single source of truth | **SIA (Knowledge/CRM module)** |
| High-ticket jobs ($5K+) with low conversion rate | **LLR** |
| Multiple disconnected tools, manual data entry between them | **SIA** |
| Both lead AND ops problems | Lead with **LLR** (faster ROI close), pre-frame SIA as Phase 2 |

### Pricing signal

- If they balk at $597/mo, they likely can't afford SIA either. Disqualify gracefully, suggest a self-serve tool, move on.
- If they don't blink at $597/mo, they're a candidate for the $997–$1,497 tier within 90 days.

---

## 5. Solution 1 — Lead Leak Recovery (LLR)

### Problem statement

Surface contractors lose 20–40% of inbound revenue to missed calls, slow follow-up, and unqualified leads consuming sales time. A typical $3M contractor with 100 inbound calls/week missing 20% loses 1,000+ qualified leads per year. At a 30% close rate and $4,500 average ticket, that's ~$1.3M in unrealized revenue.

### Value proposition

"We recover 25%+ of your missed-call revenue within 30 days, or your first month is free."

### What it does

1. **Missed call detection** — Twilio number forwards client's main line; if call isn't answered within 4 rings, system fires the recovery sequence.
2. **AI-powered SMS auto-reply** — within 30–60 seconds, caller receives a personalized text: "Hey, this is [Business]. Sorry we missed you — what can we help with? (Reply here, we'll get back fast.)"
3. **AI lead qualifier** — bidirectional SMS conversation collects: project type, square footage, timeframe, budget range, location, contact preference.
4. **CRM auto-population** — qualified lead lands in client's CRM with structured fields + full SMS transcript.
5. **Owner alert** — high-value or urgent leads (keywords: "today," "emergency," "$10K+") trigger an immediate text/email to the owner or sales lead.
6. **Daily digest** — every morning, the owner gets a one-page summary: leads captured, leads booked, leads pending, revenue pipeline added.
7. **After-hours web chatbot** — same qualification flow, embedded on the website for visitors outside business hours.
8. **Review request automation** — completed jobs automatically trigger a review request SMS/email after 3 days.

### Technical architecture (your side)

```
[Twilio missed-call webhook]
        ↓
[FastAPI endpoint on Render — multi-tenant]
        ↓
[Supabase: log call, identify client tenant]
        ↓
[Anthropic API: generate personalized greeting]
        ↓
[Twilio: send SMS]
        ↓
[Inbound SMS replies → FastAPI webhook]
        ↓
[Anthropic API: qualification logic, extract fields]
        ↓
[Supabase: store lead record]
        ↓
[CRM API (GHL/HubSpot/etc.): push lead]
        ↓
[Conditional: owner alert if qualifying signals]
        ↓
[Cron job: nightly digest email via Resend/Postmark]
```

### Deliverables (client-facing)

- Working SMS recovery on their main line (or dedicated Twilio number forwarded)
- Lead qualification chatbot deployed on their site
- CRM connected with auto-populated lead records
- Owner dashboard (Supabase-backed, simple Next.js or hosted Retool)
- Daily digest email
- Loom walkthrough video (5 min) explaining what's running and what to do when
- Written runbook (1 page) covering common issues

### Setup checklist

**Before kickoff** (client provides):
- [ ] Access to phone system / call forwarding settings
- [ ] CRM account (or accept your GoHighLevel referral)
- [ ] Website admin access for chatbot embed
- [ ] Business hours, service areas, average ticket, common service categories
- [ ] Existing FAQs / sales talking points (for qualifier prompt tuning)
- [ ] List of "VIP signal" keywords for owner alerts

**Build phase** (your side, Week 1–2):
- [ ] Spin up tenant in multi-tenant Supabase
- [ ] Configure Twilio number + forwarding
- [ ] Customize qualification prompts for their service categories
- [ ] Connect CRM API
- [ ] Configure digest email template with their branding
- [ ] Test missed-call → SMS → qualification → CRM end-to-end
- [ ] Deploy chatbot to website (script tag or iframe)

**Launch phase** (Week 3):
- [ ] Soft launch: redirect 10% of missed calls for 3 days, monitor
- [ ] Full cutover after pass
- [ ] Owner training Loom delivered
- [ ] Schedule 30-day check-in

### Pricing

- **Founding Partner (clients 1–2):** $1,500 setup + $397/mo
- **Standard:** $2,500 setup + $597/mo
- **6-month minimum** on retainer
- Setup includes up to 1,000 AI-handled interactions/mo; overage at cost + 20%

### Success metrics (track per client)

- **Recovery rate:** % of missed calls that result in an SMS conversation
- **Conversion rate:** % of SMS conversations that produce a qualified lead in CRM
- **Booked revenue attributed:** dollars of pipeline tagged to LLR-sourced leads
- **Owner alert response time:** how fast VIPs get touched
- Target by Day 60: recovery rate ≥40%, qualified lead conversion ≥30%, ROI ≥10x retainer

---

## 6. Solution 2 — Software Integration & Automation (SIA)

### Problem statement

Surface contractors run their ops on 5–8 disconnected systems: phone, email, Shopify or website lead form, accounting (QuickBooks), scheduling (Google Calendar / paper), customer notes (Excel or worse), maybe a CRM. The owner or office manager spends 15–25 hours/week manually moving data between systems, generating quotes, chasing status, and answering the same product/spec questions repeatedly.

### Value proposition

"We save your team 15+ hours/week of manual work and give you a single source of truth for every customer — typically pays for itself in saved labor within 60 days."

### What it does (modular — pick what fits the client)

**Module A — Lead-to-CRM Pipeline** (the foundation)
- Website form / Shopify orders / inbound emails all flow into one CRM record
- Automatic deduplication and contact enrichment
- Routing rules: leads assigned to the right person based on service type or zip

**Module B — Quote/Estimate Acceleration**
- AI-assisted quote prep: pulls product specs, calculates per-sqft pricing, generates branded PDF
- Reuses SEMCO Liquid invoice template architecture (category grouping, per-sqft cost rollups)
- Approval workflow → automatic send to customer with e-sign

**Module C — Knowledge Engine (the Lyro-style chatbot)**
- AI chatbot trained on the contractor's product catalog, TDS sheets, install guides, FAQs
- Embedded on website + accessible to internal staff for product lookups
- Direct reuse of the SEMCO 193-entry KB methodology

**Module D — Status & Follow-up Automation**
- Job status updates auto-sent to customers (scheduled, en route, completed)
- Review request after completion
- Re-engagement sequence for stalled quotes after 7/14/30 days
- Annual maintenance reminders for past customers

**Module E — Owner Dashboard**
- Single pane: open quotes, pipeline value, booked revenue, active jobs, review status
- Weekly performance digest

### Technical architecture (your side)

```
[Multi-source ingestion]
  Website forms → Webhook
  Shopify orders → Webhook (GraphQL pull for line items)
  Email → IMAP polling
  Phone → CallRail/Twilio webhook
        ↓
[FastAPI orchestration layer on Render — per-tenant routing]
        ↓
[Supabase: unified customer/lead/job schema]
        ↓
[Module routing based on event type]
  → CRM sync (GHL/HubSpot/Monday)
  → Quote generation (template + AI fill)
  → Knowledge base query (vector search on Supabase pgvector)
  → Status notification (Twilio/Resend)
  → Dashboard refresh
        ↓
[Owner dashboard: Next.js on Vercel or Retool]
```

### Deliverables (client-facing)

- All chosen modules deployed and connected
- Single CRM as the source of truth, with all sources flowing in
- Knowledge base chatbot live on site (if Module C selected)
- Owner dashboard URL with auth
- 30-min Loom training per major workflow
- Written SOPs for ongoing use (what to do when X happens)
- Quarterly review schedule baked into retainer

### Setup checklist

**Before kickoff** (client provides):
- [ ] List of all current tools + admin access
- [ ] Sample of: typical lead form submission, typical quote, typical order, typical customer record
- [ ] Product catalog or service list (for quote module)
- [ ] TDS sheets, install guides, FAQ docs (for knowledge module)
- [ ] Current SOPs or "how we do things" walkthrough (recorded Loom or live call)
- [ ] Designated internal owner who'll be trained

**Discovery audit** (paid, $497–$997):
- Inventory current systems and pain points
- Map current data flow (where info lives, where it gets stuck)
- Identify which modules deliver fastest ROI
- Produce written roadmap with module recommendations + cost estimate
- This is also the de-risking step for you — only quote a fixed price after the audit

**Build phase** (2–4 weeks depending on modules):
- [ ] Provision client tenant in multi-tenant Supabase
- [ ] Build/configure each chosen module
- [ ] Ingest knowledge docs (if Module C) using SEMCO Q&A extraction pipeline
- [ ] Connect all systems via webhooks/APIs
- [ ] Build owner dashboard with their KPIs
- [ ] Internal smoke tests + dry run with sample data

**Launch phase**:
- [ ] Run new system in parallel with old for 1 week
- [ ] Owner + admin training (recorded)
- [ ] Cut over fully
- [ ] 30-day, 60-day, 90-day check-ins

### Pricing

| Tier | Modules | Setup | Monthly |
|---|---|---|---|
| Core | A + B | $3,500 | $797 |
| Pro | A + B + C | $5,000 | $997 |
| Full Stack | A + B + C + D + E | $7,500 | $1,497 |

Paid audit ($497 for existing LLR clients, $997 for new prospects). Credited toward the build if they sign within 30 days.

### Success metrics

- **Hours saved per week** — measured via owner self-report at 30/60/90 days
- **Quote turnaround time** — before vs. after
- **Customer data unification rate** — % of customers with a complete CRM record
- **Knowledge base resolution rate** — % of chatbot queries handled without human intervention
- Target by Day 90: ≥15 hrs/week saved, ≥50% reduction in quote turnaround, ≥70% KB resolution rate

---

## 7. Platform Architecture & Operating Principles

This section captures the architectural thesis that determines whether this business scales to $5K MRR or strangles you at Client 5.

### 7.1 The core thesis: configuration over customization

**You are not building software for clients. You are configuring software for clients.**

The platform exists. You build it once during Phase 0 by refactoring the existing SEMCO stack. Every new client is a configuration exercise: insert their config rows, plug in their credentials, map their fields, customize their prompts. The product doesn't change. The client's experience of the product changes.

This is the difference between being a freelance developer (custom code per client) and being a product company that happens to deliver via service (one codebase, many tenants). The economic implications:

| | Freelance / Custom builds | Productized (your path) |
|---|---|---|
| Hours per client | 80–200 | 4–20 (declining) |
| Marginal cost to add a client | High | Near zero |
| Bug fixes | Per-client, repeated | Once, all clients benefit |
| Improvements | Per-client, repeated | Once, all clients benefit |
| Pricing model | Hourly or fixed-bid | Subscription |
| Scaling ceiling | ~5–8 clients before you break | 30–50+ before infra is the bottleneck |
| Exit value | $0 (you ARE the asset) | 3–5x ARR multiple |

### 7.2 The single inviolable rule

**If you find yourself maintaining two versions of the same thing, you've already broken the model.**

- Two prompt files for two clients → wrong. Should be one templated prompt with config variables.
- Two slightly different cron jobs → wrong. Should be one job iterating over clients with their schedules.
- Two deploy targets because Client A wanted a different version → wrong. Should be feature flags in a single deploy.

When you catch yourself doing this, stop, refactor, push the variability into the database, then continue. Saying yes to bespoke without pricing it as bespoke is how productized services slowly turn back into agencies, then back into freelancing. Watch for it.

### 7.3 Multi-tenant routing

One Render service runs the FastAPI app. One Supabase project holds shared tables. Every table has a `client_id` column. Every query filters by `client_id`. Every webhook routes to the right tenant based on identifiers in the incoming request.

```
[Same webhook URL for all clients]
        ↓
[FastAPI handler identifies tenant via payload]
        ↓
[Load that client's config]
        ↓
[Process with their settings, credentials, prompts]
```

Identification patterns by integration:

| Integration | How you identify the tenant |
|---|---|
| Twilio inbound SMS / missed call | The `To` number — unique Twilio number per client |
| Website chatbot | `client_id` query parameter or embed token in the chatbot script tag |
| Shopify webhook | Shop domain in the payload |
| CRM webhook (GHL, HubSpot) | `client_id` set as custom field or in webhook URL path |
| Email ingestion | `to` address using subdomains (`leads-client1@inbox.yourcompany.com`) |
| Scheduled cron jobs | Iterate over all active clients in `clients` table |

For path-based routing where supported:
```
https://api.yourcompany.com/webhooks/twilio/missed-call/{client_id}
https://api.yourcompany.com/webhooks/shopify/{client_id}
```
The `client_id` in the path is non-secret. Webhook signing secrets unique per client verify authenticity.

### 7.4 Canonical internal schema

Every lead in the system, regardless of source, looks the same internally:

```python
class Lead:
    client_id: UUID
    external_id: str          # ID in their CRM
    source_system: str        # 'ghl', 'hubspot', 'monday', 'manual', etc.
    contact_name: str
    phone: str
    email: str | None
    service_type: str         # 'countertop', 'flooring', 'pool_resurface', etc.
    sqft: float | None
    budget_range: str | None
    timeframe: str | None
    address: str | None
    notes: str
    conversation_transcript: list[dict]
    raw_payload: dict         # Always preserve original — invaluable for debugging
    created_at: datetime
```

This is the contract. All internal code reasons about leads in this shape. The mess of dealing with different CRMs lives at the edges, not in the middle.

### 7.5 The three-layer integration model

This is how the platform handles the reality that every client uses different systems, and even clients on the same system have different field schemas.

```
┌────────────────────────────────────────────────────────────┐
│ LAYER 1: ADAPTERS (translation layer)                      │
│ One adapter per supported system (GHL, HubSpot, Monday)    │
│ Speaks each system's native API; exposes uniform interface │
└────────────────────────────────────────────────────────────┘
                            ↓
┌────────────────────────────────────────────────────────────┐
│ LAYER 2: FIELD MAPPINGS (per-client config)                │
│ "This client's HubSpot calls 'square footage' a custom     │
│ field named 'project_size_sqft' — map our canonical        │
│ `sqft` to that"                                            │
└────────────────────────────────────────────────────────────┘
                            ↓
┌────────────────────────────────────────────────────────────┐
│ LAYER 3: WEBHOOK CONFIGS (long-tail integration support)   │
│ "When their unknown CRM fires this webhook, here's how     │
│ to parse it and what fields mean what"                     │
└────────────────────────────────────────────────────────────┘
```

**Layer 1 — Adapters.** Each supported CRM gets an adapter class with a uniform interface (`push_lead`, `update_lead`, `parse_webhook`). Concrete implementations (`GoHighLevelAdapter`, `HubSpotAdapter`, `MondayAdapter`) handle system-specific REST/GraphQL calls. Adapter selection happens at runtime based on the client's `crm_provider` config field.

**Layer 2 — Field Mappings.** Two clients on the same CRM can have wildly different schemas because of custom fields. The `client_field_mappings` table stores per-client translations from canonical field names (`sqft`, `service_type`) to their specific external field names. Adapters consult this table at runtime; field names are never hardcoded. Value transformations (e.g., `'countertop'` → `'Kitchen Counter'`) live in a `transform` JSONB column.

**Layer 3 — Generic Webhook Configs.** For long-tail systems where building a full adapter isn't justified (ServiceTitan, Buildertrend, homegrown CRMs), the `client_webhook_configs` table stores per-client parsing rules (JSONPath expressions, jq filters, or Python templates). The generic webhook handler loads the config and extracts fields dynamically. This is the same pattern Zapier/Make/n8n use under the hood.

### 7.6 Adapter inventory — what to build, in order

| Adapter | Build when | Notes |
|---|---|---|
| GoHighLevel | Phase 0 (default recommendation) | 40% recurring affiliate; contractor-friendly |
| Monday | Phase 0 (already have SEMCO code) | Reuse existing GraphQL integration |
| HubSpot | When first HubSpot client signs | ~20% of mid-market contractors |
| Generic Webhook Handler | Phase 0 (escape hatch) | Covers everything else via config |
| Email Parser | Phase 1 if needed | For clients with no CRM yet |
| Jobber / ServiceTitan | When 3+ clients use it | Don't build speculatively |

First 10 clients are realistically served by the first four. Build the fifth and beyond only when paid demand justifies it.

### 7.7 AI-assisted onboarding (the leverage move)

During each client's onboarding, the field mapping step is where time goes. The shortcut: feed Claude a sample record from the client's CRM with the prompt "Map these external fields to our canonical schema." Claude returns a draft mapping JSON. You review, correct edge cases, save to the database.

What would be a 2-hour mapping session becomes 20 minutes. This is a Phase 2 build (CLI tool), upgraded to a UI tool in Phase 3 (see Section 11).

### 7.8 Tenant isolation — non-negotiable

The single biggest multi-tenant mistake: forgetting the `client_id` filter on a query. Three defenses, all required:

1. **Supabase Row Level Security (RLS).** Every tenant-scoped table has an RLS policy enforcing `client_id = current_setting('app.current_client_id')::uuid`. Middleware sets this on every request. Even a buggy query can't return another tenant's data.
2. **Isolation tests in CI.** A test suite runs every API endpoint with Client A's auth and asserts it can never see Client B's data. Runs on every commit. Failure blocks merge.
3. **Audit logging.** Every write operation logs `client_id` + `actor` + `operation`. If something does leak, you can prove what happened and when.

One leak = catastrophic trust event. The defenses are cheap; the failure mode is not.

### 7.9 Schema additions (added in v1.1)

Two new tables support the three-layer integration model:

```sql
create table client_field_mappings (
  client_id uuid references clients(id),
  integration text,           -- 'crm', 'shopify', 'website_form'
  canonical_field text,       -- our internal name: 'sqft', 'service_type'
  external_field text,        -- their name: 'project_size_sqft'
  external_field_type text,   -- 'standard', 'custom_field', 'custom_property'
  transform jsonb,            -- optional value transformation rules
  primary key (client_id, integration, canonical_field)
);

create table client_webhook_configs (
  id uuid primary key,
  client_id uuid references clients(id),
  webhook_slug text,           -- e.g., 'jobber-lead-created'
  parser_type text,            -- 'jsonpath', 'jq', 'python_template'
  field_extractors jsonb,      -- canonical_field → extraction expression
  unique (client_id, webhook_slug)
);
```

Both tables are tenant-scoped under RLS.

---

## 8. Tech Stack — Your Side vs. Client Side

### Your infrastructure (you own, you pay)

| Category | Tool | Cost | Notes |
|---|---|---|---|
| Code hosting | GitHub Pro | $4/mo | Private repos, one per client + main template |
| App hosting | Render (Starter tier) | $7/mo per service | Multi-tenant; one service handles many clients |
| Database | Supabase Pro | $25/mo total | Shared instance, RLS-isolated per client; upgrade to multi-project when >10 clients |
| AI API | Anthropic + OpenAI | ~$50–$200/mo total | Variable with client count |
| Email sending | Resend or Postmark | $20/mo | Transactional only |
| Domain + email | Namecheap + Google Workspace | $20/mo | Branded domain |
| Sales tools | Calendly, Loom, Notion | ~$40/mo | Calendly Pro, Loom Business |
| Invoicing | Stripe (free) + Stripe Tax | 2.9% + 30¢ | Invoicing clients |
| Outreach (temporary) | LinkedIn Sales Navigator | $99/mo first 3 mo only | Cancel after warm pipeline established |
| Monitoring | UptimeRobot (free) + Sentry (free tier) | $0 | Basic uptime + error tracking |
| **Total fixed overhead** | | **~$150–$250/mo** | Independent of client count |

### Client infrastructure (they own, they pay)

| Category | Required/Optional | Recommended tool | Cost to client |
|---|---|---|---|
| CRM | **Required** | GoHighLevel | $97–$297/mo |
| SMS provider | **Required (LLR)** | Twilio | ~$0.008/SMS + $1/mo per number |
| Phone forwarding | **Required (LLR)** | Their existing carrier | $0 (config only) |
| Website / form host | **Required** | Their existing site | $0 incremental |
| Email platform | **Required** | Their existing | $0 incremental |
| Calendar | Optional | Google Workspace | already paid |
| Shopify | Optional (SIA Module B) | Their existing | already paid |
| Document storage | Optional (SIA Module C) | Google Drive / Dropbox | already paid |
| Review platform | Optional | Google Business Profile | free |

### Critical ownership rules

1. **Every account is in the client's name and on the client's card.** You configure under their login.
2. **You do not store client account passwords.** Use 1Password shared vaults with limited access, or have them grant you team-member access where possible.
3. **All AI API calls go through your account**, billed back to them as part of the retainer. You control rate limits, costs, and model choice.
4. **Backups:** every client's Supabase tenant is backed up nightly to your S3 bucket. Restore is part of the retainer SLA.
5. **Off-boarding clause in every contract (v1.1 — tightened language):** Upon termination, Client receives within 30 days: (a) a full CSV export of all Client-originated business records — leads, customer records, communication transcripts, knowledge base source documents — and (b) 30 days of read-only access to the production tenant for verification. The underlying software, code, prompts, integrations, schemas, automation logic, and infrastructure configurations remain the sole intellectual property of Founder and are not transferred upon termination.

This separation is intentional. Clients walk away with **their data** (leads, conversations, customer records — what was always theirs). The **system that processed that data** (your code, prompts, adapters, multi-tenant infrastructure) stays yours. This is the same model as every B2B SaaS: cancel HubSpot and you get a contacts CSV, not HubSpot's source code. Result: legitimate earned switching cost (rebuilding the system requires another vendor or in-house dev) without hostage-taking (their data is portable on demand). Market this open exit aggressively — it's a competitive weapon against AAA competitors who trap clients.

### Affiliate disclosure (legal/ethical)

Every contract includes a one-line disclosure: *"Founder may receive affiliate compensation from third-party platforms recommended during engagement, including but not limited to GoHighLevel."* Standard practice; nobody objects.

---

## 9. End-to-End Process Map

The full lifecycle from prospect identification to ongoing retainer client.

### Stage 1 — Prospect identification (ongoing)
- LinkedIn Sales Navigator searches (industry + revenue + geography filters)
- List of 100 prospects/week added to outreach CRM (your GHL or Notion)

### Stage 2 — Outreach (Day 0)
- Personalized LinkedIn DM with free Loom audit (5 min recorded analysis of their website + Google profile + missed-call patterns)
- Async — happens during your evening hours

### Stage 3 — Interest signal (Day 1–7)
- Reply received → respond within 4 hours during your available windows
- Book discovery call via Calendly (Saturday morning or weekday evening slots only)

### Stage 4 — Discovery call (25 min)
- Run script from §4
- Determine LLR vs. SIA fit
- For SIA prospects: sell the paid audit ($497–$997) as the next step
- For LLR prospects: send proposal within 24 hours

### Stage 5 — Proposal (Day after discovery)
- Notion-hosted proposal (templated) with their name, problem statement, solution, pricing, ROI math
- Include 1–2 case study links + Calendly for follow-up

### Stage 6 — Close (Day 7–14 typical)
- Follow up at Day 2, Day 5, Day 10 if no response
- Contract via Stripe + simple DocuSign-style e-sign (HelloSign free tier or Stripe's built-in)
- 50% setup fee invoiced and paid before work begins

### Stage 7 — Kickoff (Day 0 of build)
- Send the pre-built onboarding form (Tally or Notion form) collecting all access/info from §5 or §6 checklist
- Schedule kickoff Loom or 15-min call
- Add to your client tracker

### Stage 8 — Build (Week 1–4 depending on solution)
- Provision tenant (this should be a one-command script — see §11)
- Configure per their inputs
- Internal QA
- Weekly progress Loom to client (asynchronous, no meeting needed)

### Stage 9 — Soft launch (Week 4 or 5)
- Parallel run with old system for ~1 week
- Monitor closely; iterate on AI prompts and edge cases

### Stage 10 — Full launch + handoff (Week 5 or 6)
- Cut over completely
- Owner training Loom delivered
- Runbook delivered
- Final 50% setup fee + first month retainer invoiced

### Stage 11 — Ongoing retainer (Month 2+)
- Weekly: automated monitoring (your side, ~30 min review per client)
- Monthly: send performance report (auto-generated from Supabase data)
- Quarterly: 30-min review call, identify expansion opportunities (Module upgrades)

### Stage 12 — Expansion (Month 3–6)
- LLR clients → pitch Knowledge Engine (SIA Module C)
- Module C clients → pitch full Ops Stack
- Reuse same closed-won relationship; no acquisition cost

---

## 10. Setup & Handoff Process — Detailed

### What you need from the client (onboarding form fields)

```
SECTION 1 — Business info
- Legal business name
- DBA (if different)
- Primary business address
- Service area zip codes
- Business hours (per day)
- Owner name + cell
- Designated point of contact + cell + email

SECTION 2 — Lead/sales context
- Average ticket size (dollar range)
- Top 3 service categories
- Typical lead sources (% breakdown)
- Estimated calls per week
- Estimated missed calls per week
- After-hours leads — what currently happens
- 5–10 FAQs you get from new leads

SECTION 3 — Tech access (you'll set up secure password sharing here)
- Phone system / VoIP provider + login
- Website CMS + admin login
- Existing CRM (if any) + admin access
- Google Business Profile owner email
- Shopify (if applicable) + collaborator invite
- Domain registrar (for DNS if needed)

SECTION 4 — Brand
- Logo (high-res PNG/SVG)
- Brand colors
- Tone of voice ("formal," "casual & friendly," etc.)
- Example of a great customer interaction (text or email) to model the AI on

SECTION 5 — Module-specific (SIA only)
- Product catalog (CSV or link)
- TDS / spec sheets (PDF uploads)
- Install guides (PDF uploads)
- Current SOPs (Loom or doc)
- Owner dashboard KPIs (top 3–5 metrics they care about)
```

### Handoff package (what the client gets at launch)

1. **Welcome email** with links to everything below
2. **5-min "What's running" Loom** — explains what the system does, in plain English
3. **2-min "What to do when X" Loom** — common scenarios (lead comes in, system seems off, want to change something)
4. **Runbook PDF** (1–2 pages) — written version of the Loom
5. **Owner dashboard URL** with login
6. **Direct support channel** — Slack Connect, dedicated email, or your support form
7. **30-day check-in calendar invite**

### SLA (in contract)

- Response time: business-day issues within 4 business hours
- Critical issues (system down): within 2 hours, any time
- Monthly performance report by 5th of each month
- Quarterly review call within first 5 business days of each quarter

---

## 11. Automation Roadmap & UI Maturity Model

This section merges two related questions: (1) how does setup time shrink per client over time? and (2) what UI gets built when, for whom?

**Both follow the same four-phase progression, triggered by client count and specific scaling pains — not calendar dates.** Building ahead of the trigger wastes time and kills momentum. Skipping the trigger creates operational chaos.

### Phase 1 — Manual + No UI (Clients 1–5)

- **Setup time:** 20–30 hours per client
- **Who configures the system:** you, directly via SQL or Supabase Studio
- **UI for you:** Supabase dashboard, raw SQL via a Notion library of snippets
- **UI for clients:** none — you handle everything; they get an email digest and Loom walkthrough
- **Onboarding mechanism:** Google Doc questionnaire + manual provisioning
- **Goal:** discover the patterns. Every onboarding teaches you what the eventual UI should look like.

**Build investment in this phase:** ~0 hours on UI. All energy goes into the platform itself, the prompt library, and the LLR/SIA delivery.

**Trigger to advance:** you've onboarded Client 3 and noticed you're doing the same SQL operations every time.

### Phase 2 — Templated + Internal Admin UI (Clients 5–10)

- **Setup time:** 10–15 hours per client
- **Who configures the system:** you (or a hired VA), via an internal admin UI
- **UI for you:** Retool, Tooljet, or Appsmith plugged into Supabase
- **UI for clients:** still none — communication via email digest, monthly performance report, and quarterly call
- **Onboarding mechanism:** Tally/Notion form auto-populates a YAML config; CLI script provisions tenant

**Required builds in this phase:**
- One-command tenant provisioner — Python script takes a YAML, creates Supabase config rows, allocates Twilio number, sets Render env vars. Target: 10-min execution.
- Tally onboarding form mapped to the YAML schema
- Prompt library: 10–15 base prompts with variable slots
- Module catalog: each SIA module documented as a deployable unit
- Runbook generator: takes client config, produces branded PDF runbook
- **AI-assisted field mapping CLI tool** — feed it a sample CRM record, Claude proposes the mapping, you approve and save
- **Retool admin UI** — client list, per-client detail pages, config editor, audit log, "add new client" wizard

**UI build choice:** **Do not build the admin UI from scratch.** Use Retool. ~20–40 hours of weekend work gets you 80% of what you'd build in 200 hours of Next.js. Cost: $10–$50/user/mo. Worth it.

**Trigger to advance:** clients are pinging you 10+ times/week for changes they could make themselves (business hours, away messages, VIP keywords).

### Phase 3 — Productized + Client Portal (Clients 10–15)

- **Setup time:** 4–6 hours per client
- **Who configures the system:** you for sensitive operations, clients for routine settings
- **UI for you:** mature admin UI (Retool, extended)
- **UI for clients:** lightweight portal — settings, lead inbox, dashboard, document uploads
- **Onboarding mechanism:** client portal walks them through guided setup with your oversight

**Required builds in this phase:**
- **Self-service client portal** — either Retool with external user auth (faster, ~30–50 hours) or Next.js + Supabase + Tailwind on Vercel (more polished, 80–150 hours)
- Auto-generated training Looms (consider Synthesia/HeyGen if budget allows; otherwise record once, parameterize client name in overlay text)
- CRM auto-connectors for top 3–5 platforms (OAuth flows, not manual API key entry)
- Knowledge ingestion pipeline — clients upload PDFs/docs via portal, system auto-extracts Q&As, loads into KB
- Cross-tenant monitoring dashboard for you (alerts when any client's system shows issues)

**What the client portal does:**
- Dashboard: today's leads, conversion stats, missed-call recovery rate, revenue attributed
- Lead inbox: all leads, qualification status, conversation transcripts, mark won/lost
- Settings: business hours, away-message overrides, VIP keywords, simple prompt text edits within bounds
- Documents: upload TDS/FAQ docs that auto-ingest into KB
- Team: invite users with permission tiers
- Billing: view invoices, update payment method

**What the client portal does NOT do (yet):**
- Field mapping editor (still too complex; you do this for them)
- CRM credential management (security-sensitive; you do this)
- Adding new integrations (you do this)
- Modifying underlying AI logic or prompt structure beyond simple text fields

**ROI threshold for justifying the portal build:** if a portal saves 5 hrs/week × $100/hr effective rate = $2K/mo value. Pays back the build inside 2–3 months at 8+ clients.

**Trigger to advance (optional):** you've hit your personal capacity ceiling. You're turning away $500K–$1M-revenue contractors because $1,500 setup doesn't pencil for them. You want a lower-tier offering that doesn't eat your time.

### Phase 4 — Strategic Fork (Clients 15+)

At this point a decision must be made deliberately. The two paths diverge significantly and require different investments.

**Path 1 — Stay service-heavy (the lifestyle business)**
- Cap at 15–25 clients, $1K–$2K ARPU
- $300K–$600K/year revenue, 70%+ margins, 20–30 hrs/week
- Deepen Phase 3 portal, hire a part-time VA/support, don't build self-serve
- You remain the irreplaceable operator; business doesn't have meaningful exit value
- Perfectly legitimate — most successful AAA founders end up here by choice

**Path 2 — Productized SaaS (the venture path)**
- Build a self-serve tier ($99–$199/mo, no setup fee)
- Multiple tiers, hundreds to thousands of clients
- $1M–$10M+ ARR ceiling
- Lower margins (more infra, more support, more engineering)
- Business has real exit value (3–5x ARR multiples)
- Requires hiring (engineer, support, sales), 40–50 hrs/week realistic
- Required build (200–500 hours): self-onboarding wizard, OAuth flows for all major CRMs, AI-assisted field mapping in-portal, tier-gated features, Stripe Billing automation, support documentation, in-product onboarding

**You don't decide this on Day 1.** You decide it around Month 12–18 based on (a) how the business actually feels operationally, (b) whether you enjoy the white-glove relationship work or find it draining, (c) whether market demand for self-serve has materialized, and (d) whether you have a runway to invest 6 months of platform engineering without revenue growth.

### Automation investment timeline summary

| Phase | Time to build | Pays off at | UI deliverable |
|---|---|---|---|
| Phase 1 → 2 (provisioner + admin UI) | 30–50 hours | Client 3 | Retool admin UI |
| Phase 2 → 3 (client portal + onboarding tooling) | 50–150 hours | Client 8 | Retool external users OR Next.js portal |
| Phase 3 → 4 (self-serve infrastructure) | 200–500 hours | Client 25 (or never) | Full self-serve product |

### The single most important UI warning

**Do not build the client portal in Phase 0 or Phase 1.** Every first-time founder wants to. It feels productive. It produces visible artifacts. It's also the #1 reason AAA founders don't sign clients in their first 90 days — they're polishing UIs instead of pitching contractors.

The discipline: clients first, configuration in the database, admin UI only when manual config breaks, client portal only when client requests break the admin UI workflow. Each layer is justified by a specific pain you've already felt, not anticipated.

---

## 12. Pricing & Margin Model

### Pricing tiers (recap)

| Tier | Setup | Monthly | Min term | Target |
|---|---|---|---|---|
| LLR — Founding Partner | $1,500 | $397 | 6 mo | Clients 1–2 only |
| LLR — Standard | $2,500 | $597 | 6 mo | Default offer |
| SIA — Core (A+B) | $3,500 | $797 | 12 mo | After audit |
| SIA — Pro (A+B+C) | $5,000 | $997 | 12 mo | After audit |
| SIA — Full Stack | $7,500 | $1,497 | 12 mo | Larger clients |
| Paid Audit (standalone) | $997 | — | — | Pre-SIA gate |

### Margin example — 5 mixed clients (Month 6 target)

| Client | Solution | Setup (already collected) | Monthly | Affiliate kickback | Your direct cost | Your net |
|---|---|---|---|---|---|---|
| 1 | LLR Std | $2,500 | $597 | $119 | $40 | $676 |
| 2 | LLR Std | $2,500 | $597 | $119 | $40 | $676 |
| 3 | LLR Std | $2,500 | $597 | $119 | $40 | $676 |
| 4 | SIA Core | $3,500 | $797 | $119 | $50 | $866 |
| 5 | SIA Pro | $5,000 | $997 | $119 | $60 | $1,056 |
| **Total** | | **$16,000 setup (already in bank)** | **$3,585/mo** | **$595/mo** | **$230/mo** | **$3,950/mo net** |

Subtract fixed overhead ($200/mo): **~$3,750/mo net by Month 6**.

### Expansion math

Same 5 clients, no new acquisition, expanded over Months 6–12:

| Client | Solution at M12 | Monthly | Notes |
|---|---|---|---|
| 1 | LLR + Module C | $1,094 | +$497 expansion |
| 2 | LLR + Module C | $1,094 | +$497 expansion |
| 3 | LLR Std | $597 | Stayed put |
| 4 | SIA Pro | $997 | Upgraded from Core |
| 5 | SIA Full Stack | $1,497 | Upgraded from Pro |
| **Total** | | **$5,279/mo** | + ~$595/mo affiliate |

**Month 12 target without new acquisition:** ~$5,500/mo net + $1,000–$2,000/mo digital products & content = $6.5K–$7.5K/mo.

---

## 13. Success Metrics

### Business metrics

- **MRR** (target: $1K M3, $3K M6, $5K+ M12)
- **Cash collected** (target: $5K M3, $15K M6, $35K+ M12 cumulative)
- **Net Revenue Retention** (target: ≥120% by M12)
- **Average revenue per client** (target: $597 M3 → $900 M9 → $1,200 M12)
- **Client count** (target: 2 M3, 5 M6, 8–10 M12)
- **Churn** (target: <5% monthly through M12)

### Operational metrics

- **Setup hours per client** (target: 20 → 12 → 6 by Phase)
- **Active hours per week** (target: never exceed 20)
- **Time from signed → launched** (target: 4 weeks → 2 weeks by Phase 3)

### Client-success metrics (drives retention)

- **LLR clients:** ≥10x retainer ROI within 60 days (recovered revenue / monthly fee)
- **SIA clients:** ≥15 hrs/week saved within 90 days
- **NPS** (asked quarterly): ≥50

---

## 14. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| First 2 prospects don't close | Medium | High (delays revenue) | Wider top-of-funnel; 100 prospects, not 20 |
| Platform competitor (Jobber/ServiceTitan) ships native AI feature | High | Medium | Deeper integration, custom KBs, things their generic product can't touch |
| Day-job conflict if SEMCO finds out | Low | High | Pitch non-competing geographies only; don't mention SEMCO directly in marketing beyond "I run AI ops at a Las Vegas surfaces company" |
| A client churns badly and trash-talks | Medium | Medium | 30-day money-back option on LLR; over-deliver in first month; pause acquisition until issue resolved |
| AI API costs spike on one client | Low | Medium | Per-client interaction caps; overage clause in contract |
| Your time gets eaten by support | Medium | High | Phase 2 automation investment must happen by Client 3; runbooks must be excellent |
| Crypto/Midas/Flux distracts you | High | High | Hard rule: zero Midas work until M6 + $3K MRR |
| Burnout from 9-5 + 20-hr/wk side work | Medium | High | Block evenings 7–9pm only, max. Saturdays sacred for sales. Sundays off entirely. |

---

## 15. Roadmap / Phasing

### Phase 0 — Foundation (Weeks 1–2)
- SEMCO case study written + designed
- Landing page live (single page, one offer)
- LinkedIn rewrite + 3 pinned posts
- Multi-tenant template repo refactored from existing SEMCO stack
- Onboarding form built (Tally)
- Notion proposal template
- Contract template (LLR + SIA)
- Stripe + Calendly set up

### Phase 1 — First Sales (Weeks 3–6)
- 100-prospect list built (LinkedIn Sales Navigator trial)
- 100 personalized DMs sent (10/day × 14 days)
- Loom audits recorded for warm replies
- Discovery calls run (target: 5–10)
- Close 2 Founding Partners

### Phase 2 — Deliver Pilots + Document (Weeks 7–12)
- Build & launch LLR for both pilots
- Document every artifact (prompts, schemas, configs) into reusable template
- Capture 30-day metrics for case study #2 and #3
- Begin LinkedIn content (3x/week build-in-public)

### Phase 3 — Scale to $3K MRR (Weeks 13–24)
- Raise to standard pricing
- Land 3 more clients
- Build Phase 2 automation tooling (provisioner script, prompt library)
- Launch first Gumroad product ($149–$299 DIY LLR playbook)
- Begin SIA audits with existing LLR clients

### Phase 4 — Compound (Months 7–12)
- Expand 2–3 existing clients to SIA modules
- Land 2–3 more standard clients
- Build Phase 3 tooling (client portal, auto-onboarding)
- Second and third digital products
- Content audience reaches ~2K relevant followers; inbound starts replacing outbound

### Phase 5 — Evaluate & decide (Month 12) — Strategic fork

By Month 12 you face a real decision that reshapes the next 2–3 years. Make it deliberately, with data from how the business actually feels operationally.

**Decision inputs:**
- Are you at $5K+ MRR? (If no, focus on growth, defer the fork.)
- Do you enjoy the white-glove client relationship work, or does it drain you?
- Has self-serve demand materialized? (Inbound leads asking "is there a cheaper DIY tier?")
- Can you fund 6 months of platform engineering without immediate revenue growth?

**Path 1 — Stay service-heavy (lifestyle business)**
- Cap at 15–25 clients, $1K–$2K ARPU
- $300K–$600K/year revenue, 70%+ margins, 20–30 hrs/week
- Deepen Phase 3 client portal, hire part-time VA/support
- You're the operator; business doesn't have meaningful exit value
- Best for: high quality of life, financial freedom at modest scale, low risk

**Path 2 — Productized SaaS (venture path)**
- Build a self-serve tier ($99–$199/mo, no setup fee)
- Multiple tiers, hundreds to thousands of clients
- $1M–$10M+ ARR ceiling
- Build Phase 4 infrastructure (200–500 hours)
- Hire (engineer, support, sales), 40–50 hrs/week
- Business has real exit value (3–5x ARR multiples)
- Best for: bigger upside, willingness to take on operational complexity and risk

Neither is wrong. Most founders who *think* they want Path 2 actually want Path 1 once they get there. The honest assessment matters more than the ambition.

This fork also intersects with Midas: at this point you have either funded runway and a quiet trading audience built in parallel (per the M0 plan), or you don't. Path 1 + Midas launch in M13–18 is the originally sketched plan. Path 2 likely defers Midas indefinitely because attention is finite.

---

## 16. Appendices

### Appendix A — Discovery call script (printable)
[Included in Section 4 above. Print and keep next to your laptop for the first 10 calls.]

### Appendix B — Tool comparison: CRM recommendation matrix

| CRM | Best for | Monthly | Your affiliate % | Notes |
|---|---|---|---|---|
| GoHighLevel | Default recommendation | $97–$297 | 40% recurring | Best affiliate; built for contractors |
| HubSpot | If they have existing HubSpot or want enterprise-grade reporting | $20–$800 | 20–30% Solutions Partner | Heavier setup |
| Monday.com | If they're already on it (like SEMCO) | $9–$19/user | Limited | You have direct experience |
| Pipedrive | If sales-heavy mid-size | $24–$79/user | ~20% | Less contractor-specific |
| Jobber/ServiceTitan | If they already use it for ops | $39–$300+ | None | Integrate, don't replace |

### Appendix C — Contract clauses (must-haves)

1. **Scope** — what's included, what triggers change-orders
2. **Term** — 6-mo (LLR) or 12-mo (SIA) initial; month-to-month after
3. **Account ownership** — client owns all third-party accounts (CRM, Twilio, phone system, website, Shopify)
4. **Affiliate disclosure** — single line: *"Founder may receive affiliate compensation from third-party platforms recommended during engagement."*
5. **Data export & IP separation on termination (v1.1 — tightened):**
   > *Upon termination, Client receives within 30 days: (a) a full CSV export of all Client-originated business records, including lead records, customer records, communication transcripts, and knowledge base source documents; and (b) 30 days of read-only access to the production tenant for verification. Client acknowledges that the underlying software, code, prompts, integrations, schemas, automation logic, infrastructure configurations, and any derivative outputs of Founder's processing (including but not limited to vector embeddings, prompt libraries, and adapter implementations) remain the sole intellectual property of Founder and are not transferred upon termination.*
6. **AI usage cap + overage** — interaction limit + cost+20% overage
7. **SLA** — response times defined (4 business hours standard, 2 hours critical)
8. **IP** — Founder's code/templates/platform stay Founder's; Client's business data stays Client's
9. **Mutual NDA**
10. **Limitation of liability** — capped at fees paid in last 6 months
11. **Net 15 payment terms** with 1.5%/mo late fee

### Appendix D — Outreach DM template

```
Subject (LinkedIn): Quick note about [Business] — recorded a 5-min audit

Hey [First name],

I run the AI ops at SEMCO Surfaces here in Las Vegas — we cut missed-lead loss to near zero last year using a system I built in-house. Saw [Business] and noticed [specific observation about their site / Google profile / response speed].

Recorded a 5-min Loom showing what I found and where I think the biggest leak is: [Loom link]

No pitch in it — just an audit. If anything resonates, my calendar's here: [Calendly]

Andy
```

### Appendix E — "What good looks like" — milestone definitions

- **Founding Partner closed** = signed contract + 50% setup paid + onboarding form submitted
- **Client launched** = system live in production + handoff package delivered + first lead processed
- **Case study captured** = 30-day metrics collected + written testimonial + permission to use logo
- **MRR threshold met** = retainers recurring for ≥2 consecutive months at the target level

---

**Next document needed:** SEMCO case study (1-page version + 2-page PDF). This is the next priority artifact — every Phase 0 deliverable depends on it.
