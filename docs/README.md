# Documentation index

Start here. Each line says what the document is for and who needs it.

## Deploying the platform

Read in this order. The first three answer "what is this and what does it need"; the fourth is
the procedure.

| Document | What it gives you |
|---|---|
| [platform-overview.md](platform-overview.md) | What the system is, the pieces it is made of, how they talk, and the shape of a deployment. Assumes no prior knowledge |
| [dependencies.md](dependencies.md) | The inventory: services (required vs optional and what is lost without each), software and versions, command-line tools the agents invoke, network access, ports, secrets |
| [deployment-faq.md](deployment-faq.md) | Short answers to what a deployer actually asks — Docker? one machine? which ports? what do we back up? |
| [deploy-linux-vm.md](deploy-linux-vm.md) | **The procedure.** System packages, configuration, database, storage, frontend build, systemd, nginx, smoke tests, backups, and what differs on Linux |
| **`langfuse-deploy/README.md`** — a sibling folder of this repository, delivered alongside it | Self-hosting Langfuse (traces and cost) without Docker, on the same VM. Deliberately outside this repository: it deploys upstream Langfuse, not this product |

## Presenting the platform

| Document | What it gives you |
|---|---|
| [cxo-capability-deck-client.md](cxo-capability-deck-client.md) | **The deck you present.** Ten slides for a CXO audience in any industry, carrying only capabilities that are built and have run on a real project: headline, on-screen content, what to say, and the executive question each slide answers |
| [cxo-capability-deck.md](cxo-capability-deck.md) | **The internal edition.** The same ten slides plus a claims ledger — built and demonstrated against designed-but-not-built — and the known rough edges. Read before presenting |

## Running it on a development machine

| Document | What it gives you |
|---|---|
| [local-setup.md](local-setup.md) | Everything a developer needs: databases, the app role, the **separate test database**, running the servers, and the traps that have cost time before |

## Configuration and integrations

| Document | What it gives you |
|---|---|
| `../backend/.env.example` | Every backend setting, each with the reason it exists and what breaks when it is wrong. The authoritative list |
| `../frontend/.env.example` | The four frontend settings |
| [azure-key-vault-setup.md](azure-key-vault-setup.md) | Reading secrets from Key Vault instead of the environment file |
| [azure-acs-email-setup.md](azure-acs-email-setup.md) | Sending invitations through Azure Communication Services |

## How the platform is built

| Document | What it gives you |
|---|---|
| [rbac-auth-design.md](rbac-auth-design.md) | Roles, permissions and tenant isolation — the design |
| [rbac-tables.md](rbac-tables.md) | The tables behind it |
| [rbac-auth-implementation.md](rbac-auth-implementation.md) · [rbac-auth-plan.md](rbac-auth-plan.md) | How it was implemented, and the plan it followed |
| [rbac-audit-2026-08-17.md](rbac-audit-2026-08-17.md) | A point-in-time audit of that work |
| [route-inventory.md](route-inventory.md) | Every HTTP route the backend serves |
| [frontend-kt-report.md](frontend-kt-report.md) | Handover notes for the frontend |

## The short version, for a deployer in a hurry

1. One Linux VM. No Docker.
2. Install Python 3.12 + uv, Node 20.11+ + npm, PostgreSQL, Redis, nginx.
3. Fill `backend/.env` (44 keys, all documented) and `frontend/.env.local` (4 keys).
4. Migrate the database, grant the application role, build the frontend.
5. Two systemd services — the backend with **one worker** — behind nginx.
6. Run the three smoke checks in [deploy-linux-vm.md](deploy-linux-vm.md) §9.
7. Optionally add Langfuse for cost reporting.
