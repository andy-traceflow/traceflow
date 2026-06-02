# ADR-0002: Caller classification runtime tier

**Date:** 2026-06-01
**Status:** accepted

## Context

The core LLR promise is "recover 25%+ of missed-call revenue." The original pipeline treated every missed call as a sales lead: greet the caller, run an AI qualifier, push to the CRM. In practice a contractor's missed calls are a mix of real prospects, existing customers calling about a job, vendors/suppliers, recruiters, and outright spam. Texting all of them as "leads" wastes SMS + AI spend, pollutes the CRM, inflates the recovery metric with non-leads, and erodes owner trust.

We need to classify *what a caller is* and route accordingly — without ever dropping a real lead, and without per-client code branches (the platform thesis: configuration over customization).

A hard constraint shaped the design: AI is deferred in Phase 0 (`ANTHROPIC_API_KEY` unset until the framework is verified), Twilio creds are `PENDING` until Client 1, and most clients have no CRM. The feature therefore has to be *safe by default* — inert and lead-preserving when every external signal is absent.

## Decision

Build a four-stage runtime tier, every stage degrading toward `potential_lead`:

1. **Pre-send classification (`services/classification.py`).** Before greeting, `classify_caller` routes on: active conversation → vendor allowlist → CRM `lookup_by_phone` → unknown. Existing customers and vendors are tagged and (by config) not texted; unknown callers continue.
2. **Spam scoring (`services/spam.py`).** Unknown callers *only* are scored via Twilio Lookup v2 line-type intelligence against a per-client threshold. CRM-known callers are never scored.
3. **Post-reply intent (`prompts/intent.py`).** The first inbound reply is triaged (sales / existing_customer / non_lead / spam / ambiguous) before the qualifier runs. This is the **safety net**: anything misrouted earlier is caught here, biased toward `sales`.
4. **Reporting (`jobs/daily_digest.py`).** A nightly per-tenant digest reports recovery-rate metrics over genuine leads only.

Two model decisions underpin it:

- **`classification` is orthogonal to `qualification_status`.** A new `leads.classification` column captures what the caller IS; `qualification_status` captures how far a genuine lead got. They never collapse into one field.
- **Recovery rate is computed over `classification='potential_lead'` only.** Spam, existing customers, and vendors are excluded from the denominator, so the headline metric reflects real recoverable revenue, not raw call volume. "Recovered" = a genuine lead that left `unqualified` (the caller replied); `qualified`/`high_value` is tracked separately as conversion.

## The prime directive

**A lookup failure must NEVER drop a real lead.** Every failing, ambiguous, or unknown path — no Anthropic key, no Twilio creds, no CRM, a timeout, an HTTP error, an unparseable body, no signal — degrades to `potential_lead`. The post-reply intent classifier is the explicit second line of defense for anything the pre-send stage gets wrong. This is enforced by tests at every stage.

## Alternatives considered

### A single status field (fold classification into `qualification_status`)
Rejected: it conflates two independent axes and makes the recovery-rate denominator impossible to define cleanly. A caller can be a `potential_lead` (classification) that is still `unqualified` (status); the two must vary independently.

### Per-client feature flags / code branches
Rejected per the platform thesis — variability lives in `classification_config` rows, not code. A client with no CRM + empty allowlist + no spam signal behaves identically to the pre-classification system, so the tier is safe to ship enabled-by-default with no feature flag.

### Block spam hard by default
Rejected: a false-positive spam tag would drop a real lead. The default `spam_risk_threshold` is conservative (drops only the highest-risk line type); `drop_spam_silently=False` even keeps texting a flagged caller while tagging it for metrics. Aggressiveness is opt-in.

## Consequences

### Positive
- The recovery metric finally means something — genuine leads only.
- Zero added spend on non-leads; the digest's "handled automatically" section makes the filtered noise visible to the owner.
- Safe-by-default: inert in Phase 0 with all keys off; no feature flag needed.
- Post-reply intent makes the whole tier fault-tolerant — misclassification is recoverable, not terminal.

### Negative
- More moving parts (two lookups + one AI triage) on the missed-call path. Mitigated by hard ~2s timeouts on every external lookup so the <30s missed-call SMS target is never at risk.
- An extra AI interaction (intent) per first reply once the Anthropic key is live — billed +1, and skipped entirely when the key is off.
- Spam/intent accuracy depends on third-party signal quality (Twilio line type, the LLM). Conservative thresholds + a sales-biased intent prompt keep the failure mode "treat as a lead," never "drop a lead."

### Reversibility
High. The tier is config-gated end to end: set `crm_lookup_enabled=false`, `spam_filtering_enabled=false`, leave `vendor_allowlist` empty, and keep the Anthropic key off → behavior collapses to the original always-`potential_lead` pipeline with no code change.

## Decision review trigger

Revisit if: real traffic shows the conservative spam default letting through obvious robocalls (tighten to `strict`); the post-reply intent classifier misroutes real leads (re-tune the prompt or raise the sales bias); or recovery-rate gaming pressure makes "replied" too loose a definition of "recovered."

## Implementation notes

- Slices + decisions: `docs/CHANGELOG.md` (2026-06-01); `docs/workflow-schema.md` § caller_classification / route_dispatch / digest_inclusion.
- Code: `services/classification.py`, `services/spam.py`, `prompts/intent.py`, `jobs/daily_digest.py`; migrations 013–015.
- Tests: `tests/services/test_classification.py`, `test_spam.py`, `tests/prompts/test_intent.py`, `tests/jobs/test_daily_digest.py` — every degrade-to-`potential_lead` path is covered.
