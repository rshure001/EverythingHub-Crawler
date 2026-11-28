-- Metadata-only child-link staging. This is deliberately separate from public records.
-- PostgreSQL-portable staging schema for Supabase or a standalone PostgreSQL host (for example Neon).
-- Metadata-only child-link staging. No public-search objects or verified records are created here.
-- RLS remains enabled; only an explicitly provisioned service_role receives grants.
begin;

create schema if not exists eh_quad;

create table if not exists eh_quad.eh_page_catalog_candidates (
  candidate_id text primary key,
  title text not null,
  target_url text not null,
  record_kind text not null check (record_kind = 'article_link_metadata'),
  staging_status text not null check (staging_status = 'metadata_only_not_a_verified_record'),
  rights_status text not null check (rights_status = 'per_record_review_required'),
  verified boolean not null default false check (verified = false),
  content_fetched boolean not null default false check (content_fetched = false),
  source_urls jsonb not null default '[]'::jsonb,
  source_hosts jsonb not null default '[]'::jsonb,
  source_provenance jsonb not null default '[]'::jsonb,
  discovered_at timestamptz,
  last_seen_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists eh_quad.eh_page_catalog_candidate_memberships (
  membership_id text primary key,
  candidate_id text not null references eh_quad.eh_page_catalog_candidates(candidate_id) on delete restrict,
  catalog_id text not null,
  relationship text not null check (relationship = 'candidate_child_link'),
  membership_status text not null check (membership_status = 'metadata_only_not_public'),
  rights_status text not null check (rights_status = 'per_record_review_required'),
  created_at timestamptz not null default now(),
  unique (candidate_id, catalog_id)
);

create index if not exists eh_page_catalog_candidate_memberships_catalog
  on eh_quad.eh_page_catalog_candidate_memberships(catalog_id);
create index if not exists eh_page_catalog_candidates_target
  on eh_quad.eh_page_catalog_candidates(target_url);

alter table eh_quad.eh_page_catalog_candidates enable row level security;
alter table eh_quad.eh_page_catalog_candidate_memberships enable row level security;
do $grant$
begin
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    grant usage on schema eh_quad to service_role;
    grant select, insert, update on eh_quad.eh_page_catalog_candidates,
      eh_quad.eh_page_catalog_candidate_memberships to service_role;
  end if;
end
$grant$;

commit;
