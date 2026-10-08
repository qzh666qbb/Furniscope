#!/bin/sh
set -eu

: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_APP_PASSWORD:?POSTGRES_APP_PASSWORD is required}"
: "${POSTGRES_ADMIN_PASSWORD:?POSTGRES_ADMIN_PASSWORD is required}"
: "${POSTGRES_WORKER_PASSWORD:?POSTGRES_WORKER_PASSWORD is required}"
: "${POSTGRES_LANGGRAPH_PASSWORD:?POSTGRES_LANGGRAPH_PASSWORD is required}"
: "${POSTGRES_BACKUP_PASSWORD:?POSTGRES_BACKUP_PASSWORD is required}"
: "${POSTGRES_MONITOR_PASSWORD:?POSTGRES_MONITOR_PASSWORD is required}"
: "${POSTGRES_REPLICATION_PASSWORD:?POSTGRES_REPLICATION_PASSWORD is required}"

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=app_password="$POSTGRES_APP_PASSWORD" \
  --set=admin_password="$POSTGRES_ADMIN_PASSWORD" \
  --set=worker_password="$POSTGRES_WORKER_PASSWORD" \
  --set=langgraph_password="$POSTGRES_LANGGRAPH_PASSWORD" \
  --set=backup_password="$POSTGRES_BACKUP_PASSWORD" \
  --set=monitor_password="$POSTGRES_MONITOR_PASSWORD" \
  --set=replication_password="$POSTGRES_REPLICATION_PASSWORD" <<'SQL'
DO $$
DECLARE role_name text;
BEGIN
  FOREACH role_name IN ARRAY ARRAY[
    'furniscope_app_login',
    'furniscope_admin_login',
    'furniscope_worker_login',
    'furniscope_langgraph_login',
    'furniscope_backup_login',
    'furniscope_monitor_login'
  ] LOOP
    IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname=role_name) THEN
      EXECUTE format(
        'CREATE ROLE %I LOGIN INHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION',
        role_name
      );
    END IF;
    IF EXISTS(
      SELECT FROM pg_roles
       WHERE rolname=role_name AND (rolsuper OR rolbypassrls OR NOT rolcanlogin)
    ) THEN
      RAISE EXCEPTION 'unsafe deployment login role: %',role_name;
    END IF;
  END LOOP;
END $$;

DO $$
BEGIN
  IF NOT EXISTS(
    SELECT FROM pg_roles WHERE rolname='furniscope_replication_login'
  ) THEN
    CREATE ROLE furniscope_replication_login
      LOGIN REPLICATION NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
  END IF;
  IF EXISTS(
    SELECT FROM pg_roles
     WHERE rolname='furniscope_replication_login'
       AND (rolsuper OR rolbypassrls OR NOT rolcanlogin OR NOT rolreplication)
  ) THEN
    RAISE EXCEPTION 'unsafe deployment replication role';
  END IF;
END $$;

SELECT format('ALTER ROLE furniscope_app_login PASSWORD %L', :'app_password') \gexec
SELECT format('ALTER ROLE furniscope_admin_login PASSWORD %L', :'admin_password') \gexec
SELECT format('ALTER ROLE furniscope_worker_login PASSWORD %L', :'worker_password') \gexec
SELECT format('ALTER ROLE furniscope_langgraph_login PASSWORD %L', :'langgraph_password') \gexec
SELECT format('ALTER ROLE furniscope_backup_login PASSWORD %L', :'backup_password') \gexec
SELECT format('ALTER ROLE furniscope_monitor_login PASSWORD %L', :'monitor_password') \gexec
SELECT format('ALTER ROLE furniscope_replication_login PASSWORD %L', :'replication_password') \gexec

GRANT furniscope_runtime,furniscope_tenant TO furniscope_app_login;
GRANT furniscope_platform_admin TO furniscope_admin_login;
GRANT furniscope_scheduler,furniscope_tenant TO furniscope_worker_login;
GRANT furniscope_backup TO furniscope_backup_login;
GRANT furniscope_monitor TO furniscope_monitor_login;

CREATE SCHEMA IF NOT EXISTS furniscope_langgraph AUTHORIZATION furniscope_langgraph_login;
ALTER SCHEMA furniscope_langgraph OWNER TO furniscope_langgraph_login;
GRANT USAGE,CREATE ON SCHEMA furniscope_langgraph TO furniscope_langgraph_login;
ALTER ROLE furniscope_langgraph_login IN DATABASE :"DBNAME"
  SET search_path TO furniscope_langgraph,public;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL
