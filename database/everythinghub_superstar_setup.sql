-- Everything Hub complete SQL bootstrap bundle
-- Version: 1.0
-- Scope: dedicated eh_quad schema ONLY in the Everything Hub project.
-- Does not touch public, auth, storage, Dog Tag Day, or existing rows.
-- Safe to re-run: source migrations use CREATE IF NOT EXISTS / additive ALTERs.
-- IMPORTANT: Apply only after PostgreSQL accepts connections and after a backup.
-- This bundle does not create credentials, change connection strings, or import staged data.

\n-- ============================================================================\n-- database/002_eh_quad_schema.sql\n-- ============================================================================\n\n-- Everything Hub dedicated schema only.
-- Apply only to the everything-hub-control project after PostgreSQL recovery.
-- No public, auth, storage, or Dog Tag Day objects are modified.
begin;

create schema if not exists eh_quad;

create table if not exists eh_quad.eh_documents (
  id bigserial primary key,
  canonical_url text not null unique,
  source_host text not null,
  lane text not null check (lane in
    ('general','science','news','technology','business','government','culture','nature')),
  title text not null default '',
  description text not null default '',
  body_text text not null default '',
  language_code text,
  http_status integer,
  content_type text,
  content_hash text,
  fetched_at timestamptz not null default now(),
  published_at timestamptz,
  robots_allowed boolean not null default true,
  search_vector tsvector generated always as (
    setweight(to_tsvector('english', coalesce(title,'')), 'A') ||
    setweight(to_tsvector('english', coalesce(description,'')), 'B') ||
    setweight(to_tsvector('english', coalesce(body_text,'')), 'C')
  ) stored,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists eh_quad.eh_crawl_queue (
  id bigserial primary key,
  url text not null,
  host text not null,
  lane text not null check (lane in
    ('general','science','news','technology','business','government','culture','nature')),
  depth smallint not null default 0 check (depth >= 0),
  priority integer not null default 100,
  status text not null default 'pending'
    check (status in ('pending','in_progress','done','failed','skipped')),
  attempts smallint not null default 0,
  next_attempt_at timestamptz not null default now(),
  locked_at timestamptz,
  last_error text,
  discovered_at timestamptz not null default now(),
  unique (url, lane)
);

create table if not exists eh_quad.eh_host_leases (
  host text primary key,
  lane text not null,
  lease_owner text not null,
  leased_until timestamptz not null,
  updated_at timestamptz not null default now()
);

create table if not exists eh_quad.eh_crawl_runs (
  id bigserial primary key,
  lane text not null,
  worker_id text,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  claimed_count integer not null default 0,
  fetched_count integer not null default 0,
  indexed_count integer not null default 0,
  failed_count integer not null default 0,
  status text not null default 'running'
    check (status in ('running','succeeded','partial','failed')),
  error_summary text
);

create table if not exists eh_quad.eh_media (
  id bigserial primary key,
  document_id bigint references eh_quad.eh_documents(id) on delete cascade,
  page_url text not null,
  media_url text not null,
  media_type text not null,
  alt_text text,
  discovered_at timestamptz not null default now(),
  unique (page_url, media_url)
);

create table if not exists eh_quad.eh_lane_state (
  lane text primary key check (lane in
    ('general','science','news','technology','business','government','culture','nature')),
  enabled boolean not null default true,
  max_workers integer not null default 4 check (max_workers between 1 and 32),
  last_claimed_at timestamptz,
  updated_at timestamptz not null default now()
);

insert into eh_quad.eh_lane_state(lane) values
 ('general'),('science'),('news'),('technology'),
 ('business'),('government'),('culture'),('nature')
on conflict (lane) do nothing;

create index if not exists eh_documents_search_gin
  on eh_quad.eh_documents using gin(search_vector);
create index if not exists eh_documents_lane_fetched
  on eh_quad.eh_documents(lane, fetched_at desc);
create index if not exists eh_documents_host
  on eh_quad.eh_documents(source_host);
create index if not exists eh_crawl_queue_claim
  on eh_quad.eh_crawl_queue(lane, next_attempt_at, priority, depth, id)
  where status = 'pending';
create index if not exists eh_crawl_queue_stale
  on eh_quad.eh_crawl_queue(lane, locked_at)
  where status = 'in_progress';
create index if not exists eh_crawl_queue_host
  on eh_quad.eh_crawl_queue(host, status, next_attempt_at);
create index if not exists eh_host_leases_expiry
  on eh_quad.eh_host_leases(leased_until);
create index if not exists eh_crawl_runs_lane_started
  on eh_quad.eh_crawl_runs(lane, started_at desc);
create index if not exists eh_media_document
  on eh_quad.eh_media(document_id);

-- Keep all dedicated-schema tables protected if eh_quad is exposed through PostgREST.
alter table eh_quad.eh_documents enable row level security;
alter table eh_quad.eh_crawl_queue enable row level security;
alter table eh_quad.eh_host_leases enable row level security;
alter table eh_quad.eh_crawl_runs enable row level security;
alter table eh_quad.eh_media enable row level security;
alter table eh_quad.eh_lane_state enable row level security;

-- Only the server-side service role receives read access for the search RPC.
-- No anon or authenticated grants are created.
grant usage on schema eh_quad to service_role;
grant select on eh_quad.eh_documents to service_role;

create or replace function eh_quad.eh_search_pages(
  query_text text,
  lane_filter text default null,
  result_limit integer default 10
)
returns table (
  title text,
  url text,
  description text,
  tentacle text,
  fetched_at timestamptz,
  relevance real,
  total_count bigint
)
language sql
stable
security invoker
set search_path = pg_catalog, eh_quad
as $$
  with q as (
    select plainto_tsquery('english', coalesce(query_text, '')) as tsq
  ),
  matched as (
    select d.title, d.canonical_url, d.description, d.lane, d.fetched_at,
           ts_rank(d.search_vector, q.tsq)::real as rank_value,
           count(*) over () as matched_count
    from eh_quad.eh_documents d
    cross join q
    where q.tsq <> ''::tsquery
      and d.search_vector @@ q.tsq
      and d.robots_allowed is true
      and (lane_filter is null or d.lane = lane_filter)
  )
  select m.title, m.canonical_url, m.description, m.lane, m.fetched_at,
         m.rank_value, m.matched_count
  from matched m
  order by m.rank_value desc, m.fetched_at desc
  limit greatest(1, least(coalesce(result_limit, 10), 50));
$$;

grant execute on function eh_quad.eh_search_pages(text, text, integer) to service_role;

commit;
\n\n-- ============================================================================\n-- database/003_eh_quad_intelligence.sql\n-- ============================================================================\n\n-- Hurricane Core intelligence storage.
-- Apply only after PostgreSQL recovery, to the dedicated Everything Hub project.
-- Depends on 002_eh_quad_schema.sql. Additive only; no source or user data is deleted.
begin;

alter table eh_quad.eh_documents
  add column if not exists source_quality_score numeric(4,3) not null default 0.500;

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conname = 'eh_documents_source_quality_score_range'
      and conrelid = 'eh_quad.eh_documents'::regclass
  ) then
    alter table eh_quad.eh_documents
      add constraint eh_documents_source_quality_score_range
      check (source_quality_score between 0 and 1);
  end if;
end
$$;

-- Domain-level source assessments are explicit and reviewable; domains are
-- never assigned credibility automatically from their names.
create table if not exists eh_quad.eh_source_registry (
  source_host text primary key,
  quality_score numeric(4,3) not null default 0.500
    check (quality_score between 0 and 1),
  review_status text not null default 'unrated'
    check (review_status in ('unrated','community','reviewed','authoritative')),
  assessment_method text not null default 'unreviewed'
    check (assessment_method in ('unreviewed','manual_review','verified_registry')),
  reviewed_at timestamptz,
  notes text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Topic assignments are separate from documents so entity/topic resolution
-- can be audited and revised without rewriting crawler content.
create table if not exists eh_quad.eh_topics (
  topic_key text primary key,
  canonical_label text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists eh_quad.eh_document_topics (
  document_id bigint not null references eh_quad.eh_documents(id) on delete cascade,
  topic_key text not null references eh_quad.eh_topics(topic_key) on delete cascade,
  assignment_method text not null
    check (assignment_method in ('manual','entity_match','classifier','query_cluster')),
  match_confidence numeric(4,3) not null default 0.500
    check (match_confidence between 0 and 1),
  verified boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (document_id, topic_key)
);

create index if not exists eh_document_topics_topic
  on eh_quad.eh_document_topics(topic_key, verified, match_confidence desc);

-- Evidence links record relationships, not truth. Contradictions are retained
-- alongside corroborations so ranking does not mistake repetition for certainty.
create table if not exists eh_quad.eh_evidence_links (
  id bigserial primary key,
  from_document_id bigint not null references eh_quad.eh_documents(id) on delete cascade,
  to_document_id bigint not null references eh_quad.eh_documents(id) on delete cascade,
  topic_key text references eh_quad.eh_topics(topic_key) on delete set null,
  relation_type text not null
    check (relation_type in ('corroborates','contradicts','updates','references')),
  confidence numeric(4,3) not null default 0.500
    check (confidence between 0 and 1),
  verification_status text not null default 'unreviewed'
    check (verification_status in ('unreviewed','reviewed','verified')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (from_document_id <> to_document_id),
  unique (from_document_id, to_document_id, relation_type)
);

create index if not exists eh_evidence_links_target
  on eh_quad.eh_evidence_links(to_document_id, relation_type);
create index if not exists eh_evidence_links_topic
  on eh_quad.eh_evidence_links(topic_key, verification_status);

-- Feedback is append-only and stores no account IDs, email addresses, or raw
-- user identity. Only explicit labels and validated numeric feature snapshots
-- are eligible for the bounded learning function.
create table if not exists eh_quad.eh_intelligence_feedback (
  id bigserial primary key,
  document_id bigint references eh_quad.eh_documents(id) on delete restrict,
  feedback_label text not null
    check (feedback_label in ('helpful','not_helpful','verified','disputed')),
  feature_snapshot jsonb not null
    check (jsonb_typeof(feature_snapshot) = 'object'),
  feedback_version text not null default 'explicit_feedback_bounded_v1',
  created_at timestamptz not null default now()
);

create index if not exists eh_intelligence_feedback_created
  on eh_quad.eh_intelligence_feedback(created_at desc);
create index if not exists eh_intelligence_feedback_document
  on eh_quad.eh_intelligence_feedback(document_id, feedback_label);

create table if not exists eh_quad.eh_rank_profiles (
  profile_name text primary key,
  weights jsonb not null
    default '{"relevance":0.45,"source_quality":0.25,"freshness":0.15,"corroboration":0.15}'::jsonb
    check (jsonb_typeof(weights) = 'object'),
  model_version text not null default 'hurricane_core_v1',
  feedback_count bigint not null default 0 check (feedback_count >= 0),
  updated_at timestamptz not null default now()
);

insert into eh_quad.eh_rank_profiles(profile_name)
values ('default')
on conflict (profile_name) do nothing;

alter table eh_quad.eh_source_registry enable row level security;
alter table eh_quad.eh_topics enable row level security;
alter table eh_quad.eh_document_topics enable row level security;
alter table eh_quad.eh_evidence_links enable row level security;
alter table eh_quad.eh_intelligence_feedback enable row level security;
alter table eh_quad.eh_rank_profiles enable row level security;

-- No anon/authenticated/public access. Only the server-side service role may
-- operate the intelligence tables. Feedback has no UPDATE or DELETE grant.
grant select, insert, update on
  eh_quad.eh_source_registry,
  eh_quad.eh_topics,
  eh_quad.eh_document_topics,
  eh_quad.eh_evidence_links,
  eh_quad.eh_rank_profiles
to service_role;
grant select, insert on eh_quad.eh_intelligence_feedback to service_role;
grant usage, select on sequence eh_quad.eh_evidence_links_id_seq to service_role;
grant usage, select on sequence eh_quad.eh_intelligence_feedback_id_seq to service_role;

commit;
\n\n-- ============================================================================\n-- database/004_eh_quad_feedback_cursor.sql\n-- ============================================================================\n\n-- Add a monotonic feedback cursor so each explicit event is learned at most once.
-- Apply only after 003_eh_quad_intelligence.sql, in the Everything Hub eh_quad schema.
-- Additive and idempotent; no feedback or document rows are modified.
begin;

alter table eh_quad.eh_rank_profiles
  add column if not exists last_feedback_id bigint not null default 0;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'eh_rank_profiles_last_feedback_id_nonnegative'
      and conrelid = 'eh_quad.eh_rank_profiles'::regclass
  ) then
    alter table eh_quad.eh_rank_profiles
      add constraint eh_rank_profiles_last_feedback_id_nonnegative
      check (last_feedback_id >= 0);
  end if;
end
$$;

commit;
\n\n-- ============================================================================\n-- database/005_eh_quad_catalogs.sql\n-- ============================================================================\n\n-- Additive catalog-first storage for Everything Hub. Apply only in eh_quad.
begin;

create table if not exists eh_quad.eh_catalogs (
  catalog_id text primary key,
  source_id text not null,
  title text not null,
  description text not null default '',
  catalog_type text not null,
  subject text not null default '',
  publisher text not null default '',
  geography text not null default '',
  source_url text not null,
  access_method text not null default 'web',
  coverage_status text not null default 'unknown',
  record_count bigint not null default 0 check (record_count >= 0),
  last_discovered_at timestamptz,
  last_synced_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (source_id)
);

create table if not exists eh_quad.eh_catalog_records (
  record_id text primary key,
  catalog_id text not null references eh_quad.eh_catalogs(catalog_id) on delete restrict,
  record_type text not null,
  title text not null,
  description text not null default '',
  source_key text not null,
  source_url text not null,
  canonical_entity_key text not null default '',
  subject text not null default '',
  geography text not null default '',
  attributes jsonb not null default '{}'::jsonb,
  last_seen_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  search_vector tsvector generated always as (
    setweight(to_tsvector('english', coalesce(title,'')), 'A') ||
    setweight(to_tsvector('english', coalesce(description,'')), 'B') ||
    setweight(to_tsvector('english', coalesce(subject,'')), 'C')
  ) stored,
  unique (catalog_id, source_key)
);

create index if not exists eh_catalogs_type_subject
  on eh_quad.eh_catalogs(catalog_type, subject);
create index if not exists eh_catalog_records_catalog
  on eh_quad.eh_catalog_records(catalog_id, record_type);
create index if not exists eh_catalog_records_search_gin
  on eh_quad.eh_catalog_records using gin(search_vector);
create index if not exists eh_catalog_records_entity
  on eh_quad.eh_catalog_records(canonical_entity_key)
  where canonical_entity_key <> '';
create index if not exists eh_catalog_records_subject
  on eh_quad.eh_catalog_records(subject);

alter table eh_quad.eh_catalogs enable row level security;
alter table eh_quad.eh_catalog_records enable row level security;

grant usage on schema eh_quad to service_role;
grant select on eh_quad.eh_catalogs, eh_quad.eh_catalog_records to service_role;

create or replace function eh_quad.eh_search_catalog_records(
  query_text text,
  catalog_type_filter text default null,
  record_type_filter text default null,
  result_limit integer default 20
)
returns table (
  record_id text,
  catalog_id text,
  catalog_title text,
  record_type text,
  title text,
  source_url text,
  description text,
  subject text,
  attributes jsonb,
  relevance real,
  total_count bigint
)
language sql
stable
security invoker
set search_path = pg_catalog, eh_quad
as $$
  with q as (
    select plainto_tsquery('english', coalesce(query_text, '')) as tsq
  ),
  matched as (
    select r.record_id, r.catalog_id, c.title as catalog_title,
           r.record_type, r.title, r.source_url, r.description,
           r.subject, r.attributes,
           ts_rank(r.search_vector, q.tsq)::real as rank_value,
           count(*) over () as matched_count
    from eh_quad.eh_catalog_records r
    join eh_quad.eh_catalogs c using (catalog_id)
    cross join q
    where q.tsq <> ''::tsquery
      and r.search_vector @@ q.tsq
      and (catalog_type_filter is null or c.catalog_type = catalog_type_filter)
      and (record_type_filter is null or r.record_type = record_type_filter)
  )
  select m.record_id, m.catalog_id, m.catalog_title, m.record_type,
         m.title, m.source_url, m.description, m.subject, m.attributes,
         m.rank_value, m.matched_count
  from matched m
  order by m.rank_value desc, m.title
  limit greatest(1, least(coalesce(result_limit, 20), 100));
$$;

grant execute on function eh_quad.eh_search_catalog_records(text, text, text, integer)
  to service_role;

commit;
