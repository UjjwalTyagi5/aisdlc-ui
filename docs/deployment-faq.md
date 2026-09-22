# Questions a deployer asks, answered

Short answers, each pointing at the document that carries the detail.

### Does it need Docker?
No. Nothing in the platform requires it, and the Langfuse kit was written specifically to run
without it. Some *agent* flows shell out to `docker` when a demo builds a container — that is
the only use, and it is optional.

### What must be installed on the VM?
Python 3.12 with `uv`, Node 20.11+ with npm, PostgreSQL 15/16, Redis 7, nginx, and a short
list of system packages for the PDF renderer. Full inventory: `docs/dependencies.md` §2.

### Can it run on one machine?
Yes — that is the documented shape: backend, frontend, PostgreSQL, Redis and nginx on one VM.
`docs/deploy-linux-vm.md`.

### How much of it is mandatory?
PostgreSQL, Redis, somewhere to put documents, and at least one model provider. Everything
else — Langfuse, email, every connector, Azure — can be off, and the corresponding feature is
simply unavailable. Table: `docs/dependencies.md` §1.

### Why only one backend worker?
An agent's conversation state lives in the process's memory, so a second worker would answer
half of a person's messages without the context of the other half. `docs/deploy-linux-vm.md` §7.

### Where does customer data live, and what do we back up?
PostgreSQL (everything structured, including the audit trail), and the document store (one
file per document, on disk or in Azure Blob). Back up both, plus `backend/.env` — without
`SECRET_STORE_KEY` the stored connector and model keys cannot be decrypted.
`docs/deploy-linux-vm.md` §10, `docs/dependencies.md` §6.

### Does it need the internet?
For model providers, yes. Diagram rendering and any configured connector also make outbound
HTTPS calls. Nothing needs inbound access except nginx. Table: `docs/dependencies.md` §4.

### Which ports do we open?
Only 443 (and 80 to redirect). Everything else listens on localhost behind nginx.
`docs/dependencies.md` §5.

### Where do the model API keys go?
Into the application, per organisation, through its admin screens. They are encrypted with
`SECRET_STORE_KEY` and stored in the database — not in configuration files.

### How do we know the install is healthy?
Three checks, in order — they separate "a library is missing" from "a service is
misconfigured":
```bash
cd backend
uv run python scripts/check_document_generation.py   # 5/5 document formats
uv run python -c "import process_api; print(len(process_api.app.routes), 'routes')"
curl -s http://127.0.0.1:8004/health                 # postgres, redis and blob all ok
uv run python scripts/verify_artifact_storage.py     # 17/17 document lifecycle checks
```
`docs/deploy-linux-vm.md` §9.

### What does the platform cost to run, and how do we see it?
The model providers are the cost. The Cost page reports it per agent and per model, and it is
computed by Langfuse — so if Langfuse is not deployed, that page is empty. Deploying it is the
`langfuse-deploy` kit's README, a sibling folder delivered with this repository.

### Is Langfuse required?
No. It is how token usage and cost are reported, and it is self-hosted without Docker by the
`langfuse-deploy` kit, which also installs ClickHouse, a second Redis and an S3-compatible
store. Budget roughly 8 GB of RAM for the VM if you include it.

### Anything that behaves differently on Linux?
Three things: PDFs converted from Word lose Word's exact layout (content is identical),
evidence export needs Azure Blob Storage, and the security scanner installs normally (it is
Windows that is the exception). `docs/deploy-linux-vm.md` §8.

### How do we upgrade it later?
Pull, `uv sync`, `alembic upgrade head`, rebuild the frontend, restart the two services. The
one rule: after a migration that adds tables, re-run `scripts/grant_app_role` so the
application role can read them.

### A developer joins — what do they run?
`docs/local-setup.md`, end to end. Two steps are easy to miss: a **separate test database**,
and `scripts/setup_test_app_role`, without which about thirty tenant-isolation tests fail on
their machine for reasons that have nothing to do with their work.
