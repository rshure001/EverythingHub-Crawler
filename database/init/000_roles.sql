-- Roles for a self-hosted Everything Hub PostgreSQL instance.
-- Run only during first initialization of a NEW Everything Hub database.
DO $roles$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'service_role') THEN
    EXECUTE 'CREATE ROLE service_role NOLOGIN BYPASSRLS';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'eh_search_api') THEN
    EXECUTE 'CREATE ROLE eh_search_api NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT';
  END IF;
END
$roles$;
