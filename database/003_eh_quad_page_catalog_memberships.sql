-- Additive catalog-membership staging tables for the metadata-only expansion bridge.
-- Apply only to the Everything Hub control project after PostgreSQL recovery.
-- These tables do not create verified documents or public search records.
-- No public, auth, storage, DNS, billing, or Dog Tag Day objects are modified.

begin;

create table if not exists eh_quad.eh_page_catalog_candidates (
  candidate_id text primary key,
  title text not null check (length(btrim(title)) > 0),
  target_url text not null check (target_url ~ '^https://'),
  record_kind text not null check (record_kind = 'article_link_metadata'),
  staging_status text not null check (staging_status = 'metadata_only_not_a_verified_record'),
  rights_status text not null check (rights_status = 'per_record_review_required'),
  verified boolean not null default false check (verified = false),
  content_fetched boolean not null default false check (content_fetched = false),
  source_urls jsonb not null default '[]'::jsonb check (jsonb_typeof(source_urls) = 'array'),
  source_hosts jsonb not null default '[]'::jsonb check (jsonb_typeof(source_hosts) = 'array'),
  source_provenance jsonb not null default '[]'::jsonb check (jsonb_typeof(source_provenance) = 'array'),
  discovered_at timestamptz,
  last_seen_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists eh_quad.eh_page_catalog_candidate_memberships (
  membership_id text primary key,
  candidate_id text not null references eh_quad.eh_page_catalog_candidates(candidate_id) on delete cascade,
  catalog_id text not null check (length(btrim(catalog_id)) > 0),
  relationship text not null check (relationship = 'candidate_child_link'),
  membership_status text not null check (membership_status = 'metadata_only_not_public'),
  rights_status text not null check (rights_status = 'per_record_review_required'),
  created_at timestamptz not null default now(),
  unique (candidate_id, catalog_id)
);

create index if not exists eh_page_catalog_candidates_target
  on eh_quad.eh_page_catalog_candidates(target_url);
create index if not exists eh_page_catalog_memberships_catalog
  on eh_quad.eh_page_catalog_candidate_memberships(catalog_id, candidate_id);

alter table eh_quad.eh_page_catalog_candidates enable row level security;
alter table eh_quad.eh_page_catalog_candidate_memberships enable row level security;

-- The importer uses a server-side database connection. No anon/authenticated
-- access is granted, and these staging tables are not part of public search.
grant usage on schema eh_quad to service_role;
grant select, insert, update on eh_quad.eh_page_catalog_candidates to service_role;
grant select, insert, update on eh_quad.eh_page_catalog_candidate_memberships to service_role;
grant usage, select on all sequences in schema eh_quad to service_role;

commit;
