# TraceFlow Workflow Schema

> Structured representation of every workflow in TraceFlow: sales lifecycle, client onboarding, technical delivery, ongoing retention, and the development process itself. Reference this when adding/modifying steps in any flow.

**Format:** YAML-style schema for each workflow. Each stage lists inputs, outputs, owner, duration, and exit criteria.

---

## 1. Sales Lifecycle (prospect → signed client)

```yaml
workflow: sales_lifecycle
total_duration_days: 7-21
owner: founder

stages:
  - id: prospect_identification
    inputs: [linkedin_search_filters, referral, content_inbound]
    activities:
      - LinkedIn Sales Navigator search (industry + revenue + geography)
      - Add to prospect tracker (CRM or Notion)
    outputs: [prospect_record]
    exit_criteria: prospect added to outbound queue
    duration: ongoing

  - id: outreach
    inputs: [prospect_record]
    activities:
      - Personalized LinkedIn DM with 5-min Loom audit
      - Loom: analyze website + Google Business profile + missed-call patterns
    outputs: [reply | no_response_after_3_followups]
    exit_criteria: reply received OR sequence exhausted
    duration: 0-14 days
    timing: founder evening hours (async)

  - id: discovery_call
    inputs: [reply]
    activities:
      - 25-minute call following discovery_script
      - Determine LLR vs SIA fit per PRD §4 decision matrix
      - Capture pain points, current stack, decision timeline
    outputs: [llr_fit | sia_fit | disqualified | needs_followup]
    exit_criteria: prospect categorized
    duration: 1 day
    timing: Saturday morning OR weekday evening

  - id: proposal
    inputs: [llr_fit | sia_fit]
    branches:
      llr_fit:
        - Send Notion-hosted proposal within 24h
        - Pricing: $1,500-$2,500 setup + $397-$597/mo
        - Include 1-2 case study links + Calendly
      sia_fit:
        - Sell paid audit first ($497-$997)
        - Audit produces written roadmap with module recommendations
    outputs: [proposal_sent | audit_sold]
    exit_criteria: proposal or audit invoice delivered
    duration: 1 day

  - id: close
    inputs: [proposal_sent]
    activities:
      - Follow up at days 2, 5, 10 if no response
      - Contract via Stripe + e-sign
      - 50% setup fee invoiced before work begins
    outputs: [signed_client | lost | stalled]
    exit_criteria: contract signed + first payment received
    duration: 7-14 days

  - id: kickoff
    inputs: [signed_client]
    activities:
      - Send onboarding form (Tally)
      - Schedule 15-min kickoff call OR async Loom
      - Add to client tracker
    outputs: [active_engagement]
    exit_criteria: onboarding form submitted
    duration: 1-3 days
    handoff_to: client_onboarding_workflow
```

---

## 2. Client Onboarding (signed → launched)

```yaml
workflow: client_onboarding
total_duration_days: 14-28
owner: founder

stages:
  - id: information_collection
    inputs: [active_engagement]
    activities:
      - Client completes Tally onboarding form
      - Collects: business info, lead/sales context, tech access, brand, module-specific data
      - See: docs/playbooks/new-client-onboarding.md for full field list
    outputs: [client_config_yaml]
    exit_criteria: all required fields submitted
    duration: 1-3 days
    owner: client (founder follows up)

  - id: tenant_provisioning
    inputs: [client_config_yaml]
    activities:
      - Run provisioning script (`./scripts/onboard-client.py --config <yaml>`)
      - Creates: clients row, client_configs row, allocates Twilio number, sets webhook secrets
      - Manual in Phase 0; scripted in Phase 1; UI-driven in Phase 3
    outputs: [provisioned_tenant]
    exit_criteria: client_id exists, Twilio number active, config loaded
    duration: 1 hour (target)
    automation_phase: 1+

  - id: field_mapping_setup
    inputs: [provisioned_tenant, client_crm_sample_record]
    activities:
      - Feed sample record to Claude with field-mapping prompt
      - Review/correct AI-proposed mappings
      - Insert into client_field_mappings table
    outputs: [active_field_mappings]
    exit_criteria: all canonical fields mapped to client's external fields
    duration: 20-30 minutes
    automation_phase: 2+

  - id: integration_configuration
    inputs: [provisioned_tenant, active_field_mappings]
    activities:
      - Configure CRM API connection (via OAuth where possible)
      - Set up Twilio number forwarding from client's main line
      - Embed chatbot script tag on website
      - Configure webhook destinations
    outputs: [connected_integrations]
    exit_criteria: end-to-end test passes
    duration: 2-4 hours

  - id: knowledge_base_ingestion
    inputs: [provisioned_tenant, client_docs]  # only for SIA Module C
    activities:
      - Upload TDS sheets, install guides, FAQ docs
      - Run extraction pipeline (PDF → Q&A pairs → embeddings)
      - Reuse SEMCO methodology
    outputs: [populated_kb]
    exit_criteria: chatbot answers test queries with >70% accuracy
    duration: 4-8 hours
    optional: true

  - id: prompt_tuning
    inputs: [client_config, sample_qualification_scenarios]
    activities:
      - Customize qualification prompts using template + client variables
      - Run 5-10 test conversations
      - Adjust tone, branching, edge cases
    outputs: [tuned_prompts]
    exit_criteria: prompts pass test scenarios
    duration: 2-3 hours

  - id: soft_launch
    inputs: [tuned_prompts, connected_integrations]
    activities:
      - Redirect 10% of missed calls to TraceFlow for 3 days
      - Run parallel with existing process
      - Monitor closely, fix edge cases
    outputs: [validated_system]
    exit_criteria: no critical errors, recovery rate >30%
    duration: 3-5 days

  - id: full_launch
    inputs: [validated_system]
    activities:
      - Cut over 100% of missed calls
      - Deliver handoff package: welcome email, 5-min Loom, 2-min runbook Loom, written runbook PDF, dashboard URL, support channel info
      - Invoice final 50% setup fee + first month retainer
    outputs: [launched_client]
    exit_criteria: client signs off on launch
    duration: 1 day
    handoff_to: ongoing_retainer_workflow

  - id: thirty_day_checkin
    inputs: [launched_client]
    activities:
      - Capture metrics: recovery rate, conversion, attributed revenue
      - Get written testimonial + logo usage permission
      - Identify expansion opportunities (Phase 2 modules)
    outputs: [case_study_artifact, satisfied_client]
    exit_criteria: testimonial + metrics captured
    duration: 1 day at Day 30 post-launch
```

---

## 3. Technical Lead Lifecycle (event → routed outcome)

```yaml
workflow: lead_lifecycle
total_duration: real-time (seconds to minutes)
owner: platform (automated)

stages:
  - id: ingestion
    inputs: [external_event]  # missed call, form submit, chat, etc.
    activities:
      - Webhook hits FastAPI endpoint
      - Tenant resolver extracts client_id, sets RLS context
      - Signature verification
      - Raw payload logged to events table
    outputs: [validated_event]
    exit_criteria: event recorded with client_id
    duration_target: <100ms

  - id: caller_classification          # NEW — runs BEFORE any message is sent
    inputs: [validated_event]          # caller phone, called number, client_id
    activities:
      - Active-conversation check: is this caller already mid-conversation
        with TraceFlow for this client? If yes → route=active_conversation.
      - CRM lookup (best-effort, via adapter.lookup_by_phone): does this number
        already exist in the client's CRM?
          - Match tagged as customer  → route=existing_customer
          - Match tagged as vendor/partner → route=known_non_lead
          - No match → continue
      - Spam scoring (if spam_filtering_enabled): Twilio Lookup risk signal vs
        the client's configured threshold.
          - Above threshold → route=spam
          - Below → continue
      - Default → route=potential_lead
    outputs: [routing_decision]
    routing_decision_values:
      - active_conversation
      - existing_customer
      - known_non_lead
      - spam
      - potential_lead
    degradation:
      - If CRM lookup is unsupported by the client's adapter, fails, or no CRM
        is configured → skip the CRM step, fall through toward potential_lead.
        A lookup failure must NEVER drop a real lead. Post-reply intent
        classification is the safety net.
    exit_criteria: routing_decision assigned
    duration_target: <2s (CRM lookup + spam lookup are the cost)

  - id: route_dispatch                 # NEW — acts on the routing decision
    inputs: [routing_decision, validated_event]
    branches:
      active_conversation:
        - Append the inbound event to the existing conversation thread
        - Resume that conversation's existing state (do not create a new lead)
        - Exit lifecycle (handled by the live conversation)
      existing_customer:
        - Do NOT run sales qualification
        - If text_existing_customers=true: send a customer-appropriate
          acknowledgment ("Hi [name], sorry we missed you — letting the team
          know you called, someone will reach out shortly")
        - Alert existing_customer_alert_contact immediately (an existing
          customer hitting voicemail is a priority service event, higher than
          a cold lead)
        - Log as support_touch, not a lead
        - Exit lifecycle
      known_non_lead:
        - Do NOT run sales qualification
        - If text_vendors=true: send a minimal acknowledgment; else send nothing
        - Alert owner contact
        - Log as non_lead_contact
        - Exit lifecycle
      spam:
        - Send nothing
        - Mark event as spam (for metrics + future suppression)
        - Exit lifecycle (zero SMS spend, zero AI spend)
      potential_lead:
        - Proceed to lead_creation
    exit_criteria: event routed; only potential_lead continues downstream

  - id: lead_creation
    inputs: [routing_decision=potential_lead]
    activities:
      - Create Lead record with raw_payload preserved
      - qualification_status = 'unqualified'
      - classification = 'potential_lead' (carried for metrics)
      - Source system tagged
    outputs: [lead_record]
    exit_criteria: lead row inserted
    duration_target: <50ms

  - id: ai_outreach
    inputs: [lead_record, client_config]
    activities:
      - Generate the NEUTRAL opening greeting via the AI (works for any caller
        type; never pre-judges as a sales prospect)
      - Use client's greeting_template with variables filled
      - Send via Twilio SMS
    outputs: [outbound_message]
    exit_criteria: message delivered (Twilio webhook confirms)
    duration_target: <30 seconds from missed call

  - id: intent_classification          # NEW — runs on the FIRST inbound reply
    inputs: [first_inbound_reply, lead_record, client_config]
    activities:
      - AI classifies intent from the reply content (this is the safety net for
        existing customers / vendors who called from an unrecognized number, and
        the first real signal of genuine sales intent)
      - Intent values: sales | existing_customer | non_lead | spam | ambiguous
    outputs: [intent]
    branches:
      sales:
        - Proceed to ai_qualification
      existing_customer:
        - Stop qualifying; capture message; alert human; reclassify lead as
          support_touch; exit
      non_lead:
        - Capture; alert owner; mark non_lead_contact; exit
      spam:
        - Stop responding; mark spam; exit
      ambiguous:
        - Ask ONE clarifying question; re-run intent_classification on next reply
    exit_criteria: intent assigned and acted on
    duration_target: <10s per turn

  - id: ai_qualification
    inputs: [intent=sales, ongoing_conversation]
    activities:
      - Process replies through qualification prompt
      - Extract canonical fields: service_type, sqft, budget, timeframe, etc.
      - Multi-turn until enough data captured OR lead disqualifies
      - Update qualification_score 0-100
    outputs: [qualified_lead | disqualified_lead]
    exit_criteria: qualification_status updated, score assigned
    duration_target: 2-10 minutes of conversation

  - id: crm_push                       # IMPLEMENTED in webhooks/twilio.py:_maybe_push_to_crm
    inputs: [qualified_lead]
    activities:
      - Runs automatically when a qualifier turn sets qualification_status to
        'qualified'/'high_value' (the inbound-SMS path; needs_review/spam are not pushed)
      - Look up client's CRM adapter from registry (no crm_provider / unknown provider → no-op)
      - Look up field mappings
      - Translate canonical Lead → client CRM payload
      - Push via adapter.push_lead()
      - Update lead.external_id and pushed_to_crm_at; record a crm_pushed event
    degradation:
      - Idempotent: a lead that already has an external_id is never re-pushed (no
        duplicate CRM record). A push failure records a crm_push_failed event and
        leaves the lead untouched for manual re-push (POST /api/admin/leads/{id}/repush);
        it never raises into the leads pipeline.
    outputs: [synced_lead]
    exit_criteria: external_id populated
    duration_target: <5 seconds

  - id: owner_alert                    # conditional
    inputs: [synced_lead, client_config.vip_keywords]
    activities:
      - Evaluate VIP signals (keywords, value threshold, urgency)
      - If yes: immediate SMS or email to designated owner contact
    outputs: [alert_sent | no_alert]
    exit_criteria: decision made, alert delivered if triggered

  - id: digest_inclusion              # scheduled — IMPLEMENTED in jobs/daily_digest.py
    inputs: [all_genuine_leads_in_24h_window]
    activities:
      - Hourly cron; each client gated to its own 06:00 local (zoneinfo), idempotent
        via a daily_digest_sent event; enumerated through get_service_connection()
      - Per-client digest with the day's GENUINE leads + metrics
      - recovery_rate = replied / captured over classification='potential_lead' ONLY
        (excludes spam, existing customers, vendors); "replied" = lead left 'unqualified'
      - Also surfaces the silently-handled dispositions (spam / non_lead_contact /
        support_touch) so the owner sees the noise the system absorbed
      - Skipped on zero-activity days; degrades to a no-op if RESEND_API_KEY is unset
    outputs: [daily_digest_email]
    runs_at: 06:00 client_timezone
```

> **Schema dependencies applied with this revision:**
> - `client_configs.classification_config`, `existing_customer_alert_contact`, `vendor_allowlist` — migration `013_add_classification_config_to_client_configs.sql`, mirrored on `app.models.client_config.ClientConfig`.
> - `CRMAdapter.lookup_by_phone(phone, config) -> CRMContact | None` — `src/app/adapters/base.py`; canonical `CRMContact` in `src/app/models/crm_contact.py`. Must degrade to `None` (never raise), so a lookup failure can't drop a lead.
>
> **Runtime tier — BUILT** (2026-06-01, branch `feat/caller-classification-runtime`; see ADR-0002). Shipped in four slices: `leads.classification` (migration 014) + `support_touch`/`non_lead_contact` statuses (migration 015); `services/classification.py` (caller_classification + route_dispatch); `prompts/intent.py` (post-reply intent_classification); `services/spam.py` (Twilio Lookup spam scoring); per-adapter `lookup_by_phone` (GHL, Monday); and the digest recovery-rate denominator (`jobs/daily_digest.py`, over `classification='potential_lead'` only). Every failing or ambiguous path degrades to `potential_lead` — a lookup failure never drops a lead, and the post-reply intent classifier is the safety net. The neutral opener remains the connective tissue that keeps a misclassified contact recoverable.

---

## 4. Ongoing Retainer (post-launch)

```yaml
workflow: ongoing_retainer
duration: monthly recurring
owner: founder + platform

cadences:
  - id: real_time_monitoring
    frequency: continuous
    activities:
      - UptimeRobot pings critical endpoints every 5 min
      - Sentry captures errors
      - Per-client AI usage tracked vs cap
    alerts:
      - System down → immediate page to founder
      - Client over 80% of monthly AI cap → notify founder + client
      - 5+ failed CRM pushes in 1 hour → page founder
    
  - id: weekly_health_check
    frequency: weekly
    duration_minutes: 30
    activities:
      - Review per-client dashboard
      - Check recovery rate, conversion rate, error logs
      - Note any clients trending down
    output: weekly_health_log
    
  - id: revenue_readback                # IMPLEMENTED in jobs/revenue_sync.py (ADR-0003)
    frequency: daily
    activities:
      - For clients with revenue_config.mode='crm', read each pushed lead's booked
        value back from the CRM (adapter.fetch_recovered_value — HubSpot total_revenue;
        GHL sum of won opportunities) within attribution_window_days and freeze it
        onto leads.recovered_value / outcome='won' (outcome_source='crm').
        Snapshot-bounded so a later second job never inflates the figure attributed
        to the original missed call. Monday still returns None (owner-report fallback).
      - Clients with no CRM (or mode != 'crm') capture recovered_value via the admin
        outcome endpoint (POST /api/admin/leads/{id}/outcome, outcome_source='owner_report').
    output: confirmed_recovered_revenue

  - id: monthly_performance_report      # IMPLEMENTED in jobs/monthly_report.py
    frequency: monthly
    delivery_by: 5th of month           # hourly cron days 1-5, gated to each client's local 09:00; failed day retries next
    activities:
      - Auto-generate per-client report from leads/events tables for the previous
        local calendar month; idempotent per period via monthly_report_sent event
        (payload.period = 'YYYY-MM')
      - Metrics: leads captured, recovery %, qualified + conversion %, CONFIRMED
        recovered revenue (actuals from leads.recovered_value, labeled by
        outcome_source — never blended with the budget-bucket estimate), estimated
        pipeline (explicitly labeled), program-to-date confirmed total, ROI multiple
        vs revenue_config.monthly_fee (omitted when fee unset), hours-saved estimate
      - Send via email (Resend); skip dead months; no event recorded on send
        failure so the next cron hour retries
    output: client_performance_report
    
  - id: quarterly_review_call
    frequency: quarterly
    duration_minutes: 30
    activities:
      - Live call with client owner
      - Review the quarter's metrics
      - Identify expansion opportunities (SIA modules, additional services)
      - Renew/adjust scope as needed
    output: expansion_opportunities | renewal_confirmation

  - id: expansion_motion
    triggers:
      - 30-day post-launch milestone
      - Quarterly review
      - Client-initiated request
    activities:
      - Pitch next-tier offering (LLR → Knowledge Engine → Ops Stack)
      - Update contract with new scope
      - Treat as compressed onboarding for new modules
    output: expanded_client (higher ARPU)
```

---

## 5. Development Workflow (founder-side)

```yaml
workflow: dev_session
typical_duration_hours: 1-3
owner: founder + Claude Code

stages:
  - id: session_start
    activities:
      - Open repo in CachyOS terminal
      - Claude Code reads CLAUDE.md automatically
      - Founder states the session goal
    outputs: [scoped_task]
    
  - id: context_load
    activities:
      - Claude Code loads relevant skill files
      - Checks recent CHANGELOG entries
      - Confirms current code state before suggesting changes
    outputs: [working_context]

  - id: build_iterate
    activities:
      - Implement against the task
      - Run tests (including tenant_isolation suite)
      - Verify locally
    outputs: [code_change | doc_change | decision]

  - id: review_commit
    activities:
      - Founder reviews diffs
      - Commit with descriptive message
      - Push to GitHub
    outputs: [committed_change]

  - id: context_sync
    activities:
      - Update CHANGELOG.md (new entry at top)
      - Update PRD or architecture.md if scope shifted
      - Create ADR if a significant decision was made
      - See: .claude/skills/context-sync/SKILL.md
    outputs: [updated_context]
    exit_criteria: docs reflect current reality
```

---

## 6. Content & Marketing Workflow

```yaml
workflow: content_marketing
cadence: 3-5 posts per week
owner: founder + Claude

types:
  linkedin_build_in_public:
    frequency: 3x/week
    duration_per_post: 15-20 minutes
    process:
      - Source: real client work or platform builds this week
      - Draft with Claude assistance
      - Edit for voice
      - Post
    output: linkedin_post

  case_study:
    frequency: 1 per launched client
    duration: 2-3 hours
    process:
      - Wait until 30-day metrics captured
      - Interview client (or summarize from your data)
      - Draft 2-page PDF + 1-page web version
      - Get client signoff before publishing
    output: case_study_asset

  loom_audit:
    frequency: per outreach prospect
    duration_per_loom: 5-10 minutes
    process:
      - Open prospect's website + Google profile
      - Record screen narration: 3 specific issues + ROI estimate
      - Send via LinkedIn DM with personalized message
    output: outreach_loom

  product_release_note:
    frequency: per major platform improvement
    duration: 30 minutes
    process:
      - Document what changed (CHANGELOG entry first)
      - Email all clients with the improvement
      - LinkedIn post if broadly applicable
    output: client_email + linkedin_post
```

---

## How to use this schema

- **Adding a new workflow step?** Insert into the appropriate stage list. Update CHANGELOG.
- **Building automation?** Find the workflow stage and its current `automation_phase` to know what's currently manual vs scripted vs UI-driven.
- **Onboarding a teammate (Phase 2+)?** Walk them through these workflows in order.
- **Designing a new feature?** Find which workflow(s) it touches. Confirm it fits without breaking the existing flow.
- **Debugging a process problem?** Trace through the schema to find which stage is failing.

This schema is **authoritative**. If reality diverges, update the schema first, then act.
