-- migrations/008_create_user_permissions.sql
--
-- Per-(client, user) feature toggles for the future client portal. A user
-- can belong to multiple tenants; permissions live per pair. Default
-- behavior (when no row exists) is applied in-code: read-only access,
-- no destructive actions, no admin powers.
--
-- HOW TO RUN:
--   1. Open Supabase Dashboard > SQL Editor > New query
--   2. Paste this entire file
--   3. Click Run

BEGIN;

CREATE TABLE IF NOT EXISTS user_permissions (
    client_id        UUID         NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    user_id          UUID         NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    can_edit_kb      BOOLEAN      NOT NULL DEFAULT FALSE,
    can_delete_kb    BOOLEAN      NOT NULL DEFAULT FALSE,
    can_export       BOOLEAN      NOT NULL DEFAULT TRUE,
    can_view_leads   BOOLEAN      NOT NULL DEFAULT TRUE,
    can_edit_config  BOOLEAN      NOT NULL DEFAULT FALSE,
    is_admin         BOOLEAN      NOT NULL DEFAULT FALSE,
    invited_by       UUID,
    invited_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (client_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_user_permissions_user
    ON user_permissions(user_id);

CREATE TRIGGER trg_user_permissions_updated_at BEFORE UPDATE ON user_permissions
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE user_permissions ENABLE ROW LEVEL SECURITY;

-- Users read only their own rows. Service role bypasses RLS for
-- the backend's permission checks (verified per-request in the auth dep).
CREATE POLICY users_read_own ON user_permissions
    FOR SELECT TO authenticated
    USING (auth.uid() = user_id);

-- Tenant context applies on writes (admin-of-this-client manages perms).
CREATE POLICY tenant_isolation_write ON user_permissions
    FOR INSERT TO authenticated
    WITH CHECK (client_id = current_setting('app.current_client_id', true)::uuid);

CREATE POLICY tenant_isolation_update ON user_permissions
    FOR UPDATE TO authenticated
    USING (client_id = current_setting('app.current_client_id', true)::uuid);

CREATE POLICY tenant_isolation_delete ON user_permissions
    FOR DELETE TO authenticated
    USING (client_id = current_setting('app.current_client_id', true)::uuid);

COMMIT;
