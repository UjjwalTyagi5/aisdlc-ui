# Running the stack locally

Frontend (Next.js, port 3000) + FastAPI (port 8001) + PostgreSQL + Redis.

Verified end to end on 2026-08-17 by rebuilding the database from nothing.

Most of this is ordinary. **Step 3 is not**, and it is the reason a rebuilt database
fails to boot: there is no GRANT migration in the repo, so the app role gets no
privileges and startup dies on the first query. It has to be applied by hand every time
the database is recreated.

---

## Prerequisites

| | |
|---|---|
| PostgreSQL 16 | **native**, `localhost:5432`. Not the one in `docker-compose.yml` — that binds 5433. |
| Docker | for Redis only |
| Node + npm | frontend |
| `uv` | Python deps. Installs to `%USERPROFILE%\.local\bin` and is **not on PATH** by default. |

Roles: migrations run as `postgres`; the application connects as `sdlc_app`, which is
deliberately non-superuser and `NOBYPASSRLS` so `FORCE ROW LEVEL SECURITY` is a real
boundary against it. Both connection strings are in `backend/.env`.

---

## 1. Redis

```powershell
cd backend
docker compose up -d redis
```

**Name the service.** A bare `docker compose up -d` also starts a second PostgreSQL on
5433 and `litellm-proxy`, which fails without `LITELLM_API_KEY`.

## 2. Database

```powershell
# Only when rebuilding from scratch — this destroys everything.
psql -h localhost -p 5432 -U postgres -c "DROP DATABASE IF EXISTS sdlc_product;"
psql -h localhost -p 5432 -U postgres -c "CREATE DATABASE sdlc_product;"

cd backend
uv run alembic upgrade head
```

Skip the two `psql` lines to migrate an existing database in place.

## 3. Grants — the step that is not in the repo

Alembic runs as `postgres`, so every table is owned by `postgres` and `sdlc_app` holds
nothing. Without this the API dies at startup with
`InsufficientPrivilegeError: permission denied for table users`.

```sql
GRANT USAGE ON SCHEMA public TO sdlc_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO sdlc_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO sdlc_app;

-- Without these, the NEXT migration creates ungranted tables and the failure returns.
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO sdlc_app;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO sdlc_app;

-- NOT OPTIONAL, and it must come AFTER the blanket grant above.
REVOKE UPDATE, DELETE ON audit_events FROM sdlc_app;
REVOKE UPDATE, DELETE ON governance_request_events FROM sdlc_app;
```

Run it with:

```powershell
psql -h localhost -p 5432 -U postgres -d sdlc_product -f path\to\the\sql
```

### Why the two REVOKEs matter

`audit_events` is append-only **by privilege**, not by trigger — migration
`0005_audit_append_only` revokes UPDATE and DELETE from the app role precisely so that
even a SQL-injection foothold running as `sdlc_app` cannot rewrite history it is not
granted to rewrite. `GRANT … ON ALL TABLES` hands those rights straight back.

Nothing fails when you forget. The audit trail simply stops being evidence. Verify
rather than assume:

```sql
SELECT tablename,
       has_table_privilege('sdlc_app','public.'||tablename,'UPDATE') AS upd,
       has_table_privilege('sdlc_app','public.'||tablename,'DELETE') AS del
FROM pg_tables
WHERE schemaname='public'
  AND tablename IN ('audit_events','governance_request_events');
-- both columns must be false

SELECT tablename FROM pg_tables
WHERE schemaname='public'
  AND NOT has_table_privilege('sdlc_app','public.'||tablename,'SELECT');
-- must return zero rows
```

## 4. Backend

```powershell
cd backend
uv run uvicorn process_api:app --reload --port 8001
```

The first boot creates the single organization. Boot is also where two guards run — see
[Troubleshooting](#troubleshooting) if it refuses to start; both failures are the guards
doing their job, not bugs.

## 5. Seed the dev data

Once the backend has booted at least once (that is what creates the organization the
seeder needs):

```powershell
cd backend
uv run python -m scripts.seed_dev_personas
```

Creates two business units (**Payments**, **Lending**), two projects, and 14 accounts —
one per platform role. Credentials are written to `DEV_LOGINS.txt` at the repo root
(gitignored); the shared password is `devpassword123`. Idempotent, so re-run it freely.

**This step is required, not optional.** Boot no longer seeds a "Default Business Unit":
every project must belong to a unit somebody chose, so a fresh organization has zero
units and project creation fails until one exists. The seeder is the fastest way to get
past that; creating a unit on the Business Units screen also works.

The script refuses to run against anything that looks like a real deployment — these
accounts share one published password.

## 6. Frontend

```powershell
cd frontend
npm install
npm run dev
```

`frontend/.env.local` already points at the real backend (`NEXT_PUBLIC_API_MOCKS=off`,
`NEXT_PUBLIC_AUTH_MODE=local`, `FASTAPI_INTERNAL_URL=http://localhost:8001`). It is
gitignored and overrides the tracked `.env`, which stays in mock mode — **delete
`.env.local` to fall back to fixtures with no other edit.**

> `package.json` declares `pnpm@9.15.2`, but pnpm is not installed here and both
> `pnpm-lock.yaml` and `package-lock.json` exist. npm works. To match the declared
> manager: `corepack enable pnpm`, then `pnpm install` / `pnpm dev`.

---

## Troubleshooting

### `InsufficientPrivilegeError: permission denied for table users`

Step 3 was skipped, or a migration created tables after it ran without
`ALTER DEFAULT PRIVILEGES` being in place. Re-run step 3.

### `RbacCatalogDriftError: … Refusing to start`

Working as designed. `roles` / `permissions` / `role_permissions` are **code-owned** and
carry no `tenant_id` and no RLS, so a direct INSERT there would escalate every holder of
that role in every tenant. `assert_rbac_catalog` compares the database to
`shared/authz/permissions.py` before anything else and refuses to boot on any difference.

Causes, in likelihood order:

1. **Migrations are behind.** Run `uv run alembic upgrade head`. A change to
   `_ROLE_PERMISSIONS` ships with a data migration for exactly this reason — see
   `0018_grant_phase_approvals`.
2. **Someone edited `role_permissions` by hand.** Don't; the seeder deletes any edge the
   code does not declare. To change what a built-in role grants, use the Roles page
   (which writes the org-owned `role_permission_overrides`) or change the code matrix.

`RBAC_CATALOG_AUTOREPAIR=true` reconciles instead of failing. It is the break-glass
lever, not the default — it disables tamper detection for that boot.

### `alembic` fails with `KeyError` on a revision id

The migration graph cannot load, so **every** alembic command fails, including `current`
and `heads`. This branch's lineage is `0001`–`0018`; `main` has a different one reaching
`0034`+. Merging `main` can drag in migrations whose parent does not exist here.

Check `uv run alembic heads` after any merge that touches `backend/migrations/`. A broken
graph is silent until something reads the affected tables — the last occurrence surfaced
as a model picker that said "Connect a model provider" forever, because the code expected
a schema the migrations could not deliver.

### `No organization with slug 'pwc'. Start the backend once…`

Exactly what it says: `seed_dev_personas` needs the organization, and boot creates it.
Start the backend, then re-run the seeder.

### Login works, but every scoped page is empty

You have no role bindings. Sign in as one of the seeded personas from `DEV_LOGINS.txt`
rather than an account created through sign-up — self-serve registration deliberately
grants nothing.

### Model picker says "Connect a model provider"

Legitimate when no provider is onboarded. If one *is* onboarded, check
`uv run alembic current` is at head first.

---

## Full rebuild, in order

```powershell
cd backend
docker compose up -d redis
psql -h localhost -p 5432 -U postgres -c "DROP DATABASE IF EXISTS sdlc_product;"
psql -h localhost -p 5432 -U postgres -c "CREATE DATABASE sdlc_product;"
uv run alembic upgrade head
# apply the grants from step 3
uv run uvicorn process_api:app --reload --port 8001   # leave running; creates the org
# in a second terminal:
cd backend; uv run python -m scripts.seed_dev_personas
cd ..\frontend; npm run dev
```

---

See also: `docs/rbac-tables.md` (which RBAC tables are code-owned vs org-owned, and how
to add a permission), `docs/rbac-auth-implementation.md` (what the RBAC and auth work
actually implemented), `docs/rbac-auth-design.md`.
