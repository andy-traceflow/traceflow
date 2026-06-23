/** Fetch wrapper + token store for the admin API (same-origin /api/admin). */

/** True when the SPA is served at the public demo path (/demo). Demo sessions
 *  are read-only and auto-authenticated (see App.tsx + /api/demo-login). */
export const isDemo =
  typeof window !== "undefined" && window.location.pathname.startsWith("/demo");

// Distinct storage keys so an /admin session and a /demo session on the same
// origin never share a token — a real owner token must never end up driving the
// demo shell, nor a demo token the real admin.
const TOKEN_KEY = isDemo ? "tf_demo_token" : "tf_admin_token";

export function getToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  sessionStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  sessionStorage.removeItem(TOKEN_KEY);
}

/** Thrown on any non-2xx; status 401 triggers the logout bounce in App. */
export class ApiError extends Error {
  status: number;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
  }
}

let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: () => void): void {
  onUnauthorized = fn;
}

export async function api<T>(
  path: string,
  options: { method?: string; body?: unknown } = {},
): Promise<T> {
  const method = options.method ?? "GET";

  // The deployed demo is read-only. Short-circuit any mutation client-side with
  // a friendly message — the server also blocks it (403), but this avoids the
  // wasted round-trip and a confusing error toast.
  if (isDemo && method !== "GET") {
    throw new ApiError(403, "This is a read-only demo — changes are disabled here.");
  }

  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const resp = await fetch(`/api/admin${path}`, {
    method,
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  });

  // A 401 on an authenticated call means the session died — bounce to login.
  // /login's own 401 (bad credentials) falls through to the generic handler
  // so the server's message reaches the form.
  if (resp.status === 401 && path !== "/login") {
    clearToken();
    onUnauthorized?.();
    throw new ApiError(401, "Session expired — log in again");
  }
  if (!resp.ok) {
    let detail = `Request failed (${resp.status})`;
    try {
      const data = await resp.json();
      if (typeof data.detail === "string") detail = data.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(resp.status, detail);
  }
  return (await resp.json()) as T;
}

/** Bootstrap a read-only demo session — no credentials. Hits /api/demo-login
 *  directly (it's mounted outside the /api/admin base path). */
export async function demoLogin(): Promise<LoginResponse> {
  const resp = await fetch("/api/demo-login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  if (!resp.ok) throw new ApiError(resp.status, "The demo is unavailable right now.");
  return (await resp.json()) as LoginResponse;
}

// ---- shapes (mirror routers/admin/schemas.py — only the fields the UI reads)

export interface AdminMe {
  id: string;
  email: string;
  name: string;
  role: string;
}

export interface LoginResponse {
  access_token: string;
  expires_at: string;
  admin: AdminMe;
}

export interface ClientItem {
  id: string;
  slug: string;
  business_name: string;
  status: string;
  tier: string;
  timezone: string;
  crm_provider: string | null;
  twilio_number: string | null;
  leads_30d: number;
}

export interface ClassificationConfig {
  crm_lookup_enabled: boolean;
  spam_filtering_enabled: boolean;
  spam_risk_threshold: "low" | "moderate" | "high";
  text_existing_customers: boolean;
  text_vendors: boolean;
  drop_spam_silently: boolean;
}

export interface ClientConfig {
  client_id: string;
  slug: string;
  business_name: string;
  status: string;
  tier: string;
  timezone: string;
  business_hours: Record<string, { open: string; close: string }>;
  service_area_zips: string[];
  twilio_number: string | null;
  vip_keywords: string[];
  vip_value_threshold: number | null;
  crm_provider: string | null;
  qualification_prompt: string | null;
  greeting_template: string | null;
  ai_interaction_cap_monthly: number;
  ai_interactions_used: number;
  brand: Record<string, unknown>;
  notification_emails: string[];
  owner_alert_emails: string[];
  owner_alert_phones: string[];
  classification_config: ClassificationConfig;
  existing_customer_alert_contact: string | null;
  vendor_allowlist: string[];
  revenue_config: Record<string, unknown>;
  has_crm_credentials: boolean;
  webhook_integrations: string[];
  updated_at: string;
}

export interface LeadItem {
  id: string;
  created_at: string;
  contact_name: string | null;
  phone: string | null;
  email: string | null;
  classification: string;
  qualification_status: string;
  qualification_score: number | null;
  service_type: string | null;
  budget_range: string | null;
  timeframe: string | null;
  outcome: string;
  recovered_value: number | null;
  external_id: string | null;
  pushed_to_crm_at: string | null;
  is_test: boolean;
  message_count: number;
  last_message_at: string | null;
}

export interface LeadList {
  data: LeadItem[];
  count: number;
}

export interface LeadDetail extends LeadItem {
  client_id: string;
  source_system: string;
  contact_company: string | null;
  address: string | null;
  sqft: number | null;
  outcome_source: string | null;
  notes: string | null;
  intent: { intent: string | null; proceeded: boolean | null; at: string } | null;
}

export interface ConversationMessage {
  id: string;
  direction: "inbound" | "outbound";
  channel: string;
  body: string;
  ai_generated: boolean;
  created_at: string;
}

export interface RoutingActivity {
  window_days: number;
  total_calls: number;
  breakdown: Record<string, number>;
  genuine_lead_rate: number;
  spam_rate: number;
}

export interface RoutingLogItem {
  created_at: string;
  event_type: string;
  routing_decision: string | null;
  caller: string | null;
  reason: string | null;
  lead_id: string | null;
}

export interface FieldMapping {
  integration: string;
  canonical_field: string;
  external_field: string;
  external_field_type: "standard" | "custom_field" | "custom_property" | "column";
  transform: Record<string, unknown> | null;
  notes: string | null;
}

export interface AIUsage {
  cap: number;
  used: number;
  remaining: number;
  percent_used: number;
  resets_at: string;
}
