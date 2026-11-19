-- Read-only recovery preflight for the dedicated Everything Hub database.
-- Run only against everything-hub-control after PostgreSQL accepts connections.
-- Every statement is SELECT-only. No schema, table, role, policy, queue, or row changes.

select
  current_database() as database_name,
  current_setting('server_version') as server_version,
  current_timestamp as checked_at,
  current_user as executed_as;

-- Relation inventory and approximate live-row counts for eh_quad only.
select
  n.nspname as schema_name,
  c.relname as object_name,
  case c.relkind
    when 'r' then 'table'
    when 'p' then 'partitioned table'
    when 'v' then 'view'
    when 'm' then 'materialized view'
    else c.relkind::text
  end as object_type,
  c.relrowsecurity as rls_enabled,
  c.relforcerowsecurity as rls_forced,
  coalesce(s.n_live_tup, 0)::bigint as estimated_live_rows
from pg_catalog.pg_class as c
join pg_catalog.pg_namespace as n on n.oid = c.relnamespace
left join pg_catalog.pg_stat_user_tables as s on s.relid = c.oid
where n.nspname = 'eh_quad'
  and c.relkind in ('r', 'p', 'v', 'm')
order by c.relname;

-- Expected canonical objects. Missing objects appear as false, not as destructive repair.
select
  to_regnamespace('eh_quad') is not null as schema_exists,
  to_regclass('eh_quad.eh_documents') is not null as documents_exists,
  to_regclass('eh_quad.eh_crawl_queue') is not null as crawl_queue_exists,
  to_regclass('eh_quad.eh_host_leases') is not null as host_leases_exists,
  to_regclass('eh_quad.eh_crawl_runs') is not null as crawl_runs_exists,
  to_regclass('eh_quad.eh_media') is not null as media_exists,
  to_regclass('eh_quad.eh_lane_state') is not null as lane_state_exists,
  to_regprocedure('eh_quad.eh_search_pages(text,text,integer)') is not null as search_rpc_exists;

-- Verify intended service-role access and absence of direct document reads for public roles.
select
  case when to_regrole('service_role') is not null and to_regnamespace('eh_quad') is not null
    then has_schema_privilege(to_regrole('service_role'), to_regnamespace('eh_quad'), 'USAGE') end
    as service_role_schema_usage,
  case when to_regrole('service_role') is not null and to_regclass('eh_quad.eh_documents') is not null
    then has_table_privilege(to_regrole('service_role'), to_regclass('eh_quad.eh_documents'), 'SELECT') end
    as service_role_documents_select,
  case when to_regrole('service_role') is not null and to_regprocedure('eh_quad.eh_search_pages(text,text,integer)') is not null
    then has_function_privilege(to_regrole('service_role'), to_regprocedure('eh_quad.eh_search_pages(text,text,integer)'), 'EXECUTE') end
    as service_role_search_execute,
  case when to_regrole('anon') is not null and to_regclass('eh_quad.eh_documents') is not null
    then has_table_privilege(to_regrole('anon'), to_regclass('eh_quad.eh_documents'), 'SELECT') end
    as anon_documents_select,
  case when to_regrole('authenticated') is not null and to_regclass('eh_quad.eh_documents') is not null
    then has_table_privilege(to_regrole('authenticated'), to_regclass('eh_quad.eh_documents'), 'SELECT') end
    as authenticated_documents_select;

-- Inspect the canonical search RPC without changing its definition or privileges.
select
  p.proname as function_name,
  pg_catalog.pg_get_function_identity_arguments(p.oid) as identity_arguments,
  p.prosecdef as security_definer,
  p.proconfig as function_settings
from pg_catalog.pg_proc as p
join pg_catalog.pg_namespace as n on n.oid = p.pronamespace
where n.nspname = 'eh_quad'
  and p.proname = 'eh_search_pages';

-- List only policies attached to eh_quad relations.
select
  schemaname,
  tablename,
  policyname,
  roles,
  cmd
from pg_catalog.pg_policies
where schemaname = 'eh_quad'
order by tablename, policyname;
