# New Client Onboarding Playbook

**Goal:** Take a signed contract to a launched, paying, working client in 14-28 days with minimal founder time per onboarding.

**Companion skill:** `.claude/skills/client-onboarding/SKILL.md` (covers the technical patterns; this playbook is the operational checklist)

---

## The 28-day timeline at a glance

```
Day 0          Contract signed, 50% setup deposit invoiced
Day 0-3        Tally form completed by client
Day 3-5        Tenant provisioned in TraceFlow platform
Day 5-7        Field mappings configured, CRM connected
Day 7-10       Prompts tuned, knowledge base loaded (if SIA Module C)
Day 10-14      End-to-end smoke tests
Day 14-17      Soft launch (10%-50% routing)
Day 17-21      Full launch (100% routing)
Day 21         Handoff package delivered, 50% final invoice
Day 21-30      Active monitoring, edge cases captured
Day 30         Check-in call, metrics captured, testimonial requested
```

If you hit Day 21 and haven't launched, something's wrong. Audit which step stalled and fix the process.

---

## Day 0 — Signing

When the contract is signed:

- [ ] Receive 50% setup deposit via Stripe
- [ ] Send welcome email with:
  - Onboarding form (Tally) link
  - 1-min Loom: "What happens next"
  - Andy's direct contact (Slack Connect or email)
  - Calendar link for optional 15-min kickoff call
- [ ] Add to client tracker (Notion or Airtable)
- [ ] Create their entry in the client_configs YAML file
- [ ] Set 28-day countdown reminder

## Days 0-3 — Information collection

**Client action:** Complete the Tally onboarding form (see Section "Information Collection Fields" below for full list)

**Your action:**
- Day 1: Send the form
- Day 2: No nudge yet
- Day 3: Friendly nudge if not complete: "Hey [name] — just bumping the onboarding form. Anything blocking? Happy to hop on a 10-min call if helpful."

If they're still stuck at Day 5, schedule a call and fill it out together. Don't let collection drag past Day 7.

## Days 3-5 — Tenant provisioning

When the form is complete:

- [ ] Export form responses → populate `client_configs/<slug>.yaml`
- [ ] Run `./scripts/onboard-client.py --config client_configs/<slug>.yaml`
- [ ] Verify outputs:
  - [ ] `client_id` UUID assigned
  - [ ] Twilio number allocated
  - [ ] Webhook signing secrets generated
  - [ ] Default prompt templates loaded
- [ ] Initiate secure credential exchange with client (1Password share or Bitwarden Send):
  - [ ] Send them: their webhook signing secrets (so they can configure their CRM)
  - [ ] Request from them: CRM API credentials, Shopify token if applicable

## Days 5-7 — Integration configuration

- [ ] Add encrypted CRM credentials to `client_configs.crm_credentials`
- [ ] Get a sample lead record from their CRM (any format — JSON, CSV row, screenshot)
- [ ] Run `./scripts/suggest-field-mappings.py --client-id <uuid> --sample-record <path>`
- [ ] Review AI-suggested mappings; correct anything wrong
- [ ] Apply mappings via SQL or admin script
- [ ] Configure their main business line to forward unanswered calls to their TraceFlow Twilio number (they do this with their VoIP provider; document the steps if needed)
- [ ] Test push: create a synthetic Lead → verify it lands correctly in their CRM with all fields mapped

## Days 7-10 — Prompts and knowledge base

**Always:**
- [ ] Customize greeting template with their brand/tone variables
- [ ] Customize qualifier prompt with their service types, business hours, FAQs
- [ ] Configure VIP keywords from their stated criteria
- [ ] Run 5 golden test conversations; iterate prompts until they pass

**If SIA Module C (knowledge base) is included:**
- [ ] Ingest their TDS sheets / install guides / FAQs through the extraction pipeline
- [ ] Run 10 sample queries; verify chatbot answers correctly
- [ ] Tune retrieval (chunk size, top-k, similarity threshold) as needed

## Days 10-14 — End-to-end smoke tests

Run through the [Integration Test Checklist](#integration-test-checklist) below. **Every box must be checked before soft launch.** No exceptions.

If anything fails, fix and re-test the affected path.

## Days 14-17 — Soft launch

- [ ] Configure client's VoIP to route 10% of missed calls to TraceFlow (3 days)
- [ ] Monitor every conversation manually for first 24 hours
- [ ] Daily 5-min check-in with client (Slack message or email)
- [ ] Capture any edge cases as future test scenarios
- [ ] At Day 17: if no critical errors, bump to 50% routing

## Days 17-21 — Full launch

- [ ] Day 17-18: 50% routing, daily spot-check
- [ ] Day 19-20: 100% routing, daily spot-check
- [ ] Day 21: Confirm system stable, switch to weekly cadence

## Day 21 — Handoff delivery

Send the handoff package via email:

- [ ] **Welcome email** with everything below linked
- [ ] **5-min "What's running" Loom** — plain-English overview
- [ ] **2-min "What to do when X happens" Loom** — common scenarios
- [ ] **Runbook PDF** (1-2 pages) — written reference
- [ ] **Dashboard URL** (or email-digest sample if Phase 0-1)
- [ ] **Direct support channel** info
- [ ] **30-day check-in calendar invite**
- [ ] **Final 50% setup invoice + first month retainer invoice**

## Days 21-30 — Active monitoring

- [ ] Watch dashboard daily (5 min)
- [ ] Respond to any client questions same-day
- [ ] Capture metrics in their performance log:
  - Calls routed
  - SMS conversations initiated
  - Leads qualified
  - Leads pushed to CRM
  - Owner alerts triggered
  - Attributed revenue (when known)
- [ ] Note any prompt or config tweaks needed

## Day 30 — Check-in call

30-min call:

- [ ] Present their first-month metrics
- [ ] Get qualitative feedback (NPS-style: "0-10, how likely to recommend?")
- [ ] Capture any pain points or wishes
- [ ] **Request testimonial in writing + logo usage permission for case study**
- [ ] Identify expansion opportunities (Phase 2 modules, SIA cross-sell)
- [ ] Schedule quarterly review for ~Day 90

---

## Information collection fields (Tally form)

Build the form once; reuse for every client. Required vs optional marked.

### Section 1 — Business info (required)
- Legal business name *
- DBA (if different)
- Primary business address *
- Service area zip codes (multi or comma-separated) *
- Business hours by day (open/close + closed days) *
- Timezone *
- Owner full name *
- Owner cell phone *
- Owner email *
- Day-to-day point of contact name *
- Day-to-day point of contact phone *
- Day-to-day point of contact email *
- Business website URL *
- Google Business Profile URL *

### Section 2 — Lead/sales context (required)
- Average job/ticket size (dropdown: <$2K, $2-5K, $5-15K, $15-50K, $50K+) *
- Top 3 service categories (free text or multi-select) *
- Typical lead source mix (% web, % phone, % referral, % Google, % FB, % other) *
- Estimated total calls per week *
- Estimated missed calls per week *
- What currently happens to after-hours leads? *
- Average response time to a new inquiry *
- 5-10 FAQs you get from new leads (this tunes the qualifier) *
- "Tell me about your worst lost-lead story" (qualitative — captures stakes)

### Section 3 — Tech inventory (required)
- VoIP / phone system provider *
- Website CMS (WordPress, Squarespace, Shopify, custom) *
- Existing CRM (name + admin email) — leave blank if none *
- Google Business Profile owner email *
- Shopify store URL (if applicable)
- Domain registrar (if we need DNS access)
- Any other systems we should know about (free text)

*Credentials are NOT collected via form. Sent separately via 1Password Business share after form completion.*

### Section 4 — Brand (required)
- Logo upload (PNG/SVG, transparent background preferred) *
- Brand primary color (hex code) *
- Tone of voice (radio: formal / professional-friendly / casual / industry-specific) *
- Example of a great customer interaction (text paste, email screenshot upload, or text/voicemail transcript) *
- Words/phrases to ALWAYS use (e.g., "estimate" vs "quote")
- Words/phrases to NEVER use

### Section 5 — Module-specific (only show if SIA included)

For Module A (Lead-to-CRM Pipeline):
- Current lead intake workflow (Loom or written description)

For Module B (Quote Acceleration):
- Sample quote (PDF or photo) *
- Quote turnaround time today
- Quote turnaround time goal

For Module C (Knowledge Engine):
- TDS sheets (PDF uploads, multiple) *
- Install guides (PDF uploads, multiple)
- FAQ docs or website FAQ page URL
- Catalog (PDF or CSV)

For Module D (Owner Dashboard):
- Top 3-5 KPIs you care about most *
- Current reporting tools (if any)

For Module E (Internal Knowledge Base):
- Current SOPs (Loom links or written docs)
- Top 10 questions your team asks repeatedly

### Section 6 — Logistics
- Preferred communication channel for ongoing support (email / Slack Connect / text) *
- Any vacation or out-of-office during the 30-day onboarding window?
- Anyone else who should be CC'd on launch communications?

---

## Integration test checklist

**Every box must pass before soft launch. No skipping.**

### Provisioning
- [ ] `clients` row exists, status = 'active'
- [ ] `client_configs` row exists, all required fields populated
- [ ] Twilio number purchased and assigned to client_id
- [ ] Webhook signing secrets generated and stored encrypted
- [ ] RLS policies active on all tenant-scoped tables (verified by tenant isolation test suite)

### Twilio (missed call → SMS)
- [ ] Test inbound call to Twilio number from external phone
- [ ] Verify event logged in `events` table with correct client_id
- [ ] Verify outbound greeting SMS sent within 60 seconds
- [ ] Verify SMS arrives at test phone with correct content
- [ ] Verify lead row created in `leads` table

### CRM push
- [ ] Trigger a synthetic Lead (via admin script)
- [ ] Verify adapter is called correctly
- [ ] Verify all field mappings applied (custom fields included)
- [ ] Verify the lead appears in client's CRM with all data
- [ ] Verify `lead.external_id` and `lead.pushed_to_crm_at` populated

### AI qualification
- [ ] Send 5 sample inbound responses simulating a real lead
- [ ] Verify multi-turn conversation works
- [ ] Verify fields are extracted (service_type, sqft, budget, timeframe)
- [ ] Verify conversation ends naturally after qualification or disqualification
- [ ] Verify no message exceeds 160 chars
- [ ] Verify tone matches client's configured voice

### VIP / owner alerts
- [ ] Trigger a lead matching VIP keywords
- [ ] Verify owner receives SMS or email alert
- [ ] Verify alert content includes key lead details
- [ ] Verify a non-VIP lead does NOT trigger an alert

### Knowledge base (if SIA Module C)
- [ ] Run 10 representative questions through the KB
- [ ] Verify answers are accurate and cite source documents
- [ ] Verify chatbot says "I don't know" gracefully when content isn't covered

### Daily digest
- [ ] Trigger digest generation manually
- [ ] Verify it's emailed to designated owner contact
- [ ] Verify it includes day's leads, metrics, and any alerts
- [ ] Verify formatting renders correctly in email client

### Tenant isolation
- [ ] Run full `tests/test_tenant_isolation.py` suite
- [ ] Zero failures

### Operational
- [ ] Sentry capturing errors correctly
- [ ] Logs include `client_id` on all tenant-scoped events
- [ ] Health check endpoint returns 200
- [ ] Adapter health check passes for client's CRM

---

## Common stalls and how to fix them

**"Client hasn't completed the form after 5 days."**
- Schedule a 30-min call, fill it out together. Don't let collection drag.

**"Their CRM is something obscure I don't have an adapter for."**
- Use the generic webhook config (Layer 3 in adapter-pattern skill). Build a full adapter only if 2+ more clients use the same system.

**"Their VoIP provider doesn't support conditional forwarding."**
- Recommended workaround: get them a new Twilio number, publish it as their main; forward to their landline; TraceFlow catches unanswered. Or recommend switching to OpenPhone/Dialpad.

**"Their website is hard to integrate a chat widget into."**
- Skip chat for Phase 1; focus on phone + form. Phase 2 add a chat module.

**"Soft launch shows weird AI behavior."**
- Capture the exact conversations as test scenarios. Fix prompts, re-test, redeploy. Don't move to full launch until weird behavior is gone.

**"Client wants a feature mid-onboarding."**
- Note it. If it's truly required for launch, scope it and adjust timeline. If it's nice-to-have, log as a Phase 2 candidate.

**"They went silent after onboarding form."**
- One nudge at Day 3, one at Day 7. If still silent, schedule a 15-min "is this still a priority?" call. Sometimes clients sign and then get cold feet.

---

## What "launched" actually means

A client is officially launched when ALL of these are true:

1. Phone forwarding is live at their end
2. 100% of missed calls route to TraceFlow
3. All integration tests passed within the last 7 days
4. Handoff package delivered
5. Final invoice sent and paid
6. Client has acknowledged receipt of handoff materials
7. 30-day check-in is on the calendar

Until all 7 are true, the client is in onboarding. Don't count them in MRR or case study eligibility yet.

---

## Onboarding velocity targets

| Phase | Clients onboarded | Target time per onboarding |
|---|---|---|
| Phase 0 | 1-5 | 20-30 hours each |
| Phase 1 | 5-10 | 10-15 hours each |
| Phase 2 | 10-15 | 4-6 hours each |
| Phase 3 | 15-25 | 2-3 hours each |
| Phase 4 (if SaaS) | 25+ | <1 hour, self-serve |

If your time per onboarding isn't decreasing, the automation investment isn't paying off — pause and audit the process.
