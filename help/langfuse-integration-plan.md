# Langfuse integration — OSS-only, with project/user segregation

## Status (implemented 2026-09-09)

| Phase | State |
|---|---|
| 0 — Contain and unblock | **Done** |
| 1 — Complete trace attribution | **Done** |
| 2 — Push filtering into Langfuse | **Done** |
| 3 — Redaction at write time | **Done** |
| 4 — Delete dead code | **Done** |
| 4.1 — Orchestrator tracing | **Done** — orchestrator2 was untraced; now wired |
| E2E verification | **Done** — full stack driven against a stub Langfuse |
| 5.1 — /cost/summary scope leak | **Done** |
| 5.2 — bu_admin trace:view | **Done** — approved; catalog reseeded in both databases |
| 5.3 — eval:view removed | **Done** — removed from both catalogues |
| 3.2 — Trace retention window | **Blocked** — needs a number from the instance owner |
| 6.3 — ClickHouse residency | **Blocked** — operator decision |

Tracing is still **off** (`ENABLE_LANGFUSE=false`): it needs the key pair minted in the
shared instance's UI (Phase 0.4). Everything else is in place, so enabling it is now a
two-line `.env` change rather than a change that activates latent defects.

### End-to-end verification (against a stub Langfuse)

Real backend + real Next.js frontend, driven against a stand-in Langfuse Public API,
because the shared instance's keys are not available yet. 26/27 API checks passed (the
one failure was the checker building a client in a process without the env vars set;
the backend's own log line `Langfuse tracing enabled` covers it).

Confirmed working end to end: `runId` and `userId` populated on every row · `plan` no
longer collapsing into `orchestrator` · `code_review` aliasing to `review` · the user
filter leaving as Langfuse `userId` and the project filter as a `project:` tag · the
tenant tag present on every outbound query and a second tenant getting 404 on a trace
detail · `status`/`worstLevel`/`errorRate` null on the list path and computed on the
detail path · real project names resolved from Postgres · all four payloads parsing
against the shipped Zod schemas, including `/cost`, which used to throw · `/traces` and
`/cost` server-rendering 200.

The write path was exercised too: the SDK POSTed to ingestion, a planted `sk-lf-` secret
did **not** appear in the body, `[REDACTED]` did, and `tokens: 42` survived.

`bu_admin` was verified live — logging in as the seeded Business Unit Admin persona
returns a session carrying `trace:view`, and `/traces` answers 200 where it previously
would have been 403.

**Not covered:** ingestion against the real shared instance (needs its keys), and a real
agent turn end to end (needs a model). Both are the same last mile — Phase 0.4.

**5.2 is applied.** `bu_admin` now holds `trace:view` in
`frontend/lib/auth/role-permissions.ts` (the spec) and `shared/authz/permissions.py` (the
mirror), and the seeded catalog was reconciled in **both** `sdlc_product` and
`sdlc_product_test` — the RBAC catalog lives in the database, so a code-only change would
have made `assert_rbac_catalog` (`process_api.py:602`) refuse to boot. `ROLE_CATALOG`
derives from `permissions.py`, so nothing else needed editing.

`effective-role.ts` was checked before granting: it infers Security Engineer from
`audit:view && trace:view`, which `bu_admin` would now also satisfy — but the `bu_admin`
branch (`role:manage` / `workspace:manage`) is evaluated first, so the role still resolves
correctly. That ordering is now load-bearing.

**Deployment note:** any other environment needs the same reconciliation — one boot with
`RBAC_CATALOG_AUTOREPAIR=true`, or the equivalent seeder run — or it will refuse to start.

**5.3 is applied — removed, not wired.** `eval:view` was granted to no role and
required by no route (`eval.py:42` gates on `artifact:view`), so it rendered as a
checkbox on Roles & Access that changed nothing when ticked — finding 8 in
`docs/rbac-audit-2026-08-17.md`, whose stated fix is "either enforce them at the routes
that correspond to them, or remove them from the catalogue".

Unlike 5.2 this needed **no reseed**: `seed_rbac_catalog` only ever inserts permissions,
and `verify_rbac_catalog` compares roles and role_permissions but not the permissions
table, so removing a catalogue string cannot trip the boot guard. Both databases were
checked first — zero `role_permissions` and zero `custom_role_permissions` rows referenced
it — and the now-orphaned `permissions` row was left in place: it is inert, because
`custom_roles.py:66-67` validates against `ALL_PERMISSIONS` in code rather than that
table, so the string can no longer be granted. Delete it if you prefer a clean table.

Note the audit lists eight more permissions in the same state (`agent:invoke`,
`run:view`, `connector:request`, `skill:promote`, `skill:approve`, `skill:import`,
`skill:edit:project`, `artifact:export`). They differ in one respect: each IS granted to
roles, so removing any of them changes role definitions rather than deleting an unused
string. Out of scope here.

## Context

Langfuse is wired but off (`backend/.env:111`). Goal: enable it against the shared Azure
instance and make agent logs **segregable and filterable by project and by user**, using
**only the open-source feature set** — no Enterprise licence.

Turning the flag on is not a config change. It activates dormant defects, and the
attribution the filtering depends on is largely **not being written today**.

## What OSS gives us, and what it costs

Everything behind the EE paywall is out of scope:

| Feature | Status | Consequence |
|---|---|---|
| Instance Management API (`POST /api/admin/organizations`) | EE only | Cannot create Langfuse orgs programmatically |
| Organization Management API (`POST /api/public/projects`, `…/apiKeys`) | EE only | Cannot create projects or mint keys programmatically |
| Project-level RBAC | EE only | A Langfuse UI user's org role applies to every project |
| Ingestion + Public read API + tags + `userId` | **OSS** | **Everything the filtering requirement needs** |

**Therefore: one Langfuse project, and Langfuse is a datastore, not the access boundary.**
Nobody signs into Langfuse; the backend holds one service key pair; all three tiers
(Organization → Business Unit → Project) are enforced in this app, where they are already
modelled correctly — `can_perform.py:258-330`'s `visible_project_ids` descends organization →
business_unit → project, and `workspaces` *is* the Business Unit (`orm.py:37-41`).
Segregation rides on the trace as attribution; filtering is pushed into Langfuse's query API.

**Auto-provisioning a Langfuse entity per BU/Project is dropped.** There is no supported OSS
path to it. Two alternatives exist and neither is recommended as a default:

- **Manual UI provisioning** — creating orgs/projects and generating keys in the Langfuse UI
  is free in OSS. Workable for a handful of BUs, but it is manual ops on every create and
  rename, and multiplies key management. Take this only if physical separation is later
  mandated.
- **Direct writes to the Langfuse Postgres** — what the sibling agentfabric service does
  (`LANGFUSE_DB_URL`, `OBS_PROVISIONING_ENABLED`, `LANGFUSE_HASH_STRATEGY_VERSION: "v1"`). It
  works unlicensed but couples us to Langfuse's internal schema — their own
  `LANGFUSE_SCHEMA_VERSION_TAG` / `LANGFUSE_SCHEMA_LOCK` settings exist to pin that schema so
  upgrades don't break the writes — and it reimplements a licensed feature. Not recommended.

Isolation therefore stays application-enforced, which is what PRD §17/§45 call an R1 release
gate. Phase 2 hardens it as far as OSS allows; the residual risk should be recorded as an
accepted limitation rather than left implied.

---

## Phase 0 — Contain and unblock

**0.1** `langfuse_cred_help` is untracked *and* not gitignored, holding a live Postgres admin
password, an ADO PAT, Pinecone/Neo4j keys and two Azure client secrets. Gitignore or move it;
treat the contents as disclosed.

**0.2 Fix the Cost dashboard crash before enabling anything.** `frontend/lib/api/cost.ts:22`
requires `agentType: Phase`; the backend never emits it (`_schemas.py:1012-1022`) and
`tests/cost/test_cost_api.py:155` asserts its absence. Empty `rows` parse fine today; the first
real row throws in `cost-dashboard.tsx:92`. Make it optional, hide the column, fix the stale
docstring at `_schemas.py:1028`.

**0.3 Fix `backend/.env`** (~111-116): `LANGFUSE_PUBLIC_KEhiY` is a typo so the public key is
never read (`client.py:48` needs both keys truthy); `LANGFUSE_HOST` points at Langfuse
**Cloud**; there is a stray `en` line. Point at `https://lang.98.70.239.18.nip.io`, keep
`ENABLE_LANGFUSE=false` until Phase 2 lands.

**0.4 Prove connectivity** before writing code:

```bash
curl -u "pk-lf-…:sk-lf-…" "https://lang.98.70.239.18.nip.io/api/public/traces?limit=1"
```

A TLS error means the `nip.io` cert is untrusted — `httpx` and the SDK both reject it and there
is no verify-disable hatch in this codebase.

---

## Phase 1 — Write complete attribution (everything else depends on this)

**You cannot filter on what was never written.** Of 15 `langfuse_langchain_extras` call sites,
only 5 pass `user_id`, 3 pass `workspace_id`, and two pass no `tenant_id`:

| Field | Passed at | Missing at |
|---|---|---|
| `user_id` | design `:256`,`:538` · pm `:76` · requirements `:447`,`:681` | code_review `:385` · deployment `:256`,`:349` · development `:495`,`:729` · documentation `:286` · security `:364` · testing `:813`,`:1263` |
| `workspace_id` (BU) | design `:256` · requirements `:447`,`:681` | all others |
| `tenant_id` | most | development `:729` · testing `:1263` |

**1.1 One identity helper, every call site through it** — e.g.
`trace_identity(request_or_claims, *, project_id, agent_type)` returning the full kwargs
bundle. Fifteen hand-written call sites have already drifted; a single constructor is what
stops the sixteenth. Identity comes from `request.state.user_id` (REST) or ticket claims (WS),
**never** a request body — the principle already stated at `requirements_agent_api.py:628`:
*"kept for wire compatibility; NOT trusted for identity"*.

**1.2 Add `run_id` to metadata.** `traces.py:157` reads `meta.get("run_id")` but no writer sets
it (`callbacks.py:207-220`), so `runId` is always null. FR-08 requires traces *"correlated to …
workstream"*.

**1.3 Pin it with a drift test** asserting every call site supplies tenant, workspace, project,
user and agent_type.

**1.4 Fix `agent_type="plan"`** — `pm_agent_api.py:76` emits `"plan"`; `traces.py:70-73` omits
it, so `_agent_type()` files every PM trace under `"orchestrator"`. The frontend enum already
has `plan` (`enums.ts:40`).

---

## Phase 2 — Push filtering into Langfuse

`GET /api/public/traces` (OSS) accepts `userId`, `sessionId`, `name`, `tags`,
`fromTimestamp`/`toTimestamp`, `page`, `limit`. `userId` is first-class; `tags` **AND**s.

**2.1 Project filter → tag pushdown.** Today `list_traces` fetches one tenant-tagged page and
filters client-side (`_apply_trace_filters`, `:288-302`). When `project` is supplied, send
`tags=[tenant:<id>, project:<id>]` — correct, fully-paged results.

**2.2 User filter → native `userId` pushdown.** Add a `user` param to `GET /traces` and
`/traces/metrics`, passed through as `userId`. This delivers PRD §15.9's *"grouped by member"*
and §32.1's *"unit → project → member"*.

**2.3 The unfiltered multi-project case** — the one genuinely open bug (`traces.py:352-357`;
*"Still open"* at `docs/rbac-audit-2026-08-17.md:223-225`). Fix with the fan-out this repo
already uses at `cost.py:179-194`: one query per visible project tag, merge-sorted by
`startTime`, truncated to `limit`; org-wide callers keep the single query. Cap the fan-out.
Give `_LF_CACHE` eviction — it is currently an unbounded module dict (`:91`).

**2.4 Authorization stays ahead of filtering.** Scope first, filter second — the ordering
`trace_metrics` already uses (`:474-480`). A `user` filter narrows *within* visible projects and
must never widen. `_tenant_tag` (`:248-249`) stays unconditional; add a test that a request
cannot influence it.

---

## Phase 3 — The two PRD promises with no code

**3.1 Redaction at write time** (§34.8). Use the SDK's mask hook — `client.py:54-58` constructs
`Langfuse(...)`; add `mask=<callable>`. One function on every input/output means no call site
can forget it. Cover API keys, bearer tokens, connection strings, private keys and this
platform's own shapes. Unit-test the mask against a secret-shape corpus; it is a compliance
control.

**3.2 Trace retention** (§34.8, *"far shorter than audit"*). Set the project retention policy
and record the window in `docs/`. Audit retention is already 30/90/365 in onboarding; traces
must be strictly shorter. Needs a number from the instance owner.

---

## Phase 4 — Delete what is no longer needed

Each item below is dead, misleading, or superseded. Verified, not assumed.

**4.1 `build_agent_callbacks` — delete.** Zero production call sites: only its own definition
(`callbacks.py:49`), the re-export (`__init__.py:12,18`), an `env.py:238` comment and one test
(`tests/observability/test_langfuse.py:24-39`). The pipeline stage runners it was written for
do not exist. All 15 live sites use `langfuse_langchain_extras`. Removing it leaves **one**
trace-emission path instead of two, and deletes ~100 lines of unreachable
`create_trace_id`/span/flush machinery.

*Consequence to accept or handle separately:* `orchestrator2` and `monitoring_feedback_agent`
emit no traces at all, which fails FR-08. If they should be traced, wire them onto
`langfuse_langchain_extras` — the live path — rather than reviving the dead one.

**4.2 `backend/docker-compose.langfuse.yml` — delete.** A 6-container local stack (Postgres +
ClickHouse + Redis + MinIO + web + worker), superseded by the hosted instance. Its own header is
already wrong: it claims *"DEFAULT is Langfuse Cloud (US)"*.

**4.3 `frontend/lib/mock/trace-fixtures.ts` — delete**, with its test
(`lib/mock/__tests__/trace-fixtures.test.ts`). The traces pages call the real API through
`bffProxy`; the fixture header still claims it is *"imported by the app/api/traces route
handlers"*, which is stale. **One live dependency to handle first:**
`__tests__/auth/access-scope.test.ts:13` imports `TRACES` — give that test its own small inline
fixture.

**4.4 The dead `status` filter — remove.** Declared at `traces.py:348` and `:445`, referenced
nowhere in either body; `_apply_trace_filters` handles only agent and project. Remove the param
and the UI control (`traces-explorer.tsx:34,89-94`). On an evidence surface, a filter that
appears to work and silently does nothing is worse than no filter.

**4.5 The permanently-green status column and 0% error tile — remove.** List rows hardcode
`status="approved"` (`:163`) and `worstLevel="default"` (`:169`); `errorRate` is hardcoded `0.0`
(`:493`,`:501`), so the tile reads `0.0%` forever (`trace-metrics-strip.tsx:21-26`) and a failed
run shows green until opened. Remove rather than fake; re-add if the level data is ever derived
on the list path.

**4.6 `SpanModelOut` — remove.** `_schemas.py:1058-1060` is never populated: `SpanOut.model` is
hardcoded `None` at `traces.py:205` (*"provider enum is narrow; omit to avoid client-side
rejects"*). Drop the schema and the field, or populate it — but do not keep a field that is
structurally always null.

**4.7 `.env` cruft — remove.** The commented-out `localhost:3100` block and its stale keys, the
stray `en` line, and the old Langfuse Cloud key pair.

**4.8 `tests/cost/test_cost_api.py:221-285` — rewrite or delete.**
`test_get_cost_cross_tenant_isolation` seeds `AgentCallLog` rows and asserts
`totalCostUsd < 999.0`, but `/cost` no longer reads `agent_call_logs` at all — it passes
trivially and no longer tests its own docstring.

**Explicitly NOT removed:** `model_call_wrapper._log_retry`'s `create_event` call
(`:128-134`). `create_event` **does** exist on the installed SDK (`client.py:1705`), so this
works; the `hasattr` guard is now redundant but harmless as drift protection.

---

## Phase 5 — RBAC alignment

**5.1 `GET /cost/summary` leaks cross-project spend.** `cost.py:315-320` filters by `id AND
tenant_id` only — no `visible_project_ids`. Any `cost:view` holder, including a `project_admin`
of a different project, can read any project's spend and budget by id. Same class the RBAC audit
fixed for `/cost`, `/cost/budgets` and `/traces/project-summary`; missed here. Fix as its traces
twin does (`traces.py:400-405`): **404 an invisible project**, not zeroes. Extend
`tests/test_aggregate_scope.py`.

**5.2 `bu_admin` has no `trace:view`, but PRD §35 grants them "Unit".** `permissions.py:81` has
`audit:view`/`cost:view` only. `visible_project_ids` already resolves a business_unit binding to
its projects, so it works the moment the permission exists. `permissions.py:9-17` declares a
**MIRROR CONTRACT** (frontend is the spec), so this is a three-sided change, and
`assert_rbac_catalog` (`catalog.py:404-439`) **refuses to boot on drift** — the seeder moves in
the same commit. Flag for security sign-off: it widens who reads prompt/output previews.

**5.3 `eval:view` is unwired** — in the catalog (`permissions.py:311`), granted to no role, used
by no route (`eval.py:42` gates on `artifact:view`). Wire it or remove it.

---

## Phase 6 — Deployment

**6.1 Key Vault.** `secret_bootstrap.py:63-64` already hydrates both keys — **no code change**,
only vault entries named per `secret_name_for()` (`:104-120`): `{prefix}langfuse-public-key`,
prefix defaulting to `sdlc-{ENV}-`. Any `ENV != dev` makes `hydrate_environment()` **fail
closed**: `AZURE_KEY_VAULT_URL` must be set and the pod identity needs *Key Vault Secrets User*,
or the process refuses to boot. One key pair, so the flat mapping is sufficient.

**6.2 Cluster host.** Use the public ingress, **not** `http://langfuse-web:3000` from the other
project's ConfigMap: per `frontend/DEPLOY.md:127` this app runs on `aisdlc-aks` (uaenorth), a
different cluster from `langfuse-aks`, so that name will not resolve.

**6.3 Data residency — operator decision, not a code fix.** The shared Langfuse writes to
**ClickHouse Cloud in `germanywestcentral`** while this platform runs `REGION_CODE: "IN"` /
`centralindia`. Traces carry prompt and output previews, and PRD §17 requires *"Client-defined
residency"*. For a PwC deployment this may be a compliance blocker.

---

## Verification

1. **Connectivity** — the 0.4 `curl` returns `200`.
2. **Client constructs** — `get_langfuse_client()` returns a `Langfuse`, not `None`.
3. **Attribution complete** — one turn of *every* agent; each trace carries `tenant:`,
   `workspace:`, `project:` tags, non-null `userId` and `runId`, correct `agentType` (including
   `plan`). This is the test that proves filtering can work.
4. **Project filter** — `GET /traces?project=X` returns a **full** page of only X, correct past
   page 1.
5. **User filter** — `GET /traces?user=U` returns only that member's runs, correctly paged, and
   nothing for a user outside the caller's visible projects.
6. **Isolation** — a caller bound to one project gets a full page of only visible rows;
   `GET /traces/{id}` 404s a foreign trace; `/traces/project-summary` 404s a foreign project.
   Extend `tests/test_aggregate_scope.py`.
7. **Nothing broke on deletion** — after Phase 4, `pytest` is green and the frontend builds;
   `access-scope.test.ts` passes on its new inline fixture.
8. **Redaction** — unit tests over secret shapes, then a live run with a planted fake key
   confirming it never reaches the UI.
9. **Regression** — `pytest tests/observability/ tests/cost/ tests/test_aggregate_scope.py
   tests/test_enterprise_rbac_catalog.py tests/test_rbac_matrix.py`. **One pytest process at a
   time**; **never against the dev DB** — a full run empties projects, credentials and role
   bindings.
10. **Fail-open preserved** — `ENABLE_LANGFUSE=false` still yields empty results and zeroed
    metrics, never 5xx.

---

## Open decisions

- **Accepted limitation.** With OSS only, tenant/project isolation stays application-enforced.
  PRD §17 calls org-wide trace leakage a release blocker; Phase 2 hardens it as far as OSS
  allows, but the residual risk should be recorded and signed off, not left implicit.
- **Orchestrator tracing** (4.1) — trace `orchestrator2` / `monitoring_feedback_agent` via the
  live path, or accept the FR-08 coverage gap.
- **`bu_admin` trace access** (5.2) — PRD says yes; widens prompt/output visibility.
- **Trace retention window** (3.2) and **ClickHouse residency** (6.3) — need the instance owner.
- **Per-agent cost attribution** — deferred in 0.2; backend and frontend documents contradict
  each other and one should be corrected.
