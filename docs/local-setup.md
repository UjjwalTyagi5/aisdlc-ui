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

### If alembic says it cannot locate your revision

```
Can't locate revision identified by '0036_secret_ref_nullable'
```

Your database is stamped on the **pre-baseline lineage** and this is the one failure
that looks like a broken tool rather than a stale database.

`0001_baseline` deliberately replaced the 39 migrations that built the previous schema,
so this branch's revision ids (`0001_baseline` … `0022_notification_scope`) share none of
their names with the old ones (`0001_initial_schema` … `0036_secret_ref_nullable`). A
database migrated before the reset holds a `version_num` that no longer exists in the
repo, and alembic cannot build its revision map at all — `current` and `heads` die too,
not just `upgrade`. Nothing about the message says "old lineage".

**The fix is to rebuild**, using the DROP/CREATE lines above. Do not `alembic stamp` your
way across: the two lineages describe different schemas, and stamping tells alembic a
migration ran when it did not. The same mismatch has bitten once already, silently — see
`5090cc2`, where the database sat on the old lineage while the code expected the new
schema, and the only symptom was a model picker that stayed empty forever.

Check which lineage you are on before rebuilding, if you want to be sure:

```powershell
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -h localhost -U postgres -d sdlc_product `
    -c "SELECT version_num FROM alembic_version;"
```

A `version_num` matching a file in `backend/migrations/versions/` is fine; anything else
is the pre-baseline lineage.

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

> **Keep `watchfiles` installed.** It is pinned in `requirements.txt`/`pyproject.toml`
> and `--reload` depends on it. Without it uvicorn silently falls back to `StatReload`,
> which `os.stat()`s every `.py` file under `backend/` four times a second — including
> the ~15k files in `.venv`. One sweep takes ~4s on a laptop, so the watcher never
> sleeps and burns a full CPU core at idle, starving the event loop: every endpoint,
> even `/health`, then answers in >1s. With `watchfiles` the reloader uses OS-native
> file notifications and sits at 0%.

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

## 7. Email (optional)

Onboarding sends a single-use set-password link, and "Forgot password" sends the same
kind. **You do not need a mail server to work on this.** With `SMTP_HOST` unset the
backend logs the whole message — link included — at WARNING and carries on, so the flow
is fully exercisable: onboard somebody, then copy the link out of the backend log.

To send for real, set these on the **backend** (see `backend/config/env.py` for the full
commentary):

```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=<account@gmail.com>
SMTP_PASSWORD=<16-char app password>
SMTP_USE_TLS=true
EMAIL_FROM=<account@gmail.com>
PUBLIC_APP_URL=http://localhost:3000
```

`PUBLIC_APP_URL` is the **browser-facing** origin the emailed links point at — not
`AGENTIC_BASE_URL`, which is this API. A link built against the API host goes nowhere.

Gmail needs 2FA on the account plus an **App Password**; Google removed plain-password
SMTP auth. It is fine for development and a poor production choice for this traffic
specifically: the From address cannot be your own domain, there is no SPF/DKIM alignment
so a real spam-folder risk, and a set-password link in spam means the user cannot get in
at all. Any SMTP provider works — Azure Communication Services, SES, SendGrid, Mailgun —
so moving is configuration, not code.

---

## Troubleshooting

### Code Review agent: Semgrep scans always return zero findings, or crash with `FileNotFoundError`

Semgrep isn't a declared project dependency yet (see `docs/help/portfolio-1-agent-status.md`
for why). Install it into the backend venv with:

```powershell
cd backend
uv pip install semgrep==1.173.0
```

**Not** `uv add semgrep` — it resolves a broken Windows wheel (a `semgrep-core` binary
missing its `.exe` extension and required DLLs), which crashes any real scan with
`FileNotFoundError`. Also: Semgrep's `--config auto` silently skips any file not tracked
by git, and any path it default-ignores (patterns including `test`/`fixtures`) — a scan
against such a path returns `"findings": []`, not an error, which looks like "nothing
wrong" rather than "nothing scanned."

### Security agent: SCA/secret scans return "unavailable" or all-zero findings

Trivy (SCA) and Gitleaks (secrets) are external Go binaries, not pip-installable —
neither is a project dependency. Install both with `winget`:

```powershell
winget install --id Gitleaks.Gitleaks --silent --accept-package-agreements --accept-source-agreements
winget install --id AquaSecurity.Trivy --silent --accept-package-agreements --accept-source-agreements
```

**Open a new terminal after installing** — `winget` updates the persistent Windows User
`PATH`, but any shell already open (including one you install from) won't see the change
until it's restarted. (Semgrep's own gotchas are documented separately, above.)

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

You have no role bindings. Sign in as one of the seeded personas from `DEV_LOGINS.txt`.
There is no sign-up: accounts are created by an Organization Admin onboarding somebody,
and that person receives an emailed link to set their own password.

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
