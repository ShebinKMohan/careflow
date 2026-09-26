-- CareFlow databases, roles and extensions. Runs once, on an empty data volume.
-- Passwords come from the container environment (filled from the gitignored .env) via \getenv.
\set ON_ERROR_STOP on
\getenv migrator_password CAREFLOW_DB_MIGRATOR_PASSWORD
\getenv app_password CAREFLOW_DB_APP_PASSWORD
\getenv hapi_password HAPI_DB_PASSWORD

-- careflow_migrator owns the schema (Alembic, M0-07). careflow_app is the API's runtime role:
-- it can never bypass row-level security.
CREATE ROLE careflow_migrator LOGIN NOSUPERUSER NOCREATEROLE NOCREATEDB PASSWORD :'migrator_password';
CREATE ROLE careflow_app LOGIN NOSUPERUSER NOCREATEROLE NOCREATEDB NOBYPASSRLS PASSWORD :'app_password';
CREATE ROLE hapi LOGIN NOSUPERUSER NOCREATEROLE NOCREATEDB PASSWORD :'hapi_password';

CREATE DATABASE careflow OWNER careflow_migrator;
CREATE DATABASE hapi OWNER hapi;

\connect careflow
-- Extensions need superuser to create; the migrator owns everything else.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_textsearch;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO careflow_migrator;
GRANT USAGE ON SCHEMA public TO careflow_app;
GRANT CONNECT ON DATABASE careflow TO careflow_app;
