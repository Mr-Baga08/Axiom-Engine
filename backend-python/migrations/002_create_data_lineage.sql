-- Migration: create_data_lineage
CREATE TABLE IF NOT EXISTS data_lineage (
    id              BIGSERIAL PRIMARY KEY,
    document_id     TEXT        NOT NULL,     -- FK to your documents table
    source_user_uid TEXT        NOT NULL,     -- UID of the user who ingested this row
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_label    TEXT,                     -- human label: 'upload', 'api', 'crawl'
    pii_detected    BOOLEAN     NOT NULL DEFAULT FALSE,
    pii_entities    JSONB,                    -- array of {type, start, end, score} dicts
    erasure_status  TEXT        NOT NULL DEFAULT 'retained',  -- 'retained' | 'erased'
    erased_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_lineage_source_user ON data_lineage(source_user_uid);
CREATE INDEX IF NOT EXISTS idx_lineage_document    ON data_lineage(document_id);
