CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS knowledge_index_state (
  id text PRIMARY KEY,
  payload jsonb NOT NULL,
  updated_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_items (
  id text PRIMARY KEY,
  type text NOT NULL,
  title text NOT NULL,
  domain_tags text[] NOT NULL,
  market_tags text[] NOT NULL,
  platform_tags text[] NOT NULL,
  trust_level text NOT NULL,
  source_type text NOT NULL,
  source_uri text NOT NULL,
  version integer NOT NULL,
  status text NOT NULL,
  content_hash text NOT NULL,
  content text NOT NULL,
  created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_chunks (
  chunk_id text PRIMARY KEY,
  item_id text NOT NULL REFERENCES knowledge_items(id),
  source_id text NOT NULL,
  chunk_index integer NOT NULL,
  text text NOT NULL,
  embedding vector(64),
  embedding_model text NOT NULL,
  stages text[] NOT NULL DEFAULT '{}',
  metadata jsonb NOT NULL DEFAULT '{}',
  search_vector tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
  status text NOT NULL,
  content_hash text NOT NULL,
  updated_at timestamptz NOT NULL
);
ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS title text NOT NULL DEFAULT '';
ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS embedding_model text NOT NULL DEFAULT 'local/hash-embedding-64';
ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS stages text[] NOT NULL DEFAULT '{}';
ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}';
ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();
CREATE INDEX IF NOT EXISTS knowledge_chunks_embedding_hnsw ON knowledge_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS knowledge_chunks_search_vector_idx ON knowledge_chunks USING gin (search_vector);
CREATE INDEX IF NOT EXISTS knowledge_chunks_stages_idx ON knowledge_chunks USING gin (stages);
CREATE TABLE IF NOT EXISTS knowledge_query_embeddings (
  cache_key text PRIMARY KEY,
  normalized_query text NOT NULL,
  stage text,
  embedding_provider text NOT NULL,
  embedding_model text NOT NULL,
  knowledge_version text NOT NULL,
  embedding vector(64) NOT NULL,
  created_at timestamptz NOT NULL,
  last_used_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS knowledge_query_embeddings_lookup_idx ON knowledge_query_embeddings (
  embedding_provider, embedding_model, knowledge_version, last_used_at
);
CREATE TABLE IF NOT EXISTS knowledge_sources (id text PRIMARY KEY, payload jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS knowledge_feedback (id bigserial PRIMARY KEY, chunk_id text NOT NULL, feedback jsonb NOT NULL, created_at timestamptz NOT NULL);
CREATE TABLE IF NOT EXISTS knowledge_candidates (candidate_id text PRIMARY KEY, status text NOT NULL, payload jsonb NOT NULL, created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL);
