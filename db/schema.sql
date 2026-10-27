-- BidCraft database schema
-- PostgreSQL 16 with the pgvector extension. Safe to re-run.
-- Applied by deploy/install.sh as the bidcraft user (who owns the tables):
--   sudo -u bidcraft psql -v ON_ERROR_STOP=1 -d bidcraft < db/schema.sql

BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS users (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email         text NOT NULL UNIQUE,
  full_name     text NOT NULL,
  password_hash text NOT NULL,
  role          text NOT NULL DEFAULT 'bid_manager'
                CHECK (role IN ('admin', 'bid_manager', 'reviewer')),
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- Organisation library: experience, statutory, HR and literature documents.
-- Files stay on local disk; only their path is stored here.
CREATE TABLE IF NOT EXISTS library_documents (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  category       text NOT NULL
                 CHECK (category IN ('experience', 'statutory', 'hr', 'literature', 'uploads')),
  doc_type       text NOT NULL DEFAULT 'other',
  title          text NOT NULL,
  file_path      text NOT NULL,            -- relative to FILES_ROOT
  pdf_path       text,                     -- PDF rendition (converted / OCR'd), relative
  original_name  text NOT NULL,
  mime_type      text NOT NULL,
  page_count     int,
  status         text NOT NULL DEFAULT 'uploaded'
                 CHECK (status IN ('uploaded', 'processing', 'ready', 'failed')),
  error          text,
  metadata       jsonb NOT NULL DEFAULT '{}'::jsonb,
  valid_until    date,
  -- public/internal: may be used as evidence after redaction
  -- confidential:    every outbound request using it needs reviewer approval
  -- restricted:      never leaves the server
  sensitivity    text NOT NULL DEFAULT 'internal'
                 CHECK (sensitivity IN ('public', 'internal', 'confidential', 'restricted')),
  extracted_text text,
  file_sha256    text,                     -- SHA-256 of the uploaded file, to spot duplicates
  created_by     uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS library_documents_category_idx ON library_documents (category, status);

CREATE TABLE IF NOT EXISTS bids (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  title              text NOT NULL,
  rfp_ref            text,
  department         text,
  due_at             timestamptz,
  status             text NOT NULL DEFAULT 'new'
                     CHECK (status IN ('new', 'analysing', 'analysed', 'drafting', 'drafted',
                                       'assembling', 'ready', 'failed')),
  error              text,
  rfp_files          jsonb NOT NULL DEFAULT '[]'::jsonb,   -- [{path, name}]
  analysis           jsonb NOT NULL DEFAULT '{}'::jsonb,
  settings           jsonb NOT NULL DEFAULT '{}'::jsonb,   -- sign, stamp, page numbers, header
  output_pdf_path    text,
  output_page_count  int,
  created_by         uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now()
);

-- Local vector store. Embeddings are computed on this server.
CREATE TABLE IF NOT EXISTS doc_chunks (
  id           bigserial PRIMARY KEY,
  document_id  uuid REFERENCES library_documents(id) ON DELETE CASCADE,
  bid_id       uuid REFERENCES bids(id) ON DELETE CASCADE,   -- chunks of an RFP
  chunk_index  int NOT NULL,
  page_from    int,
  page_to      int,
  heading      text,
  content      text NOT NULL,
  tsv          tsvector GENERATED ALWAYS AS
               (to_tsvector('english', coalesce(heading, '') || ' ' || content)) STORED,
  embedding    vector(384) NOT NULL,
  CHECK ((document_id IS NULL) <> (bid_id IS NULL))
);
CREATE INDEX IF NOT EXISTS doc_chunks_embedding_idx ON doc_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS doc_chunks_tsv_idx ON doc_chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS doc_chunks_document_idx ON doc_chunks (document_id);
CREATE INDEX IF NOT EXISTS doc_chunks_bid_idx ON doc_chunks (bid_id);

CREATE TABLE IF NOT EXISTS bid_sections (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  bid_id      uuid NOT NULL REFERENCES bids(id) ON DELETE CASCADE,
  position    int NOT NULL,
  kind        text NOT NULL CHECK (kind IN ('cover', 'index', 'text', 'docs')),
  title       text NOT NULL,
  html        text NOT NULL DEFAULT '',
  include     boolean NOT NULL DEFAULT true,
  sources     jsonb NOT NULL DEFAULT '[]'::jsonb,
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS bid_sections_bid_idx ON bid_sections (bid_id, position);

CREATE TABLE IF NOT EXISTS bid_section_documents (
  id           bigserial PRIMARY KEY,
  section_id   uuid NOT NULL REFERENCES bid_sections(id) ON DELETE CASCADE,
  document_id  uuid NOT NULL REFERENCES library_documents(id) ON DELETE CASCADE,
  position     int NOT NULL
);
CREATE INDEX IF NOT EXISTS bid_section_documents_section_idx ON bid_section_documents (section_id, position);

-- Work queue shared by the API (producer) and the AI worker (consumer).
CREATE TABLE IF NOT EXISTS jobs (
  id                 bigserial PRIMARY KEY,
  type               text NOT NULL
                     CHECK (type IN ('ingest_document', 'analyze_rfp', 'draft_bid', 'assemble_pdf', 'redraft_section', 'extract_project', 'extract_cv', 'match_rfp', 'sync_draft')),
  payload            jsonb NOT NULL,
  status             text NOT NULL DEFAULT 'queued'
                     CHECK (status IN ('queued', 'running', 'awaiting_approval', 'done', 'failed')),
  waiting_egress_id  uuid,
  result             jsonb,
  error              text,
  attempts           int NOT NULL DEFAULT 0,
  created_by         uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at         timestamptz NOT NULL DEFAULT now(),
  started_at         timestamptz,
  finished_at        timestamptz
);
CREATE INDEX IF NOT EXISTS jobs_queue_idx ON jobs (status, id);

-- Every request that leaves the premises, exactly as sent (after redaction).
CREATE TABLE IF NOT EXISTS egress_requests (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  bid_id             uuid REFERENCES bids(id) ON DELETE SET NULL,
  job_id             bigint,
  purpose            text NOT NULL,
  status             text NOT NULL
                     CHECK (status IN ('pending', 'approved', 'rejected', 'sent', 'failed')),
  payload_sha256     text NOT NULL,
  redacted_payload   jsonb NOT NULL,      -- system + user text as transmitted
  redaction_summary  jsonb NOT NULL,      -- counts per entity type, dropped evidence
  source_refs        jsonb NOT NULL DEFAULT '[]'::jsonb,
  char_count         int NOT NULL,
  model              text NOT NULL,
  response_redacted  jsonb,
  response_meta      jsonb,
  error              text,
  reviewed_by        uuid REFERENCES users(id) ON DELETE SET NULL,
  review_note        text,
  reviewed_at        timestamptz,
  created_at         timestamptz NOT NULL DEFAULT now(),
  sent_at            timestamptz
);
CREATE INDEX IF NOT EXISTS egress_hash_idx ON egress_requests (payload_sha256, purpose);
CREATE INDEX IF NOT EXISTS egress_status_idx ON egress_requests (status, created_at);

-- Original values behind redaction tokens, encrypted with VAULT_KEY. Never transmitted.
CREATE TABLE IF NOT EXISTS redaction_vault (
  id            bigserial PRIMARY KEY,
  egress_id     uuid NOT NULL REFERENCES egress_requests(id) ON DELETE CASCADE,
  token         text NOT NULL,
  original_enc  bytea NOT NULL,
  UNIQUE (egress_id, token)
);

CREATE TABLE IF NOT EXISTS audit_log (
  id         bigserial PRIMARY KEY,
  at         timestamptz NOT NULL DEFAULT now(),
  actor      text NOT NULL,
  action     text NOT NULL,
  entity     text,
  entity_id  text,
  detail     jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS audit_log_at_idx ON audit_log (at DESC);

-- upgrades for databases created by earlier versions
ALTER TABLE library_documents ADD COLUMN IF NOT EXISTS file_sha256 text;
CREATE INDEX IF NOT EXISTS library_documents_sha_idx ON library_documents (file_sha256);
-- Signature and stamp images, saved once and chosen for each bid on the Preview & sign screen
CREATE TABLE IF NOT EXISTS sign_images (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind        text NOT NULL CHECK (kind IN ('signature', 'stamp')),
  name        text NOT NULL,
  file_path   text NOT NULL,
  created_by  uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_type_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_type_check
  CHECK (type IN ('ingest_document', 'analyze_rfp', 'draft_bid', 'assemble_pdf', 'redraft_section', 'extract_project', 'extract_cv', 'match_rfp', 'sync_draft'));

COMMIT;
