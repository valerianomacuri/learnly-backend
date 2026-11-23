-- Runs once, on first initialisation of the data volume (after 01-*.sql).
-- pgvector is used by the RAG knowledge base (specs/007-rag/).
-- For an existing volume run once in each database:
--   CREATE EXTENSION IF NOT EXISTS vector;
\connect customer_support
CREATE EXTENSION IF NOT EXISTS vector;
\connect customer_support_test
CREATE EXTENSION IF NOT EXISTS vector;
