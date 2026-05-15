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

## 3. Technical Lead Lifecycle (event → CRM record)

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

  - id: lead_creation
    inputs: [validated_event]
    activities:
      - Create Lead record with raw_payload preserved
      - Initial qualification_status = 'unqualified'
      - Source system tagged
    outputs: [lead_record]
    exit_criteria: lead row inserted
    duration_target: <50ms

  - id: ai_outreach
    inputs: [lead_record, client_config]
    activities:
      - Generate personalized greeting via Anthropic API
      - Use client's greeting_template with variables filled
      - Send via Twilio SMS (or email/chat depending on channel)
    outputs: [outbound_message]
    exit_criteria: message delivered (Twilio webhook confirms)
    duration_target: <30 seconds from missed call

  - id: ai_qualification
    inputs: [inbound_reply]  # client responds via SMS
    activities:
      - Process reply through qualification prompt
      - Extract canonical fields: service_type, sqft, budget, timeframe, etc.
      - Multi-turn conversation until enough data captured OR lead disqualifies
      - Update qualification_score 0-100
    outputs: [qualified_lead | disqualified_lead]
    exit_criteria: qualification_status updated, score assigned
    duration_target: 2-10 minutes of conversation

  - id: crm_push
    inputs: [qualified_lead]
    activities:
      - Look up client's CRM adapter from registry
      - Look up field mappings
      - Translate canonical Lead → client CRM payload
      - Push via adapter.push_lead()
      - Update lead.external_id and pushed_to_crm_at
    outputs: [synced_lead]
    exit_criteria: external_id populated
    duration_target: <5 seconds

  - id: owner_alert  # conditional
    inputs: [synced_lead, client_config.vip_keywords]
    activities:
      - Evaluate if lead matches VIP signals (keywords, value threshold, urgency)
      - If yes: send immediate SMS or email to designated owner contact
    outputs: [alert_sent | no_alert]
    exit_criteria: decision made, alert delivered if triggered

  - id: digest_inclusion  # scheduled
    inputs: [all_leads_in_24h_window]
    activities:
      - Nightly cron iterates over all active clients
      - Generate per-client digest with day's leads + metrics
      - Send via Resend/Postmark
    outputs: [daily_digest_email]
    runs_at: 06:00 client_timezone
```

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
    
  - id: monthly_performance_report
    frequency: monthly
    delivery_by: 5th of month
    activities:
      - Auto-generate per-client report from leads/events tables
      - Metrics: leads captured, conversion %, attributed revenue, hours saved
      - Send via email with branded template
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
