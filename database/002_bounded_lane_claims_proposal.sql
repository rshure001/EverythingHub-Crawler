-- Bounded host-safe claims for the independent EverythingHub PostgreSQL schema.
-- This migration targets ONLY database/001_everythinghub_core.sql.
-- It is not compatible with the production Supabase schema without a separate
-- schema review and must not be applied there.

begin;

create index if not exists eh_crawl_queue_claim_cover
  on public.eh_crawl_queue (lane, next_attempt_at, priority, depth, id)
  include (host)
  where status = 'pending';

create or replace function public.claim_eh_crawl_batch_for_lane(
  p_lane text,
  p_batch_size integer default 20,
  p_worker_id text default null
)
returns setof public.eh_crawl_queue
language plpgsql
set search_path = ''
as $function$
declare
  v_worker text := coalesce(nullif(p_worker_id, ''), pg_catalog.gen_random_uuid()::text);
  v_limit integer := least(greatest(coalesce(p_batch_size, 20), 1), 100);
begin
  return query
  with candidate_pool as materialized (
    select q.id, q.host, q.lane, q.priority, q.depth
    from public.eh_crawl_queue q
    where q.lane = p_lane
      and q.status = 'pending'
      and q.next_attempt_at <= pg_catalog.now()
      and not exists (
        select 1 from public.eh_host_leases l
        where l.host = q.host and l.leased_until > pg_catalog.now()
      )
    order by q.priority, q.depth, q.id
    for update of q skip locked
    limit least(v_limit * 8, 800)
  ),
  one_per_host as materialized (
    select distinct on (c.host) c.id, c.host, c.lane
    from candidate_pool c
    order by c.host, c.priority, c.depth, c.id
  ),
  leased as (
    insert into public.eh_host_leases
      (host, lane, lease_owner, leased_until, updated_at)
    select h.host, h.lane, v_worker,
           pg_catalog.now() + interval '3 minutes', pg_catalog.now()
    from one_per_host h
    order by h.host
    limit v_limit
    on conflict (host) do update
      set lane = excluded.lane,
          lease_owner = excluded.lease_owner,
          leased_until = excluded.leased_until,
          updated_at = excluded.updated_at
      where public.eh_host_leases.leased_until <= pg_catalog.now()
    returning host
  ),
  claimed as (
    update public.eh_crawl_queue q
    set status = 'in_progress', locked_at = pg_catalog.now()
    from one_per_host h
    join leased l on l.host = h.host
    where q.id = h.id
    returning q.*
  )
  select c.* from claimed c;
end;
$function$;

commit;
