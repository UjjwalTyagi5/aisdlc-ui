# What this platform is, and what it is made of

## In one paragraph

An AI-assisted software delivery platform. A person signs in, opens a project, and works with
a series of **agents** — Requirements, Design, Project Manager, Development, Code Review,
Security, Testing, Deployment and Documentation, plus Discovery and Requirements
Modernization for work that starts from an existing system — each of which holds a
conversation, calls a
large language model, reads and writes the project's documents, and can reach out to the
tools a delivery team already uses (Azure DevOps, GitHub, Jira, Confluence, SharePoint).
Documents an agent produces are filed, reviewed and approved inside the platform, and every
model call is recorded so its cost can be reported.

## The pieces

| Piece | What it is | Where it lives |
|---|---|---|
| **Frontend** | The web application people use | `frontend/`, Next.js 15 + React 19 (`package.json`) |
| **Backend** | One HTTP + WebSocket service that hosts every agent | `backend/process_api.py`, FastAPI (`pyproject.toml`) |
| **Database** | All durable state: organisations, projects, runs, documents' metadata, roles, audit trail | PostgreSQL, schema managed by Alembic (`backend/migrations/`) |
| **Cache and queues** | Sessions, rate limits, spend counters, background work | Redis |
| **Document store** | The actual bytes of every generated document | a directory on disk, or Azure Blob Storage (`backend/shared/storage/`) |
| **Observability** | Every model call, its tokens and its cost | Langfuse — optional, self-hosted (`langfuse-deploy/`) |
| **Model providers** | The LLMs themselves | Anthropic, OpenAI, Google, xAI… reached through LiteLLM |
| **Connectors** | The customer's own tools | Azure DevOps, GitHub, Jira, Confluence, SharePoint, Slack, SonarQube, Figma (`backend/config/connectors/`) |

Only the first four are required. The rest can be switched off and the platform still runs,
with the corresponding feature unavailable — see `docs/dependencies.md`.

## How the parts talk

```
                 browser
                    │  HTTPS
              ┌─────▼─────┐
              │ Frontend  │  Next.js — also the only thing the browser talks to.
              │  :3000    │  It forwards API calls to the backend server-side,
              └─────┬─────┘  so a browser never holds a backend token.
                    │  HTTP + WebSocket (127.0.0.1:8004)
              ┌─────▼─────┐        ┌──────────────┐
              │  Backend  ├────────► PostgreSQL   │  projects, runs, documents,
              │  :8004    │        └──────────────┘  roles, audit trail
              │ (FastAPI, │        ┌──────────────┐
              │  agents)  ├────────► Redis        │  sessions, queues, spend counters
              │           │        └──────────────┘
              │           │        ┌──────────────┐
              │           ├────────► Document     │  a local directory, or Azure Blob
              │           │        │ store        │
              │           │        └──────────────┘
              │           │        ┌──────────────┐
              │           ├────────► Langfuse     │  traces + cost   (optional)
              │           │        └──────────────┘
              │           │  outbound HTTPS
              │           ├───────► model providers (Anthropic / OpenAI / Google / xAI)
              │           ├───────► the customer's tools (Azure DevOps, Jira, …)
              └───────────┘───────► diagram rendering (mermaid.ink, kroki.io)
```

## What happens during an agent run

1. The person sends a message on an agent's page. The frontend passes it to the backend over
   a WebSocket.
2. The backend checks who they are and whether their role may use that agent on that project.
3. It resolves **which model** this run uses — each organisation brings its own provider keys,
   stored encrypted in the database.
4. The agent runs as a small graph of steps (LangGraph): the model decides, tools execute, the
   result feeds back. Tools include reading the project's approved documents, writing a new
   one, running a scanner, cloning a repository, calling Jira.
5. Documents it produces are written to the document store and recorded in the database as
   drafts, awaiting approval.
6. The model call, its tokens and its cost are sent to Langfuse, if enabled.

## The shape of a deployment

One Linux VM is enough. On it:

- the **backend**, as a systemd service, **with exactly one worker** — an agent's conversation
  lives in that process's memory, so a second worker would answer half the messages without it
  (`docs/deploy-linux-vm.md` §7);
- the **frontend**, built once and served by Node;
- **PostgreSQL** and **Redis**, from the distribution's packages;
- **nginx** in front, holding the TLS certificate;
- optionally **Langfuse** and its own services, installed by a separate kit.

Nothing requires Docker. The procedure is `docs/deploy-linux-vm.md`; the inventory of
everything it needs is `docs/dependencies.md`.
