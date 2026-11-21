#!/usr/bin/env bash
# Docker entrypoint init hook. This file is sourced by the official Postgres image.
set -eu
: "${EH_SEARCH_DB_PASSWORD:?Set EH_SEARCH_DB_PASSWORD in database/.env}"

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=ON_ERROR_STOP=1 --set=search_password="$EH_SEARCH_DB_PASSWORD" <<'SQL'
ALTER ROLE eh_search_api
  WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT
  PASSWORD :'search_password';

GRANT CONNECT ON DATABASE everythinghub TO eh_search_api;
GRANT USAGE ON SCHEMA eh_quad TO eh_search_api;
GRANT SELECT ON eh_quad.eh_documents,
                eh_quad.eh_catalogs,
                eh_quad.eh_catalog_records
  TO eh_search_api;
GRANT EXECUTE ON FUNCTION eh_quad.eh_search_pages(text, text, integer)
  TO eh_search_api;
GRANT EXECUTE ON FUNCTION eh_quad.eh_search_catalog_records(text, text, text, integer)
  TO eh_search_api;

CREATE POLICY eh_search_api_read_documents
  ON eh_quad.eh_documents FOR SELECT TO eh_search_api
  USING (robots_allowed IS TRUE);

CREATE POLICY eh_search_api_read_catalog_records
  ON eh_quad.eh_catalog_records FOR SELECT TO eh_search_api
  USING (attributes ->> 'public_search_allowed' = 'true');

CREATE POLICY eh_search_api_read_catalogs
  ON eh_quad.eh_catalogs FOR SELECT TO eh_search_api
  USING (
    EXISTS (
      SELECT 1 FROM eh_quad.eh_catalog_records AS r
      WHERE r.catalog_id = eh_catalogs.catalog_id
        AND r.attributes ->> 'public_search_allowed' = 'true'
    )
  );
SQL
