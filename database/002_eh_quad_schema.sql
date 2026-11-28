-- Everything Hub dedicated schema only.
-- Apply only to the everything-hub-control project after PostgreSQL recovery.
-- No public, auth, storage, or Dog Tag Day objects are modified.
begin;

create schema if not exists eh_quad;

-- Stock PostgreSQL lacks Supabase's service_role; add a compatible non-login role.
do $
begin
  if not exists (select 1 from pg_roles where rolname = 'service_role') then
    create role service_role nologin;
  end if;
end
$;

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
