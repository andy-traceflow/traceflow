---
name: client-onboarding
description: The full client onboarding workflow — from signed contract to launched system. Load when building the onboarding form, the provisioning script, the kickoff process, the handoff package, or when onboarding an actual client. Includes the explicit field list, the provisioning steps, and the handoff deliverables.
---

# Client Onboarding

When a client signs, this skill governs every step from intake to launch. The goal: **less time per client with each successive onboarding.** Phase 0 onboarding takes 20-30 hours. By Client 6, it should be 4-6 hours.

See also: `docs/workflow-schema.md` § Client Onboarding for the high-level stage map.

## The information collection form (Tally)

Build this in Tally or Notion form. Required fields:

### Section 1 — Business info
- Legal business name
- DBA (if different)
- Primary business address
- Service area zip codes (multi-select or comma-separated)
- Business hours per day (open/close + closed days)
- Owner name + cell
- Designated point of contact + cell + email
- Business website URL
- Google Business Profile URL (helps verify identity)

### Section 2 — Lead/sales context
- Average ticket size (dropdown: <$2K, $2-5K, $5-15K, $15-50K, $50K+)
- Top 3 service categories (free text or multi-select)
- Typical lead sources (% breakdown: web, phone, referral, Google, Facebook, other)
- Estimated calls per week
- Estimated missed calls per week
- After-hours leads — what currently happens?
- 5-10 FAQs you get from new leads (these tune the qualifier)

### Section 3 — Tech access
*Note: do not store credentials in form. Use a secure share via 1Password Business or Bitwarden Send link in follow-up.*

- Phone system / VoIP provider name
- Website CMS name (WordPress, Squarespace, Shopify, custom)
- Existing CRM (if any) — name + admin email
- Google Business Profile owner email
- Shopify (if applicable)
- Domain registrar (for DNS if needed)

### Section 4 — Brand
- Logo upload (PNG/SVG)
- Brand primary color (hex)
- Tone of voice (radio: formal / professional-friendly / casual / industry-specific)
- Example of a great customer interaction (text or email screenshot to model AI on)

### Section 5 — Module-specific (SIA only)
- Product catalog upload (CSV or PDF)
- TDS / spec sheets (PDF uploads, multiple)
- Install guides (PDF uploads, multiple)
- Current SOPs (Loom video link or written doc)
- Owner dashboard top 3-5 KPIs they care about

## The provisioning script

```bash
# scripts/onboard-client.py
#
# Usage:
#   ./scripts/onboard-client.py --config client_configs/<slug>.yaml
#
# Reads the YAML config, provisions all tenant resources in one pass.

python scripts/onboard-client.py --config client_configs/example_client.yaml
```

The YAML config file (auto-populated from Tally form submission):

```yaml
# client_configs/example_client.yaml

slug: example-surfaces
business_name: "Example Surfaces Co."
tier: founding_partner  # or 'standard', 'pro', 'full_stack'

contact:
  owner_name: "Jane Doe"
  owner_phone: "+17025551234"
  point_of_contact_name: "John Manager"
  point_of_contact_email: "john@example.com"
  point_of_contact_phone: "+17025555678"

business_hours:
  monday: { open: "08:00", close: "17:00" }
  tuesday: { open: "08:00", close: "17:00" }
  wednesday: { open: "08:00", close: "17:00" }
  thursday: { open: "08:00", close: "17:00" }
  friday: { open: "08:00", close: "16:00" }
  saturday: null  # closed
  sunday: null

timezone: "America/Los_Angeles"
service_area_zips: ["89117", "89118", "89134", "89135"]
service_types: ["countertop", "flooring", "tile"]
average_ticket_range: "5k-15k"

brand:
  logo_url: "https://storage.traceflow.app/clients/example-surfaces/logo.png"
  primary_color: "#1a4d7a"
  tone_of_voice: "professional-friendly"
  category: "surface contractor"

vip_keywords: ["urgent", "today", "emergency", "asap", "commercial"]
vip_value_threshold: 15000

modules:
  llr: true
  sia_a: false
  sia_b: false
  sia_c: false  # knowledge base
  sia_d: false
  sia_e: false

crm:
  provider: "ghl"  # or 'hubspot', 'monday', 'generic'
  # credentials added separately via secure channel; not in YAML

ai_interaction_cap_monthly: 1000
```

Provisioning script steps:

```python
async def onboard_client(config_path: str):
    config = yaml.safe_load(open(config_path))
    
    # 1. Create client row
    client = await Client.create(
        slug=config["slug"],
        business_name=config["business_name"],
        tier=config["tier"],
    )
    
    # 2. Create client_config row
    await ClientConfig.create(
        client_id=client.id,
        business_hours=config["business_hours"],
        timezone=config["timezone"],
        service_area_zips=config["service_area_zips"],
        brand=config["brand"],
        vip_keywords=config["vip_keywords"],
        ai_interaction_cap_monthly=config["ai_interaction_cap_monthly"],
    )
    
    # 3. Allocate Twilio number
    twilio_number = await allocate_twilio_number(area_code=infer_area_code(config))
    await ClientConfig.update(client.id, twilio_number=twilio_number)
    
    # 4. Generate webhook signing secrets
    secrets = {
        provider: generate_secret() 
        for provider in ["twilio", "shopify", "ghl", "generic"]
    }
    await ClientConfig.update(client.id, webhook_signing_secrets=secrets)
    
    # 5. Initialize default prompt configurations
    await PromptConfig.create_defaults(client_id=client.id)
    
    # 6. Generate field mapping suggestions (if CRM credentials available)
    # ... requires separate step after credentials secured
    
    # 7. Output summary
    print(f"✓ Provisioned tenant: {client.slug}")
    print(f"  Client ID: {client.id}")
    print(f"  Twilio number: {twilio_number}")
    print(f"  Webhook URLs:")
    print(f"    Twilio: https://api.traceflow.app/webhooks/twilio/missed-call/{client.id}")
    print(f"    Shopify: https://api.traceflow.app/webhooks/shopify/{client.id}")
    print(f"  Next steps:")
    print(f"    1. Send signing secrets to client via 1Password share")
    print(f"    2. Configure CRM credentials via admin UI")
    print(f"    3. Run field mapping wizard")
    print(f"    4. Configure prompts")
    print(f"    5. Run smoke tests")
```

Target execution time: **under 10 minutes**.

## Field mapping setup

After provisioning, use the AI-assisted field mapping flow:

```bash
./scripts/suggest-field-mappings.py \
    --client-id <uuid> \
    --integration crm \
    --sample-record path/to/sample.json
```

Output: a YAML file the founder reviews, edits, and applies:

```yaml
# Suggested mappings (review before applying)
client_id: <uuid>
integration: crm

mappings:
  - canonical_field: sqft
    external_field: project_size_sqft
    external_field_type: custom_field
    transform: null
    confidence: high
  
  - canonical_field: service_type
    external_field: service_category
    external_field_type: custom_field
    transform:
      type: value_map
      mapping:
        countertop: "Kitchen Counter"
        flooring: "Floor Installation"
    confidence: medium  # review the value_map
  
  - canonical_field: budget_range
    external_field: estimated_budget
    external_field_type: standard
    transform: null
    confidence: low  # they use different bucket boundaries
```

## Integration configuration checklist

After provisioning + mappings, run through this checklist before soft launch:

- [ ] Twilio number purchased and assigned
- [ ] Client's main line configured to forward unanswered calls to Twilio number (4-6 ring forward)
- [ ] CRM API credentials added to client_configs.crm_credentials (encrypted)
- [ ] Test push: create a test lead in canonical schema → verify it lands in client's CRM correctly
- [ ] Field mappings reviewed and applied
- [ ] Chatbot script tag added to client's website (if applicable)
- [ ] Webhook signing secrets shared securely with client
- [ ] Resend/Postmark sender domain verified for client (or using TraceFlow's)
- [ ] First test SMS sent to founder's number from client tenant — receives correctly
- [ ] AI greeting test passes (5 sample missed-call simulations)
- [ ] AI qualifier test passes (3 golden conversation scenarios)
- [ ] Owner alert test passes (VIP keyword triggers correctly)
- [ ] Daily digest test passes (generates correctly for empty + populated day)

## Soft launch protocol

Days 1-3 of launch:

- Route only 10% of missed calls to TraceFlow (keep 90% on existing process)
- Monitor every conversation manually for the first 24 hours
- Have founder phone available for emergency cutover back
- Daily check-in call with client (Day 1, Day 3)

Days 4-7:

- Bump to 50% routing
- Spot-check conversations daily
- Capture any edge cases as future test scenarios

Day 8+:

- Cut over to 100%
- Switch to weekly monitoring cadence

## The handoff package

Deliver at full launch:

1. **Welcome email** with links to everything below
2. **5-min "What's running" Loom** — plain-English explainer
3. **2-min "What to do when X" Loom** — common scenarios
4. **Runbook PDF** (1-2 pages) — written version of the Looms
5. **Owner dashboard URL** with auth (Phase 3+; email digest in Phase 0-1)
6. **Direct support channel** — Slack Connect, dedicated email, or support form
7. **30-day check-in calendar invite**

## The 30-day check-in

Schedule at signing. Cover:

- Performance metrics: recovery rate, qualification rate, attributed revenue
- Client satisfaction: NPS-style question + open feedback
- Edge cases or behavior tweaks
- **Capture testimonial + logo permission for case study**
- Identify expansion opportunities (Phase 2 modules)

## Phase progression of onboarding automation

| Phase | Setup time | What's automated |
|---|---|---|
| Phase 0 (Clients 1-5) | 20-30 hrs | Nothing — everything manual via SQL + Google Doc form |
| Phase 1 (Clients 5-10) | 10-15 hrs | Provisioning script + Tally form + prompt templates |
| Phase 2 (Clients 5-10) | 10-15 hrs | Add: admin UI (Retool), AI field mapping, runbook generator |
| Phase 3 (Clients 10-15) | 4-6 hrs | Add: client portal walks through guided self-setup |
| Phase 4 (Clients 15+) | <1 hr | Self-serve tier with OAuth flows and in-product onboarding |

Each phase's automation investment must be justified by the next client signing. Don't build Phase 2 tooling until Client 3 is signed.
