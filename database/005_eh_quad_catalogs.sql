-- Additive catalog-first storage for Everything Hub. Apply only in eh_quad.
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
