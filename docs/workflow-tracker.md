# TraceFlow Workflow Tracker

Phase 0 → first $1K MRR → Phase 2 maturity → strategic fork. Check items off as you complete them.

**Legend:** `[x]` done • `[ ]` not done • **bold** = milestone or revenue gate

**Last reconciled:** 2026-07-06, against session memory + live checks (traceflow.app fetch, GitHub/Render/Supabase state). See `[verified]` notes below for items corrected from the original draft.

---

## 0. Foundation

- [x]  PRD v1.1 finalized
- [x]  Brand name locked: TraceFlow
- [x]  Domain purchased: traceflow.app
- [x]  DNS records configured on Namecheap (MX, SPF, verification TXT)
- [x]  Repo context bundle generated (CLAUDE.md, skills, docs, playbooks)
- [x]  Brand collision analysis logged (USPTO check, decision to keep name)
- [x]  SEMCO repo refactored — features extracted, proprietary names scrubbed
- [x]  Google Workspace login completed + admin console set up
- [x]  DKIM activated (after Workspace first login)
- [x]  Test email sent + received from andy@traceflow.app
- [x]  Repo set up locally: extract bundle, `git init`, first commit
- [x]  GitHub Pro account active (paywall) — `[verified]` listed as active shared infra across the portfolio; both TraceFlow repos are already private under it
- [x]  Repo pushed to GitHub (private)
- [x]  Case study draft moved into `docs/case-study.md`
- [x]  CHANGELOG entry for founder-as-asset decision
- [x]  CHANGELOG entry for case study draft
- [x]  CHANGELOG entry for SEMCO refactor completion

---

## 1. Platform build (Phase 0 minimum)

The minimum-viable platform to onboard a first client.

- [x]  Supabase project provisioned + schema migrated
- [x]  Tenant resolver middleware working (sets `app.current_client_id`)
- [x]  Tenant isolation test suite — first version, passes
- [x]  Generic webhook handler (Layer 3) built and tested
- [x]  Monday adapter generalized to multi-tenant
- [x]  Shopify webhook handler generalized to multi-tenant
- [x]  First CRM adapter for whichever Client 1 uses (likely GHL or Zoho)
- [x]  Twilio missed-call webhook handler
- [x]  AI greeting generation (Anthropic API, templated prompts)
- [x]  AI qualification loop (multi-turn SMS, field extraction)
- [x]  Owner alert system (VIP keyword/value triggers)
- [x]  Daily digest cron job
- [x]  Adapter health-check cron (hourly)
- [x]  Health check endpoint `/health`
- [ ]  Sentry error monitoring connected (not needed for now)
- [ ]  UptimeRobot pinging health endpoint every 5 min (not needed for now)
- [x]  Render deploy live and stable
- [ ]  First end-to-end smoke test passes (synthetic missed call → SMS → qualification → CRM push, all under one client_id) — genuinely blocked: HubSpot Private App token still 401s (dev-account token, not a CRM portal token), and Twilio Toll-Free Verification was resubmitted 2026-07-06 after a 3-reason denial (business details, use-case mismatch, opt-in) — pending Twilio review

---

## 2. Sales infrastructure

- [x]  Stripe account live + payment links tested
- [x]  Calendly account configured (25-min discovery, Sat AM + weekday evenings)
- [x]  Loom account (Pro if quota becomes an issue)
- [ ]  1Password Business (or Bitwarden) for client credential sharing (paywall)
- [x]  Notion workspace for proposals + client tracker (need 2h work)
- [ ]  Tally onboarding form built (all 6 sections per playbook)
- [x]  Notion proposal template — LLR version
- [x]  Notion proposal template — SIA paid audit version
- [ ]  Contract template — LLR with IP-separation clause
- [ ]  Contract template — SIA audit
- [ ]  E-signature workflow (Stripe, HelloSign, or PandaDoc)

---

## 3. Marketing assets

- [x]  Case study #1 — anonymized draft
- [x]  Case study #1 numbers verified with the source company
- [x]  Case study #1 published on landing page
- [ ]  Case study one-pager exported as PDF (for post-reply sharing) — content exists (`case-study.md` full + one-pager), PDF design pass still pending
- [ ]  LinkedIn profile rewrite — positioning, headline, about section
- [ ]  LinkedIn featured section with case study link
- [x]  LinkedIn profile photo (clean, professional)
- [ ]  LinkedIn cover image (TraceFlow branding or surface-niche relevant)
- [x]  Landing page copy locked (use marketing-copy skill) — `[verified]` live fetch confirms finished hero copy ("Recover 25%+ of missed-call revenue in 30 days — or your first month is free"), case study section, no placeholder text
- [x]  Landing page built (Render Static, HTML/Tailwind or Astro)
- [x]  Calendly button: above the fold + bottom + sticky on mobile — `[verified]` 2026-07-06: switched from external link-out to a live inline embed (`andy-traceflow/30min`, confirmed 200) in the `#book` section; CTA also present in hero + pricing card. Sticky-on-mobile specifically not implemented — the button scrolls to the embed rather than floating; revisit only if mobile conversion data asks for it
- [x]  traceflow.app A record pointed at Render
- [x]  SSL active and verified — `[verified]` live `https://traceflow.app` fetch succeeds; custom domain has been live since 2026-05-26 and Render auto-issues/renews certs on custom domains
- [ ]  Privacy policy + terms (basic — use a generator, fine for Phase 0) — the `/sms-terms` page (built for Twilio TFV) covers SMS-specific privacy/consent only; there's no general site privacy policy or ToS, and the landing footer has no legal links yet
- [ ]  Analytics installed (Plausible recommended, or GA4)
- [ ]  Email signature with andy@traceflow.app + landing page link

---

## 4. Outreach pre-flight

- [ ]  GoHighLevel trial signed up + 30 min API doc skim — the GHL adapter itself is fully built and tested in code (Phase 0 default CRM, 40% recurring affiliate); this line item is specifically about *your own* live trial account + API doc read, which is still open
- [x]  HubSpot free account + 30 min API doc skim
- [ ]  LinkedIn Sales Navigator free trial activated
- [x]  Prospect tracker built (Notion or Airtable, columns per playbook)
- [ ]  First 100 prospects identified (filters: contractor + $1-10M + NV/AZ/CA/TX)
- [ ]  First 10 prospects deeply researched
- [ ]  First batch of 10 Looms recorded
- [ ]  First batch of 10 DMs sent

---

## See also

- [`docs/timeline-30-90.md`](./timeline-30-90.md) — the active near-term revenue timeline (30 days to first paying customer, 90 to $1K MRR), which supersedes PRD §15's original week-by-week pacing now that the platform build is done and everything left is GTM.
