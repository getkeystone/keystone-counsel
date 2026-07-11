-- migrations/001_counsel_schema.sql
--
-- Full schema for keystone-counsel.
-- Chunks carry document classification for ACL-filtered retrieval.
-- Agents, tasks, and audit_entries follow the platform substrate pattern
-- established in keystone-engage.
--
-- Contact center heritage:
--   chunks + classification = knowledge base articles with queue tags
--   classification filtering = skill-based routing at the retrieval layer
--   agents table = routing engine registry
--   audit_entries = compliance logging

BEGIN;

-- ---------------------------------------------------------------
-- pgvector extension (already enabled, idempotent)
-- ---------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------
-- Enums
-- ---------------------------------------------------------------

CREATE TYPE doc_classification AS ENUM (
    'regulatory_guidance',
    'suitability_assessment',
    'kyc_document',
    'legal_opinion',
    'privileged'
);

CREATE TYPE agent_tempo AS ENUM ('fast', 'medium', 'slow', 'deferred');

CREATE TYPE task_state AS ENUM ('created', 'in_progress', 'completed', 'failed');

-- ---------------------------------------------------------------
-- Chunks table with classification and pgvector embedding
-- ---------------------------------------------------------------

CREATE TABLE chunks (
    id              SERIAL PRIMARY KEY,
    chunk_id        TEXT UNIQUE NOT NULL,
    content         TEXT NOT NULL,
    source_document TEXT NOT NULL,
    section         TEXT NOT NULL,
    classification  doc_classification NOT NULL,
    evidence_tier   TEXT NOT NULL DEFAULT 'verified',
    jurisdiction    TEXT,
    client_id       TEXT,
    embedding       vector(768),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- HNSW index for similarity search
CREATE INDEX idx_chunks_embedding ON chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Classification filter index (used in WHERE before vector search)
CREATE INDEX idx_chunks_classification ON chunks (classification);

-- Composite index for classification-filtered similarity queries
CREATE INDEX idx_chunks_class_source ON chunks (classification, source_document);

-- ---------------------------------------------------------------
-- Agents table (platform substrate)
-- ---------------------------------------------------------------

CREATE TABLE agents (
    agent_id        TEXT PRIMARY KEY,
    agent_name      TEXT NOT NULL,
    agent_role      TEXT NOT NULL,
    tempo           agent_tempo NOT NULL,
    cost_profile    JSONB NOT NULL,
    registered_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed counsel-agent-v1
INSERT INTO agents (agent_id, agent_name, agent_role, tempo, cost_profile)
VALUES (
    'counsel-agent-v1',
    'Counsel Agent',
    'retrieval',
    'medium',
    '{
        "typical_input_tokens": 800,
        "typical_output_tokens": 500,
        "typical_latency_ms": 3000,
        "model_used": "qwen2.5:7b-instruct"
    }'::jsonb
);

-- ---------------------------------------------------------------
-- Tasks table (platform substrate)
-- ---------------------------------------------------------------

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE tasks (
    task_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_agent_id  TEXT NOT NULL REFERENCES agents(agent_id),
    state           task_state NOT NULL DEFAULT 'created',
    payload         JSONB NOT NULL,
    budget_cents    INTEGER NOT NULL CHECK (budget_cents >= 0),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_tasks_owner_agent ON tasks (owner_agent_id);
CREATE INDEX idx_tasks_state       ON tasks (state);

CREATE TRIGGER trigger_tasks_updated_at
BEFORE UPDATE ON tasks
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------
-- Audit entries with substrate columns
-- ---------------------------------------------------------------

CREATE TABLE audit_entries (
    id                          SERIAL PRIMARY KEY,
    timestamp                   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event_type                  TEXT NOT NULL,
    actor                       TEXT NOT NULL,
    payload                     JSONB NOT NULL DEFAULT '{}',
    prev_hash                   TEXT NOT NULL DEFAULT '',
    curr_hash                   TEXT NOT NULL DEFAULT '',
    agent_id                    TEXT REFERENCES agents(agent_id),
    tempo                       agent_tempo,
    task_id                     UUID REFERENCES tasks(task_id),
    input_tokens                INTEGER CHECK (input_tokens IS NULL OR input_tokens >= 0),
    output_tokens               INTEGER CHECK (output_tokens IS NULL OR output_tokens >= 0),
    model_used                  TEXT,
    cost_cents                  NUMERIC(12, 4) CHECK (cost_cents IS NULL OR cost_cents >= 0),
    latency_ms                  INTEGER CHECK (latency_ms IS NULL OR latency_ms >= 0),
    session_rolling_cost_cents  NUMERIC(12, 4) CHECK (session_rolling_cost_cents IS NULL OR session_rolling_cost_cents >= 0)
);

CREATE INDEX idx_audit_agent_id ON audit_entries (agent_id);
CREATE INDEX idx_audit_task_id  ON audit_entries (task_id);
CREATE INDEX idx_audit_event    ON audit_entries (event_type);

COMMIT;
