# 30/90-Day Revenue Timeline

**Set:** 2026-07-06
**Supersedes (for near-term pacing only):** `docs/PRD.md` §15's original Phase 0-3 week estimates. The PRD's phase content (what to build, pricing, ICP, risks) is still the source of truth — only the calendar changed, because engineering finished far ahead of the original plan.

## Why this timeline is tighter than the original

PRD §15 assumed build and GTM would run roughly in sequence over ~24 weeks, with "Phase 0 — Foundation" itself taking weeks 1-2. In reality, the full platform — LLR runtime, 4 CRM adapters (GHL/Monday/generic/HubSpot), recovered-revenue capture, monthly performance report, and the self-hosted `/admin` console — shipped to production on **2026-06-17**. As of this timeline, **zero prospects have been contacted and no GTM artifact has shipped.** Every day in the next 90 is GTM time, not build time. That's the whole reason "90 days to $1K MRR" (already the PRD's M3 target) now starts from a foundation that's already done, instead of one still being built.

## The two gates

- **Day 30 — by 2026-08-05: first paying customer.** Signed contract + deposit collected (Stripe payment link executed), not a verbal yes.
- **Day 90 — by 2026-10-04: $1,000+ MRR.** Realistic path: 2 Founding Partners ($397/mo × 2 = $794) + 1 Standard client ($597/mo) = **$1,391 MRR** — clears the gate with buffer rather than landing exactly on it. Founding Partner pricing is capped at clients 1-2 per the pricing table; client 3 onward is Standard.

---

## Week 1 (Jul 6–12) — GTM foundation sprint

- [ ] Twilio Toll-Free Verification approval (resubmitted 2026-07-06 after 3-reason denial; no action pending on our side, just a waiting gate — the live-pilot smoke test is blocked on this)
- [ ] LinkedIn profile rewrite + 3 pinned posts
- [ ] Tally onboarding form (all 6 sections per playbook)
- [ ] LLR + SIA contract templates with v1.1 IP-separation clause
- [ ] E-signature workflow wired (HelloSign / PandaDoc / Stripe)
- [ ] First 100-prospect list (LinkedIn Sales Navigator trial; filters: contractor + $1-10M revenue + NV/AZ/CA/TX)
- [ ] Close remaining landing-page gaps: Calendly sticky-mobile behavior, favicon, OG image, basic privacy/terms page

## Week 2 (Jul 13–19) — Outreach opens

- [ ] 10 prospects deeply researched
- [ ] 10 Looms recorded (personalized missed-call audits)
- [ ] First 30 DMs sent (10/day × 3 days minimum)
- [ ] Book first discovery calls off warm replies

## Week 3 (Jul 20–26) — Discovery + close attempts

- [ ] Remaining ~70 DMs sent (10/day)
- [ ] 5-10 discovery calls run (25-min script per PRD §4)
- [ ] First proposal(s) sent (Notion template, LLR or SIA-audit)

## Week 4 (Jul 27–Aug 5) — Close

- [ ] **Close Founding Partner #1** — signed contract + deposit ($1,500 setup + $397/mo)
- [ ] Kick off onboarding (config rows, credentials, field mappings, Twilio number provisioning)

**Gate check — Day 30 (2026-08-05): first paying customer signed.**

---

## Days 31-60 (Aug 6–Sep 4) — Deliver + keep prospecting

- [ ] Launch LLR live for client #1 within ~2 weeks of signing
- [ ] Continue outreach in parallel — close Founding Partner #2
- [ ] Capture first 30-day recovered-revenue numbers for case study #2
- [ ] File USPTO Class 035 trademark (due within 30 days of client #1's first payment — target ~Sep 4)

## Days 61-90 (Sep 5–Oct 4) — Push past $1K MRR

- [ ] Close client #3 at Standard pricing ($2,500 setup + $597/mo)
- [ ] Both Founding Partner clients live and billing on schedule

**Gate check — Day 90 (2026-10-04): $1,000+ MRR** (2 Founding + 1 Standard = $1,391 MRR target).

---

## Standing risks (unchanged from PRD §14 — still apply)

- First prospects don't close → wider top-of-funnel (100, not 20) is already the plan above
- Day-job time conflict → block evenings 7-9pm only, Saturdays sales-only, Sundays off
- **Hard rule carried forward: zero Midas/Flux/ByteKeep work until $3K MRR** — this 90-day window is entirely LLR-focused
