-- Local self-hosted PostgreSQL only. Supabase defines this role for its API;
-- create a non-login equivalent so the same eh_quad migrations can run locally.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
    CREATE ROLE service_role NOLOGIN;
  END IF;
END
$$;
