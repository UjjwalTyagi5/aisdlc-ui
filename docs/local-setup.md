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
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -h localhost -p 5432 -U postgres `
    -c "DROP DATABASE IF EXISTS sdlc_product;"
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -h localhost -p 5432 -U postgres `
    -c "CREATE DATABASE sdlc_product;"

cd backend
uv run alembic upgrade head
```

Skip the two `psql` lines to migrate an existing database in place.

> `psql` is not on PATH after a default Windows install — hence the full path. Adjust
> `16` to your major version. Creating the database is the one step with no Python
> equivalent here; everything after it runs through `uv`.

Check it landed — a migration whose transaction rolls back still logs
`Running upgrade …` on the way in, so the log alone does not prove anything:

```powershell
uv run alembic current    # must print the same revision as `uv run alembic heads`
```

## 3. Grants for the app role

```powershell
cd backend
uv run python -m scripts.grant_app_role
```

**No psql needed** — it uses the virtualenv you already have for alembic. Idempotent, so
re-run it any time; you *must* re-run it after any `alembic upgrade` that creates tables.

Expected output:

```
applying grants to sdlc_product on localhost:5432 as postgres
  7 statement(s) applied
  every table is readable by sdlc_app
  audit_events is append-only (no UPDATE, no DELETE)
  governance_request_events is append-only (no UPDATE, no DELETE)

grants applied and verified
```

Skip this and the API dies at startup with
`InsufficientPrivilegeError: permission denied for table users`.

### Why it is a script and not a migration

Alembic connects as `postgres`, so everything it creates is owned by `postgres` and
`sdlc_app` gets nothing. A migration could grant, but grants must be re-asserted whenever
the schema changes and a migration only ever runs once. `scripts/grant_app_role.sql` is
the statements; `scripts/grant_app_role.py` applies them and then checks its own work.

`sdlc_app` deliberately stays non-superuser and `NOBYPASSRLS`: `FORCE ROW LEVEL SECURITY`
is only a real tenant boundary against a role that cannot step around it.

### Why the script verifies, and why the two REVOKEs matter

`audit_events` is append-only **by privilege**, not by trigger — migration
`0005_audit_append_only` revokes UPDATE and DELETE from the app role so that even a
SQL-injection foothold running as `sdlc_app` cannot rewrite history it is not granted to
rewrite. The `GRANT … ON ALL TABLES` above hands those rights straight back, which is why
the REVOKEs come last in the SQL.

**Nothing fails when they are missing.** The audit trail simply stops being evidence.
That is the whole reason the script asserts the result instead of trusting it, and it
exits non-zero if either table is writable.

### If you prefer psql

`psql` ships with PostgreSQL but is **not added to PATH** by the Windows installer, which
is why `psql : The term 'psql' is not recognized` is the usual first result. Call it by
full path, or add `C:\Program Files\PostgreSQL\16\bin` to PATH:

```powershell
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -h localhost -p 5432 -U postgres `
    -d sdlc_product -f scripts\grant_app_role.sql
```

That applies the same statements but does **not** verify them — prefer the Python script.

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

### `psql : The term 'psql' is not recognized`

The Windows PostgreSQL installer does not add `psql` to PATH. Nothing is broken.

Only two commands in this document need it — creating and dropping the database — and
both are in step 2 with the full path. Everything else runs through `uv`, including the
grants: `uv run python -m scripts.grant_app_role`. To have `psql` generally, add
`C:\Program Files\PostgreSQL\16\bin` to PATH (adjust the major version).

### `InsufficientPrivilegeError: permission denied for table users`

Step 3 was skipped, or a migration created tables after it ran without
`ALTER DEFAULT PRIVILEGES` being in place. Re-run `uv run python -m scripts.grant_app_role`
— it is idempotent and reports what it verified.

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
$psql = "C:\Program Files\PostgreSQL\16\bin\psql.exe"   # not on PATH by default

cd backend
docker compose up -d redis
& $psql -h localhost -p 5432 -U postgres -c "DROP DATABASE IF EXISTS sdlc_product;"
& $psql -h localhost -p 5432 -U postgres -c "CREATE DATABASE sdlc_product;"
uv run alembic upgrade head
uv run alembic current                      # must match `uv run alembic heads`
uv run python -m scripts.grant_app_role
uv run uvicorn process_api:app --reload --port 8001   # leave running; creates the org

# in a second terminal:
cd backend
uv run python -m scripts.seed_dev_personas
cd ..\frontend
npm install
npm run dev
```

---

See also: `docs/rbac-tables.md` (which RBAC tables are code-owned vs org-owned, and how
to add a permission), `docs/rbac-auth-implementation.md` (what the RBAC and auth work
actually implemented), `docs/rbac-auth-design.md`.
