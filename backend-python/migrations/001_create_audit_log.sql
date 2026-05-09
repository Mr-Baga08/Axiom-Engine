-- Migration: create_audit_log
CREATE TABLE IF NOT EXISTS audit_log (
    id            BIGSERIAL PRIMARY KEY,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_uid      TEXT        NOT NULL,           -- UID from JWT subject claim
    user_role     TEXT        NOT NULL,           -- role from JWT
    action        TEXT        NOT NULL,           -- e.g. 'query', 'ingest', 'delete'
    resource      TEXT        NOT NULL,           -- e.g. 'document:42', 'tool:render_chart'
    query_hash    TEXT        NOT NULL,           -- SHA-256 of the raw user query
    ip_address    TEXT,
    status        TEXT        NOT NULL DEFAULT 'success',  -- 'success' | 'denied' | 'error'
    detail        JSONB
);

-- The application role must only be able to INSERT, never UPDATE or DELETE.
-- Replace 'app_role' with the actual role name used by the connection string.
REVOKE UPDATE, DELETE ON audit_log FROM app_role;
GRANT INSERT, SELECT ON audit_log TO app_role;

-- Index for per-user lookups (GDPR erasure, compliance queries)
CREATE INDEX IF NOT EXISTS idx_audit_log_user_uid ON audit_log(user_uid);
CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON audit_log(created_at);