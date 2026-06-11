# ADR-0003: Recovered-revenue attribution

**Date:** 2026-06-07
**Status:** accepted

## Context

The LLR promise — "recover 25%+ of your missed-call revenue" — and the ROI metric (`recovered revenue / monthly fee`, target ≥10x) are both stated in dollars. But the platform goes blind the moment a qualified lead is pushed to the CRM: the quote, the job, and the payment all happen offline in the client's world, days or weeks later. Until now the only dollar figure we produced was the daily digest's *estimated pipeline* — `budget_range` bucket midpoints — which is explicitly a directional proxy, not booked revenue. There was no `won`/`booked` outcome, no actual deal value, and no monthly report. The keystone Phase 0 artifact (the SEMCO case study) needs a defensible recovered-revenue number, and the value-prop guarantee ("first month free") makes that number's credibility load-bearing.

## Decision

Add a **booked-outcome axis** to the lead and capture actual recovered dollars through whichever source a client supports, with the source always recorded.

- **Canonical model.** `leads.outcome` (`open`/`won`/`lost`) + `recovered_value` (NUMERIC) + `outcome_source` (`crm`/`owner_report`/`estimated`) + `outcome_recorded_at` (migration 016). `outcome` is orthogonal to both `qualification_status` (how far the lead got) and `classification` (what the caller is) — a third independent axis. `outcome_source` is provenance: actuals are never silently blended with the estimate.
- **Three capture sources, tiered.** *Estimated* (budget-bucket proxy, the digest's existing number, the floor). *Owner-report* — the **universal baseline that works with or without a CRM**: the founder records the outcome via the admin endpoint `POST /api/admin/leads/{id}/outcome` (e.g. after the monthly review). *CRM readback* — automation on top, for clients whose CRM holds deal values. All three write the same `recovered_value` column; only the source tag and capture mechanism differ. This is how the no-CRM case is handled: it is not a special case, it is the baseline.
- **Attribution unit: contact "total spent," snapshot-bounded.** CRM readback reads the contact-level total (HubSpot `total_revenue`, a closed-won rollup) — chosen over per-deal amounts because recovered leads are *new by construction* (existing customers are filtered upstream by `classification`), so a recovered contact's total spend ≈ the job we recovered. To prevent lifetime drift (a contact who books a second job months later), the value is read back **only while the lead is within `attribution_window_days` of creation (default 90)**, refreshed each run to capture deal growth, then frozen.
- **CRM readback is HubSpot-first.** `CRMAdapter.fetch_recovered_value(external_id, config) -> Decimal | None` is implemented for HubSpot; GHL and Monday return `None` (their clients fall back to owner-report) until one needs auto-sync. A scheduled `jobs/revenue_sync.py` (daily Render cron) drives it for `revenue_config.mode='crm'` clients and finally gives `sync_log` (migration 007) a real user.

## The prime directive

**Provenance over precision, and never overclaim.** Every recovered number carries its `outcome_source`; "confirmed recovered" (`crm`/`owner_report`) is reported separately from "estimated pipeline." A CRM read that returns `None` (not booked yet, unsupported, error) never overwrites a stored value and never marks a lead `won` — like `lookup_by_phone`, `None` means "no confirmed value yet," never a hard failure. Only `classification='potential_lead'` leads are attributed.

## Alternatives considered

### Per-deal / won-opportunity amount as the attribution unit
More precise, but requires deal-level linkage between the recovered lead and a CRM deal that many small contractors won't maintain cleanly. Contact `total_revenue` is a strong proxy *because the recovered population is new*, and the snapshot window bounds the overcount. Revisit per-client (`revenue_config`) if a client's data supports deal linkage.

### Lifetime `total_revenue` with no window
Rejected: it drifts upward forever as a contact books future jobs, over-attributing revenue the missed-call recovery didn't source. The attribution window freezes the number.

### CRM webhook (push) instead of scheduled pull
The inbound CRM webhook (`webhooks/crm.py`) could carry "deal won" events, but each CRM fires a different shape and it needs per-client webhook wiring. A scheduled pull is simpler and reliable for Phase 0/1 — we already hold the `external_id`. Webhook ingestion stays a Phase 2 option.

### Estimate only (status quo)
Rejected: an estimate can't back the "25%+ of revenue" claim or the ROI guarantee, and can't anchor the case study.

## Consequences

### Positive
- A defensible recovered-revenue number exists immediately via owner-report (no CRM dependency), and automatically for HubSpot clients.
- Provenance separation keeps the headline honest — estimate vs confirmed never blur.
- Reuses existing seams: the adapter pattern, `sync_log`, the admin/Retool layer, the cron+tenant-enumeration pattern from `daily_digest`.

### Negative
- GHL/Monday auto-readback is deferred — those clients rely on manual entry until implemented (clearly TODO'd in the adapters).
- "Total spent" can over-attribute if a brand-new contact happens to have unrelated prior revenue; bounded, not eliminated, by the window.
- Manual owner-report depends on founder diligence at the monthly review.

### Reversibility
High. `revenue_config.mode` defaults to `estimated`, so a tenant with no config behaves exactly as before. Turning off `mode='crm'` stops readback with no code change; the columns are additive and backfill to `open`.

## Decision review trigger

Revisit if: clients keep clean deal linkage (switch the attribution unit to won-deal amount); the 90-day window proves too short/long against real close cycles; a GHL/Monday client needs auto-sync (implement their `fetch_recovered_value`); or we build the inbound CRM webhook and want push instead of pull.

## Implementation notes

- Code: migration 016; `models/lead.py` (`LeadOutcome`/`OutcomeSource`), `models/client_config.py` (`revenue_config`); `adapters/base.py` + `adapters/hubspot.py` (`fetch_recovered_value`); `routers/admin.py` (`/leads/{id}/outcome`); `jobs/revenue_sync.py`; `render.yaml` (`revenue-sync` cron).
- Tests: `tests/adapters/test_hubspot_adapter.py`, `tests/test_admin.py`, `tests/jobs/test_revenue_sync.py`.
- Not yet built: the `monthly_performance_report` job (`docs/workflow-schema.md` §4) that rolls `recovered_value` + ROI into the client email — the natural next increment now that the data exists.
