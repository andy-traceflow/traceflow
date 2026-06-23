/**
 * labels.ts — single source of truth for turning backend enum values into
 * human-facing labels.
 *
 * Why this exists: the API speaks in snake_case enums (`potential_lead`,
 * `high_value`, `founding_partner`…). Those are correct on the wire but read as
 * "unfinished" when screen-shared in a demo. Every panel routes its enum values
 * through the maps below, so the UI never renders a raw snake_case token.
 *
 * Operator note: to relabel anything, edit the map here and every panel updates.
 * Keys mirror the Python `StrEnum`s in `src/app/models` + `src/app/services`;
 * any value missing from a map falls back to `humanize()` (sentence case), so an
 * unmapped enum still renders cleanly instead of as raw snake_case.
 */

export type LabelMap = Record<string, string>;

/** Sentence-case an unmapped enum value: "needs_review" -> "Needs review". */
export function humanize(value: string): string {
  const spaced = value.replace(/[_-]+/g, " ").trim();
  if (!spaced) return "—";
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** Look up a label, falling back to humanize(); null/empty -> em dash. */
export function labelFor(map: LabelMap, value: string | null | undefined): string {
  if (value == null || value === "") return "—";
  return map[value] ?? humanize(value);
}

/**
 * Caller classification — plural, for filter options and count buckets.
 * Mirrors `Classification` (models/lead.py) plus the `active_conversation`
 * routing bucket surfaced in routing activity.
 */
export const CLASSIFICATION_LABELS: LabelMap = {
  potential_lead: "Genuine leads",
  existing_customer: "Existing customers",
  known_non_lead: "Vendors / non-leads",
  spam: "Spam",
  active_conversation: "Mid-conversation",
  all: "All leads",
};

/** The same buckets, singular — for a single lead (drawer, badges). */
export const CLASSIFICATION_SINGULAR_LABELS: LabelMap = {
  potential_lead: "Genuine lead",
  existing_customer: "Existing customer",
  known_non_lead: "Vendor / non-lead",
  spam: "Spam",
  active_conversation: "Mid-conversation",
};

/** Qualification status — how far a lead got (models/lead.py QualificationStatus). */
export const QUALIFICATION_STATUS_LABELS: LabelMap = {
  unqualified: "Unqualified",
  qualifying: "Qualifying",
  qualified: "Qualified",
  high_value: "High value",
  needs_review: "Needs review",
  spam: "Spam",
  duplicate: "Duplicate",
  support_touch: "Support touch",
  non_lead_contact: "Non-lead contact",
};

/** Booking outcome (models/lead.py LeadOutcome). */
export const OUTCOME_LABELS: LabelMap = {
  open: "Open",
  won: "Won",
  lost: "Lost",
};

/** Provenance of a recovered value (models/lead.py OutcomeSource). */
export const OUTCOME_SOURCE_LABELS: LabelMap = {
  crm: "CRM",
  owner_report: "Owner report",
  estimated: "Estimated",
};

/** First-reply intent (prompts/intent.py Intent). */
export const INTENT_LABELS: LabelMap = {
  sales: "Sales",
  existing_customer: "Existing customer",
  non_lead: "Non-lead",
  spam: "Spam",
  ambiguous: "Ambiguous",
};

/** Routing decision chosen per missed call (services/classification.py Route). */
export const ROUTING_DECISION_LABELS: LabelMap = {
  potential_lead: "Genuine lead",
  existing_customer: "Existing customer",
  known_non_lead: "Vendor / non-lead",
  spam: "Spam",
  active_conversation: "Mid-conversation",
};

/** Client tier (models/client.py ClientTier). */
export const CLIENT_TIER_LABELS: LabelMap = {
  founding_partner: "Founding partner",
  standard: "Standard",
  pro: "Pro",
  full_stack: "Full stack",
};

/** Client status (models/client.py ClientStatus). */
export const CLIENT_STATUS_LABELS: LabelMap = {
  active: "Active",
  paused: "Paused",
  churned: "Churned",
  trial: "Trial",
};

/** Field-mapping external field type (admin field mappings). */
export const FIELD_TYPE_LABELS: LabelMap = {
  standard: "Standard",
  custom_field: "Custom field",
  custom_property: "Custom property",
  column: "Column",
};

/** Spam-risk threshold (services/spam.py SpamRisk). */
export const SPAM_RISK_LABELS: LabelMap = {
  low: "Low",
  moderate: "Moderate",
  high: "High",
};
