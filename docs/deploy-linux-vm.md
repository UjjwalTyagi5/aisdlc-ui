# Deploying on a Linux VM

Everything a fresh Ubuntu VM needs to run this platform: system packages, Python and Node
dependencies, configuration, the database steps, and the smoke tests that prove it worked.

Verified on 2026-09-22 by installing the Python dependency set in a clean
`python:3.12` Linux container from this repository's `pyproject.toml` + `uv.lock`.

| Part | Runs as | Port |
|---|---|---|
| Backend | `uvicorn process_api:app`, **one worker** | 8004 |
| Frontend | Next.js 15 standalone (`node server.js`) | 3000 |
| PostgreSQL | 16 | 5432 |
| Redis | 7 | 6379 |

> **One worker, not several.** Every agent graph keeps its conversation in memory
> (`MemorySaver`), and the standalone agent APIs hold per-session state in module globals. A
> second worker process would answer half the turns of a chat with no memory of the other
> half. Scale by putting more VMs behind a load balancer with sticky sessions, not by adding
> workers to one.

---

## 1. System packages

```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  git curl ca-certificates \
  libglib2.0-0 libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libfribidi0 \
  libgdk-pixbuf-2.0-0 shared-mime-info fonts-dejavu-core
```

Why each group:

- **git** — the Development, Code Review, Security, Testing and Deployment agents clone and
  push the repositories they work on. Without it those agents fail at the first step.
- **The `libpango`/`libglib` set** — WeasyPrint renders the Testing agent's QA report to PDF
  and is a Python package that links these C libraries at import time. Missing, the import
  raises `cannot load library 'libgobject-2.0-0'` and QA reports stay HTML-only. This exact
  list was verified by installing it in a clean container and rendering a PDF.
- **fonts-dejavu-core** — a VM has no fonts by default; without one, generated PDFs render
  boxes instead of text.

### The document formats, and what each one needs

Every format this platform produces was generated and read back on Linux with the packages
above (`scripts/check_document_generation.py`, §9):

| Format | Library | Needs from the system |
|---|---|---|
| Word `.docx` — every agent's documents | python-docx (`shared/docs/`) | nothing |
| PowerPoint `.pptx` — Design and Requirements decks | python-pptx | nothing |
| Excel `.xlsx` — estimates, test suites, run reports | openpyxl | nothing |
| PDF — the fallback renderer | reportlab | fonts only |
| PDF — the Testing agent's QA report | **WeasyPrint** | **the `libpango`/`libglib` set above** |
| PDF — the Design agent's designed document | docx2pdf + Word | **Windows/macOS only** — falls back to reportlab (§8) |

Worth knowing: WeasyPrint does **not** import on the Windows development machine (the same
GTK libraries are missing there), so the QA report is HTML-only in development and gains its
PDF on this Linux VM.

**Node 20.11+** (the frontend requires it):

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs
```

**PostgreSQL 16 and Redis 7** — install locally, or use managed services and point the
connection strings at them:

```bash
sudo apt-get install -y postgresql-16 redis-server
```

### Optional, depending on what you need

| Need | Install |
|---|---|
| Design PDFs that look exactly like the Word file | `sudo apt-get install -y libreoffice-writer` — see §8 |
| Agents that build or run containers | `sudo apt-get install -y docker.io` and add the service user to the `docker` group |
| Node-based projects the agents generate | already covered by Node above (they run `npm`/`npx`) |

---

## 2. Python dependencies — `pyproject.toml` is the only file

The repository declares its Python dependencies in **`backend/pyproject.toml`**, pinned, with
**`backend/uv.lock`** holding the exact resolved set. There is no `requirements.txt`; it was
a second hand-maintained copy that had drifted (different versions in each file), so it was
removed.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh      # installs to ~/.local/bin
export PATH="$HOME/.local/bin:$PATH"

cd backend
uv sync --frozen --no-dev
```

- `--frozen` installs exactly what `uv.lock` says and fails if the lock and `pyproject.toml`
  disagree — no surprise upgrade on the server.
- `--no-dev` leaves out the test-only packages (`pytest`, `pytest-asyncio`, `respx`).
- It creates `backend/.venv`. Run everything through `uv run …`, or call
  `backend/.venv/bin/python` directly.

**The backend directory must be writable by the service user.** The application writes into
its own tree, and a read-only deployment fails at import:

| Path | What |
|---|---|
| `backend/pipeline.log` | opened when the deployment agent's module is imported |
| `backend/files/` | agent workspaces: repositories cloned, generated projects, uploads |
| `backend/files/artifact-store` | generated documents, when storage is the local directory (§5) |

```bash
sudo chown -R sdlc:sdlc /opt/sdlc/backend
```

The backend folder can be deployed on its own, without the rest of the repository.

> **Why not `pip install -r requirements.txt`?** Because pip cannot resolve this project:
> `google-generativeai==0.8.6` pins `google-ai-generativelanguage==0.6.15` while
> `langchain-google-genai` requires `>=0.6.16`. `pyproject.toml` settles it with uv's
> `[tool.uv] override-dependencies`, which pip does not implement — pip stops with
> `ResolutionImpossible`. Install with `uv`.

`pywin32` is Windows-only and appears in the lock with a `sys_platform == 'win32'` marker, so
Linux installs skip it. Nothing else in the set is platform-specific.

---

## 3. Configuration

Copy `backend/.env` from a working environment, or build it from
`backend/.env.example`, and set at least:

| Key | What it is |
|---|---|
| `ENV` | `dev` on a demo box, anything else for a deployed one. Also decides artifact storage — see §5 |
| `POSTGRES_CONN_STRING` | app connection, `postgresql+asyncpg://sdlc_app:…@host:5432/sdlc_product` |
| `POSTGRES_SYNC_CONN_STRING` | same database, `postgresql://…` (the agent checkpointer) |
| `POSTGRES_MIGRATIONS_CONN_STRING` | as `postgres`, used by alembic only |
| `REDIS_URL` | `redis://localhost:6379/0` |
| `JWT_SECRET_KEY` | **secret.** Must be byte-identical to the frontend's, or every sign-in fails |
| `SECRET_STORE_KEY` | **secret.** Encrypts stored connector credentials |
| `AGENTIC_BASE_URL`, `AGENTIC_WS_URL` | the backend's public URLs; they build the links agents hand out |
| `CORS_ALLOWED_ORIGINS` | the frontend's public origin |
| `DEFAULT_ORG_SLUG`, `DEFAULT_ORG_NAME`, `ORG_ADMIN_EMAILS`, `ORG_ADMIN_PASSWORD` | first-boot organisation and its first admin. **`ORG_ADMIN_PASSWORD` is a secret** |
| `ARTIFACT_STORAGE_ROOT` / `AZURE_BLOB_ACCOUNT_URL` / `STORAGE_BACKEND` | where documents are stored — §5 |
| `SMTP_*`, `EMAIL_FROM` | invitations and password resets. **`SMTP_PASSWORD` is a secret** |
| `ENABLE_LANGFUSE`, `LANGFUSE_*` | model tracing; leave disabled if you have no Langfuse |
| `AGENT_RUNTIME_MODE` | `local` keeps agent state in memory (matches the one-worker rule) |
| `MCP_ENABLED`, `MCP_STDIO_*` | Model Context Protocol tools; leave off unless used |

Keep `.env` out of git (it already is) and readable only by the service user:
`chmod 600 backend/.env`.

**Frontend** — `frontend/.env.local`:

| Key | Value |
|---|---|
| `FASTAPI_INTERNAL_URL` | `http://127.0.0.1:8004` — how the frontend server reaches the backend |
| `JWT_SECRET_KEY` | **the same secret as the backend's** |
| `NEXT_PUBLIC_AUTH_MODE` | `real` |
| `NEXT_PUBLIC_API_MOCKS` | `disabled` |

`NEXT_PUBLIC_*` values are baked into the browser bundle **at build time**. Changing one means
rebuilding the frontend.

---

## 4. Database

```bash
sudo -u postgres createdb sdlc_product
cd backend
uv run alembic upgrade head          # creates the schema
uv run python -m scripts.grant_app_role   # REQUIRED — see below
uv run alembic current               # must equal `uv run alembic heads`
```

**Do not skip the grants.** Migrations run as `postgres`, so every table is owned by
`postgres` and the application's role (`sdlc_app`, deliberately non-superuser and
`NOBYPASSRLS` so row-level security is a real boundary) has no privileges. Without this step
the API dies at startup with `InsufficientPrivilegeError: permission denied for table users`.
Re-run it after **every** `alembic upgrade` that adds tables.

Optional demo data: `uv run python -m scripts.seed_dev_personas` (after the backend has
booted once, which is what creates the organisation).

---

## 5. Where documents are stored

Every generated document (BRD, design, QA report) is bytes in a store plus a row in
`artifacts`. Two backends, chosen in `shared/storage/__init__.py`:

- **A directory on the VM** — set `ARTIFACT_STORAGE_ROOT=/var/lib/sdlc/artifact-store`
  (absolute, on a disk with room and in your backup) and `STORAGE_BACKEND=local`. Create it
  and give the service user write access. Simplest, and the whole lifecycle works.
- **Azure Blob Storage** — set `AZURE_BLOB_ACCOUNT_URL` and `STORAGE_BACKEND=azure`. The VM
  authenticates with its managed identity or `az login`; the account needs
  "Storage Blob Data Contributor" for that identity.

`STORAGE_BACKEND` accepts `auto` (the default: local when `ENV=dev` and a root is set, else
Azure), `local` or `azure`. On a deployed host set it explicitly.

**Moving existing documents between the two is a separate step.** They share names, not
bytes, so switching without copying leaves every existing document unreadable:

```bash
uv run python scripts/mirror_artifact_storage.py --direction azure-to-local          # dry run
uv run python scripts/mirror_artifact_storage.py --direction azure-to-local --apply
```

Only evidence export differs between the backends: it mints Azure signed links, so it answers
503 on local storage and says so.

---

## 6. Frontend

```bash
cd frontend
npm ci                    # package-lock.json is the current lockfile
npm run build             # produces .next/standalone
```

Verified on 2026-09-22 in a clean `node:20-bookworm-slim` container: `npm ci` then
`next build` both succeed on Node 20.20.2 with no extra system packages.

Run it from the standalone output:

```bash
cp -r public .next/standalone/ && cp -r .next/static .next/standalone/.next/
cd .next/standalone && PORT=3000 HOSTNAME=127.0.0.1 node server.js
```

> `frontend/Dockerfile` installs with **pnpm** and a `pnpm-lock.yaml` last updated in August,
> while `package-lock.json` (npm) is current and is what development uses. Build with `npm ci`
> as above, or regenerate the pnpm lock before using that Dockerfile — a frozen pnpm install
> would build from stale dependencies.

---

## 7. Running as services

`/etc/systemd/system/sdlc-backend.service`:

```ini
[Unit]
Description=SDLC backend (FastAPI)
After=network.target postgresql.service redis-server.service

[Service]
Type=simple
User=sdlc
WorkingDirectory=/opt/sdlc/backend
# One worker: agent conversations live in this process's memory.
ExecStart=/opt/sdlc/backend/.venv/bin/uvicorn process_api:app \
  --host 127.0.0.1 --port 8004 --workers 1 --ws-max-size 1000000
Restart=on-failure
RestartSec=5
Environment=PATH=/opt/sdlc/backend/.venv/bin:/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/sdlc-frontend.service`:

```ini
[Unit]
Description=SDLC frontend (Next.js)
After=network.target sdlc-backend.service

[Service]
Type=simple
User=sdlc
WorkingDirectory=/opt/sdlc/frontend/.next/standalone
Environment=NODE_ENV=production PORT=3000 HOSTNAME=127.0.0.1
ExecStart=/usr/bin/node server.js
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now sdlc-backend sdlc-frontend
```

### nginx in front

Agents stream over WebSockets and a run can take minutes, so the proxy needs the upgrade
headers and generous timeouts:

```nginx
server {
    listen 443 ssl;
    server_name sdlc.example.com;

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
    }
}
```

Only the frontend needs to be public. Keep the backend on 127.0.0.1; the frontend server
calls it directly, and the browser never does.

---

## 8. What differs on Linux

- **Design PDFs.** The Design agent writes a styled Word document and converts it with Word
  through `docx2pdf`, which exists only on Windows/macOS. On Linux the app falls back to its
  own PDF renderer, so the PDF has the same content but not the Word layout. For matching
  output install `libreoffice-writer` and convert with
  `soffice --headless --convert-to pdf`; that is a small code change in
  `agents_orchestrator/design_architecture_agent/agents/architecture.py::_docx_to_pdf`, not a
  configuration switch.
- **Evidence export** needs Azure Blob Storage (it mints signed links) — 503 on local storage.
- **Long paths** are a Windows-only concern; nothing to configure here.
- **Agent tooling.** Development and Testing agents shell out to `git`, `node`, `npm`, `npx`,
  `pip` and `pytest` inside the workspaces they create, and some flows use `docker`. Install
  what your demos need; missing tools surface as an agent reporting a failed command.

---

## 9. Smoke tests

```bash
curl -s http://127.0.0.1:8004/health
```

Expect `"status":"ok"` with `postgres`, `redis` and `blob` all `ok`. `blob: ok` means the
store is writable — it is the check that catches a misconfigured `ARTIFACT_STORAGE_ROOT`.

```bash
cd backend
uv run python scripts/check_document_generation.py
```

Generates a Word document, a PowerPoint deck, an Excel workbook and both PDF renderers, and
reads each back. It needs no database, no network and no configuration, so it is the first
thing to run on a new host: it separates "a library is missing its system packages" from
every other kind of failure. Expect `5/5 formats generated and read back`.

```bash
uv run python scripts/verify_artifact_storage.py
```

17 checks against the configured store: the Documents list, preview, downloading an approved
document and one awaiting approval, the agents' `list_project_documents` and `read_document`,
a file-and-approve round trip (which moves the bytes out of the pending area), and the tenant
and project isolation rules. It creates one document and removes it.

Before the database exists, this one proves the install itself — it imports the whole
application, which is where a missing system library or a platform-specific package shows up:

```bash
cd backend
uv run python -c "import process_api; print(len(process_api.app.routes), 'routes')"
```

Then sign in through the frontend, open a project, and run one agent end to end.

---

## 10. Backups

- **PostgreSQL** — `pg_dump sdlc_product`. It holds every project, document row, approval and
  audit record.
- **The artifact store** — the directory at `ARTIFACT_STORAGE_ROOT`, or the blob container.
  The database rows reference it by path; one without the other is not a restore.
- **`backend/.env`** — the secrets. Losing `SECRET_STORE_KEY` makes stored connector
  credentials undecryptable.
