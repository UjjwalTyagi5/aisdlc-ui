# Everything this platform depends on

The inventory a deployer needs: which services must exist, which are optional and what is lost
without them, which software and versions, which command-line tools the agents invoke, and
what the machine must be able to reach on the network.

Two files are authoritative and this document only summarises them: `backend/pyproject.toml`
(every Python dependency, pinned, with `uv.lock`) and `frontend/package.json`.

---

## 1. Services at runtime

| Service | Required? | Used for | Without it |
|---|---|---|---|
| **PostgreSQL** ≥ 15 | **Yes** | every durable record: organisations, business units, projects, runs, document metadata, roles and permissions, audit trail, encrypted connector credentials | the platform does not start |
| **Redis** 7 | **Yes** | sessions and token denylist, per-model rate limits, monthly spend counters, background queues, webhook de-duplication | the platform does not start |
| **Document store** | **Yes** (one of two) | the bytes of every generated document. Either a **directory on the VM** (`ARTIFACT_STORAGE_ROOT`) or **Azure Blob Storage** (`AZURE_BLOB_ACCOUNT_URL`); `STORAGE_BACKEND` chooses | documents cannot be produced or opened |
| **A model provider** | **Yes, in practice** | the agents' intelligence. Anthropic, OpenAI, Google, xAI and others, reached through LiteLLM. Keys are entered in the app per organisation and stored encrypted, not in config | agents answer nothing; the rest of the app works |
| **Langfuse** | No | traces of every model call, token counts, and the **cost figures the Cost page shows** | the platform runs; the Cost and Traces pages are empty. Self-host it with the `langfuse-deploy` kit (a sibling folder, delivered with this repository) — it brings its own ClickHouse, Redis and S3-compatible store |
| **SMTP** | No | invitation and password-reset emails | invitations must be handed over another way |
| **Connectors** | No | Azure DevOps, Azure Repos/Pipelines, GitHub Issues/Actions, Jira, Confluence, SharePoint (Microsoft Graph), Microsoft Teams, Slack, SonarQube, Figma. Configured per project by its own admin; credentials are personal or per-project, stored encrypted | those integrations are simply unavailable |
| **Azure Key Vault** | No | an alternative place to read secrets from | configuration comes from the environment file only |
| **Diagram rendering** | No, but visible | the Design agent renders diagrams through `mermaid.ink`, falling back to `kroki.io` | design documents are produced without their diagrams |

### What data lives where

- **PostgreSQL** — everything structured, including the audit trail, which is append-only by
  database privilege rather than by application code.
- **The document store** — one file per document, under
  `tenant/unit/project/stage/run/document/…`; documents awaiting approval live under a
  `_pending` prefix and are moved when approved.
- **Redis** — nothing that cannot be rebuilt.
- **Langfuse** — traces and costs, in its own PostgreSQL database and ClickHouse.

---

## 2. Software to install

| | Version | Why that version |
|---|---|---|
| **Python** | **3.12** (`>=3.12,<3.13`) | declared in `backend/pyproject.toml`; 3.13 is not supported by the pinned dependency set |
| **uv** | current | installs the backend from `pyproject.toml` + `uv.lock`. `pip` cannot: the lock uses an override that pip does not honour |
| **Node.js** | **≥ 20.11** for the frontend | `frontend/package.json` engines. *Langfuse, if you self-host it, needs Node 24 — see its own README* |
| **npm** | bundled with Node | the frontend is installed with `npm ci`. The repository's pnpm lockfile is stale; do not use it |
| **PostgreSQL** | 15 or 16 | the development compose file uses 15; nothing in the schema requires 16 |
| **Redis** | 7 | the client library is 7.x and queue behaviour assumes it |
| **nginx** | any current | TLS termination and a single public entry point |

### The Python dependency set, by purpose

165 pinned packages. The ones that explain the system:

| Purpose | Packages |
|---|---|
| Web service | `fastapi` 0.116, `uvicorn` 0.35, `pydantic` 2.11, `python-multipart` |
| Database | `sqlalchemy`, `asyncpg`, `alembic`, `psycopg[binary]` (the agent checkpointer), `psycopg-pool` |
| Agents | `langgraph` 0.6 + its Postgres checkpointer, `langchain` 0.3 and its Anthropic / OpenAI / Google / LiteLLM bindings, `langchain-mcp-adapters` |
| Model access | `litellm` 1.80 — one interface in front of every provider |
| Observability | `langfuse` 3.x |
| Documents | `python-docx` (Word), `python-pptx` (PowerPoint), `openpyxl` (Excel), `reportlab` (PDF), `weasyprint` (the QA report's PDF) |
| Azure | `azure-storage-blob`, `azure-identity`, `azure-keyvault-secrets` |
| Security scanning | `semgrep` — **installed on Linux only**; the Windows wheel is broken, so Windows developers install it by hand |
| Auth | `pyjwt`, `passlib` |

### The frontend, by purpose

Next.js 15.1 with React 19 and TypeScript 5.7; Tailwind CSS 4 for styling; TanStack Query 5
for server state; Zustand for client state; Zod for validating everything crossing the wire;
Radix UI primitives for dialogs and menus.

### Linux system packages

The PDF renderer links C libraries (Pango, GLib), and fonts must exist or PDFs render boxes
instead of text. The verified `apt` list is in `docs/deploy-linux-vm.md` §1. Confirm it with:

```bash
cd backend && uv run python scripts/check_document_generation.py    # expect 5/5
```

---

## 3. Command-line tools the agents invoke

The Development, Testing, Code Review and Deployment agents work inside a copy of the
customer's repository and shell out to ordinary tools. Install the ones your demos need; a
missing tool surfaces as an agent reporting a failed command, not as a crash.

`git` · `node` · `npm` · `npx` · `pip` · `pytest` · `semgrep` (the security scan) · `docker`
(only for flows that build containers)

---

## 4. Network access the VM needs

| Destination | Port | Why | Optional? |
|---|---|---|---|
| Model providers (`api.anthropic.com`, `api.openai.com`, Google, xAI, …) | 443 | every agent answer | no, in practice |
| `mermaid.ink`, `kroki.io` | 443 | Design agent diagrams — also from the **browser**, since the image URLs are rendered in the page | diagrams only |
| The customer's tools: Azure DevOps, GitHub, Jira, Confluence, Microsoft Graph, Slack, SonarQube | 443 | connectors that are configured | per connector |
| SMTP relay | 587 / 465 | invitations, password resets | email only |
| Azure Blob / Key Vault | 443 | only if you choose Azure storage or Key Vault | no, with local storage |
| Package registries (PyPI, npm) | 443 | **installation and build only**, not at run time | — |

A completely air-gapped VM can run the platform, but without model providers the agents
cannot answer.

---

## 5. Ports

| Port | Process | Exposure |
|---|---|---|
| 443 / 80 | nginx | public |
| 3000 | frontend (Node) | localhost only, behind nginx |
| 8004 | backend (uvicorn) | localhost only, behind nginx |
| 5432 | PostgreSQL | localhost |
| 6379 | Redis | localhost |
| 3100 / 3030 | Langfuse web / worker | localhost (optional) |
| 8123 / 9000 | ClickHouse (Langfuse) | localhost (optional) |
| 9090 | S3-compatible store (Langfuse) | localhost (optional) |
| 6380 | Langfuse's own Redis | localhost (optional) |

---

## 6. Configuration and secrets

`backend/.env` holds 44 operator-set keys; `backend/.env.example` documents every one, and
`scripts/check_env_example.py` fails if a required key is ever added without documentation.
The grouped table for deployment is `docs/deploy-linux-vm.md` §3. The frontend needs four
keys (`frontend/.env.example`).

**The secrets, and what each protects:**

| Key | Protects |
|---|---|
| `JWT_SECRET_KEY` | sign-in tokens. **Must be byte-identical in the backend and the frontend**, or every sign-in fails |
| `SECRET_STORE_KEY` | encrypts connector credentials and model provider keys stored in the database. Lose it and those must be re-entered |
| `ORG_ADMIN_PASSWORD` | the first administrator account created at first boot |
| `SMTP_PASSWORD` | the mail relay |
| `LANGFUSE_SALT`, `LANGFUSE_SECRET_KEY` | Langfuse API keys. The salt **must equal** the Langfuse server's own |
| Provider API keys | entered in the app, encrypted with `SECRET_STORE_KEY` — never in configuration files |

`backend/.env` is gitignored; keep it at `chmod 600` and back it up with the database.

---

## 7. What differs on Linux

Short list, in full in `docs/deploy-linux-vm.md` §8:

- **Word-to-PDF conversion** uses Word itself on Windows. On Linux the platform falls back to
  its own renderer: same content, not the same layout. Installing `libreoffice-writer` and
  converting with it is a small code change, not a setting.
- **Evidence export** requires Azure Blob Storage, because it hands out signed links. On local
  storage it answers 503 with that reason.
- **`semgrep`** installs normally on Linux (and is skipped on Windows).
