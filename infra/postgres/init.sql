-- PostgreSQL production schema for AI-Insights.
-- Column names match the CSV files in data/csv/ exactly —
-- the Go ingestion worker uses CSV headers as PostgreSQL column names via CopyFrom.
-- Idempotent: safe to run multiple times (IF NOT EXISTS / DROP POLICY IF EXISTS).

-- ── Extensions ─────────────────────────────────────────────────────────────

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ── Structured tables (CSV-backed) ────────────────────────────────────────

CREATE TABLE IF NOT EXISTS movies (
  movie_id                INTEGER PRIMARY KEY,
  title                   TEXT    NOT NULL,
  genre                   TEXT    NOT NULL,
  release_date            DATE,
  budget_usd              BIGINT,
  box_office_usd          BIGINT,
  rating                  NUMERIC(3,1) CHECK (rating BETWEEN 0 AND 5),
  director                TEXT,
  release_year            INTEGER,
  runtime_mins            INTEGER,
  streaming_release_date  DATE,
  status                  TEXT
);

CREATE TABLE IF NOT EXISTS viewers (
  viewer_id          INTEGER PRIMARY KEY,
  age                INTEGER,
  gender             TEXT,
  city               TEXT,
  country            TEXT,
  subscription_tier  TEXT,
  preferred_genre    TEXT,
  join_date          DATE
);

CREATE TABLE IF NOT EXISTS watch_activity (
  activity_id           INTEGER PRIMARY KEY,
  viewer_id             INTEGER REFERENCES viewers(viewer_id),
  movie_id              INTEGER REFERENCES movies(movie_id),
  watch_date            DATE,
  watch_duration_mins   INTEGER,
  completion_rate       NUMERIC(4,2),
  device_type           TEXT
);

CREATE TABLE IF NOT EXISTS reviews (
  review_id    INTEGER PRIMARY KEY,
  viewer_id    INTEGER REFERENCES viewers(viewer_id),
  movie_id     INTEGER REFERENCES movies(movie_id),
  rating       NUMERIC(3,1) CHECK (rating BETWEEN 0 AND 5),
  sentiment    TEXT,
  review_text  TEXT,
  review_date  DATE
);

CREATE TABLE IF NOT EXISTS marketing_spend (
  spend_id        INTEGER PRIMARY KEY,
  movie_id        INTEGER REFERENCES movies(movie_id),
  title           TEXT,
  channel         TEXT,
  spend_usd       BIGINT,
  impressions     BIGINT,
  clicks          BIGINT,
  campaign_start  DATE,
  campaign_end    DATE
);

CREATE TABLE IF NOT EXISTS regional_performance (
  region_id             INTEGER PRIMARY KEY,
  movie_id              INTEGER REFERENCES movies(movie_id),
  title                 TEXT,
  city                  TEXT,
  country               TEXT,
  month                 TEXT,
  views                 BIGINT,
  revenue_usd           BIGINT,
  engagement_score      NUMERIC(5,1),
  avg_watch_time_mins   NUMERIC(5,1)
);

-- ── Vector store for document embeddings ──────────────────────────────────

CREATE TABLE IF NOT EXISTS document_chunks (
  id             SERIAL PRIMARY KEY,
  source         TEXT NOT NULL,
  page           INT,
  chunk_text     TEXT NOT NULL,
  embedding      vector(384),
  access_level   TEXT DEFAULT 'internal',
  allowed_roles  TEXT[] DEFAULT ARRAY['analyst','executive'],
  hmac_signature TEXT,
  created_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chunks_embedding
  ON document_chunks USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 100);

-- ── Audit and lineage ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS audit_log (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  timestamp    TIMESTAMPTZ DEFAULT NOW(),
  action       TEXT NOT NULL,
  resource     TEXT,
  user_id      TEXT,
  hashed_query TEXT,
  metadata     JSONB
);

CREATE TABLE IF NOT EXISTS data_lineage (
  id             SERIAL PRIMARY KEY,
  table_name     TEXT,
  row_id         INT,
  source_file    TEXT,
  ingested_at    TIMESTAMPTZ DEFAULT NOW(),
  source_user_id TEXT
);

-- ── Row-Level Security ─────────────────────────────────────────────────────

ALTER TABLE marketing_spend ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS marketing_exec ON marketing_spend;
CREATE POLICY marketing_exec ON marketing_spend FOR SELECT
  USING (current_setting('app.current_user_role', true) = 'executive');

ALTER TABLE document_chunks ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS chunks_role ON document_chunks;
CREATE POLICY chunks_role ON document_chunks FOR SELECT
  USING (current_setting('app.current_user_role', true) = ANY(allowed_roles));

-- ── Application role (least privilege) ────────────────────────────────────

DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_user') THEN
    CREATE ROLE app_user LOGIN PASSWORD 'changeme';
  END IF;
END $$;

GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO app_user;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_user;
REVOKE UPDATE ON audit_log FROM app_user;
