-- Hurricane Core intelligence storage.
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
  document_id bigint references eh_quad.eh_documents(id) on delete set null,
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
