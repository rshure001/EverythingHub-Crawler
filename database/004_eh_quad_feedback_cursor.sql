-- Add a monotonic feedback cursor so each explicit event is learned at most once.
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
