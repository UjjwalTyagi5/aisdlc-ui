-- Privileges for the application role. Run after EVERY `alembic upgrade` that creates
-- tables, and after any rebuild of the database.
--
-- WHY THIS IS NOT A MIGRATION: alembic connects as `postgres`, so everything it creates
-- is owned by `postgres` and `sdlc_app` gets nothing. Granting from inside a migration
-- would work, but the grants have to be re-asserted whenever the role set or the schema
-- changes, and a migration only ever runs once. This file is idempotent and safe to
-- re-run at any time, which a migration is not.
--
-- `sdlc_app` deliberately stays non-superuser and NOBYPASSRLS: FORCE ROW LEVEL SECURITY
-- is only a real tenant boundary against a role that cannot step around it.

GRANT USAGE ON SCHEMA public TO sdlc_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO sdlc_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO sdlc_app;

-- Without these two, the NEXT migration creates tables the app role has no rights on
-- and the "permission denied for table …" failure comes back looking brand new.
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO sdlc_app;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO sdlc_app;

-- ── APPEND-ONLY TABLES ──────────────────────────────────────────────────────────
-- These MUST come after the blanket grant above, and must not be removed.
--
-- `audit_events` is append-only BY PRIVILEGE, not by trigger: migration 0005 revokes
-- UPDATE and DELETE from the app role precisely so that even a SQL-injection foothold
-- running as `sdlc_app` cannot rewrite history it is not granted to rewrite. The
-- `GRANT … ON ALL TABLES` above hands those rights straight back.
--
-- Nothing fails when this is skipped. The audit trail simply stops being evidence.
REVOKE UPDATE, DELETE ON audit_events FROM sdlc_app;
REVOKE UPDATE, DELETE ON governance_request_events FROM sdlc_app;
