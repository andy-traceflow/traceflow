-- migrations/005_create_kb_tables.sql
--
-- Knowledge base for SIA Module C ("Knowledge Engine"). Tenant-scoped
-- by client_id; every CRUD operation runs through RLS.
--
-- Schema generalizes the per-vertical product taxonomy from the source
-- KB system into a generic tag array. A surface contractor stores
-- product names + substrates as tags; an HVAC contractor stores brands
-- + system types — same table, different tag namespace per client.
--
-- pgvector embeddings live in kb_chunks for retrieval-augmented Q&A.
-- Enable the pgvector extension in Supabase before running.
--
-- HOW TO RUN:
--   1. In Supabase Dashboard > Database > Extensions, enable `vector`.
--   2. Open SQL Editor > New query, paste this file, click Run.

BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------------
-- kb_entries: the canonical question/answer pairs
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kb_entries (
    id           BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    client_id    UUID         NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    question     TEXT         NOT NULL,
    answer       TEXT         NOT NULL,
    category     TEXT         NOT NULL DEFAULT '',
    tags         TEXT[]       NOT NULL DEFAULT '{}',           -- per-client vocabulary (products, services, regions, etc.)
    source       TEXT         NOT NULL DEFAULT '',             -- 'manual' | 'csv_import' | 'pdf_extract' | 'unanswered_promoted'
    embedding    vector(1536),                                  -- nullable until indexed
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_kb_entries_client_created
    ON kb_entries(client_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_kb_entries_category
    ON kb_entries(client_id, category);

CREATE INDEX IF NOT EXISTS idx_kb_entries_tags
    ON kb_entries USING GIN (tags);

-- Vector similarity index — cosine distance, IVFFlat for speed.
-- Rebuild with `REINDEX` after large bulk imports.
CREATE INDEX IF NOT EXISTS idx_kb_entries_embedding
    ON kb_entries USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

CREATE TRIGGER trg_kb_entries_updated_at BEFORE UPDATE ON kb_entries
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE kb_entries ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON kb_entries
    FOR ALL
    USING (client_id = current_setting('app.current_client_id', true)::uuid);


-- ---------------------------------------------------------------------------
-- kb_documents + kb_chunks: source documents and their chunked, embedded form
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kb_documents (
    id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id     UUID         NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    source_type   TEXT         NOT NULL,                                   -- 'pdf' | 'website' | 'csv' | 'manual' | etc.
    title         TEXT         NOT NULL,
    source_url    TEXT,
    raw_content   TEXT,
    metadata      JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_kb_documents_client
    ON kb_documents(client_id, created_at DESC);

CREATE TRIGGER trg_kb_documents_updated_at BEFORE UPDATE ON kb_documents
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE kb_documents ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON kb_documents
    FOR ALL
    USING (client_id = current_setting('app.current_client_id', true)::uuid);


CREATE TABLE IF NOT EXISTS kb_chunks (
    id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id     UUID         NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    document_id   UUID         NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
    chunk_index   INTEGER      NOT NULL,
    content       TEXT         NOT NULL,
    embedding     vector(1536),
    metadata      JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_kb_chunks_doc
    ON kb_chunks(document_id, chunk_index);

CREATE INDEX IF NOT EXISTS idx_kb_chunks_embedding
    ON kb_chunks USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

ALTER TABLE kb_chunks ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON kb_chunks
    FOR ALL
    USING (client_id = current_setting('app.current_client_id', true)::uuid);

COMMIT;
