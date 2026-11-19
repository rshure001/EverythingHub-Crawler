-- Everything Hub independent PostgreSQL core
-- Fresh database bootstrap only. Does not connect to or modify Supabase.
BEGIN;

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE SCHEMA IF NOT EXISTS eh_search;
SET search_path = eh_search, public;

CREATE TABLE IF NOT EXISTS crawl_sources (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  domain TEXT NOT NULL UNIQUE,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  crawl_delay_seconds INTEGER NOT NULL DEFAULT 5 CHECK (crawl_delay_seconds BETWEEN 0 AND 86400),
  max_pages_per_run INTEGER NOT NULL DEFAULT 100 CHECK (max_pages_per_run > 0),
  allowed_paths TEXT[] NOT NULL DEFAULT ARRAY['/']::TEXT[],
  blocked_paths TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS web_pages (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source_id BIGINT REFERENCES crawl_sources(id) ON DELETE SET NULL,
  canonical_url TEXT NOT NULL UNIQUE,
  url_hash BYTEA NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  body_text TEXT NOT NULL DEFAULT '',
  language_code VARCHAR(16),
  http_status SMALLINT,
  content_type TEXT,
  content_hash BYTEA,
  discovered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  crawled_at TIMESTAMPTZ,
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  is_indexable BOOLEAN NOT NULL DEFAULT TRUE,
  -- Generated expressions must be IMMUTABLE; unaccent is intentionally applied at query time.
  search_document TSVECTOR GENERATED ALWAYS AS (
    setweight(to_tsvector('simple', coalesce(title,'')), 'A') ||
    setweight(to_tsvector('simple', coalesce(description,'')), 'B') ||
    setweight(to_tsvector('simple', coalesce(body_text,'')), 'C')
  ) STORED
);

CREATE INDEX IF NOT EXISTS web_pages_search_gin ON web_pages USING GIN(search_document);
CREATE INDEX IF NOT EXISTS web_pages_title_trgm ON web_pages USING GIN(title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS web_pages_source_crawled ON web_pages(source_id, crawled_at DESC);
CREATE INDEX IF NOT EXISTS web_pages_last_seen ON web_pages(last_seen_at DESC);
CREATE INDEX IF NOT EXISTS web_pages_indexable ON web_pages(is_indexable) WHERE is_indexable;

CREATE TABLE IF NOT EXISTS crawl_queue (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  url TEXT NOT NULL UNIQUE,
  source_id BIGINT REFERENCES crawl_sources(id) ON DELETE SET NULL,
  priority SMALLINT NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','leased','completed','failed','blocked')),
  attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  max_attempts INTEGER NOT NULL DEFAULT 5 CHECK (max_attempts > 0),
  available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  leased_until TIMESTAMPTZ,
  lease_owner TEXT,
  last_error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS crawl_queue_ready
  ON crawl_queue(priority DESC, available_at, id) WHERE status='pending';
CREATE INDEX IF NOT EXISTS crawl_queue_expired
  ON crawl_queue(leased_until) WHERE status='leased';

CREATE TABLE IF NOT EXISTS index_checkpoints (
  worker_name TEXT PRIMARY KEY,
  cursor_value TEXT,
  pages_processed BIGINT NOT NULL DEFAULT 0,
  last_success_at TIMESTAMPTZ,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS system_health (
  component TEXT PRIMARY KEY,
  status TEXT NOT NULL CHECK (status IN ('healthy','degraded','offline','maintenance')),
  checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  details JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS audit_log (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  actor TEXT NOT NULL DEFAULT 'system',
  action TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id TEXT,
  details JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS audit_log_created ON audit_log(created_at DESC);

CREATE OR REPLACE FUNCTION eh_search.touch_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$;
DROP TRIGGER IF EXISTS crawl_sources_touch ON crawl_sources;
CREATE TRIGGER crawl_sources_touch BEFORE UPDATE ON crawl_sources
FOR EACH ROW EXECUTE FUNCTION eh_search.touch_updated_at();
DROP TRIGGER IF EXISTS crawl_queue_touch ON crawl_queue;
CREATE TRIGGER crawl_queue_touch BEFORE UPDATE ON crawl_queue
FOR EACH ROW EXECUTE FUNCTION eh_search.touch_updated_at();

-- Atomic queue leasing; multiple crawler workers can safely claim distinct URLs.
CREATE OR REPLACE FUNCTION eh_search.claim_crawl_jobs(p_worker TEXT, p_limit INTEGER DEFAULT 10, p_lease_seconds INTEGER DEFAULT 300)
RETURNS SETOF eh_search.crawl_queue
LANGUAGE plpgsql AS $$
BEGIN
  IF p_worker IS NULL OR length(btrim(p_worker)) = 0 THEN
    RAISE EXCEPTION 'p_worker must be a non-empty worker name';
  END IF;

  -- Expired jobs that have exhausted their retry budget become terminal.
  UPDATE eh_search.crawl_queue
     SET status='failed', lease_owner=NULL, leased_until=NULL,
         last_error=coalesce(last_error, 'Lease expired after maximum attempts')
   WHERE status='leased'
     AND leased_until < now()
     AND attempts >= max_attempts;

  RETURN QUERY
  WITH candidates AS (
    SELECT q.id FROM eh_search.crawl_queue q
    WHERE q.attempts < q.max_attempts
      AND ((q.status='pending' AND q.available_at <= now())
        OR (q.status='leased' AND q.leased_until < now()))
    ORDER BY q.priority DESC, q.available_at, q.id
    FOR UPDATE SKIP LOCKED
    LIMIT GREATEST(1, LEAST(p_limit, 100))
  )
  UPDATE eh_search.crawl_queue q
     SET status='leased', lease_owner=p_worker,
         leased_until=now() + make_interval(secs => GREATEST(30, LEAST(p_lease_seconds, 3600))),
         attempts=q.attempts+1
    FROM candidates c WHERE q.id=c.id
  RETURNING q.*;
END;
$$;

-- Example search:
-- SELECT id, canonical_url, title, description,
--        ts_rank(search_document, websearch_to_tsquery('simple', unaccent($1))) AS rank
-- FROM eh_search.web_pages
-- WHERE is_indexable AND search_document @@ websearch_to_tsquery('simple', unaccent($1))
-- ORDER BY rank DESC LIMIT 20;

COMMIT;
