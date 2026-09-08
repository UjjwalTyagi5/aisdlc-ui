# Orchestrator Phase 4 — Deliverables

**Status:** approved 2026-09-06. Supersedes nothing; extends `orchestrator_instruction.md` §1.4
(the Artifacts requirement) and §8 (the phase plan).

Phase 4 is requirement 7 of the user's original message: *"All the generated documents are
stored on the artifact tab for reference… in the artifacts tab, there are headings
agent-wise."* Today `orchestrator2` writes nothing, and the cockpit passes `artifacts={[]}`
with `runId=""`.

---

## 1. The distinction this phase turns on

**The Orchestrator's agents are not the standalone agents.** They carry the same names and
the same capability, and they are a different thing. The standalone agents are what a
delivery role reaches from the project page; the Orchestrator's are what a Project Admin
drives through one conversation. D10a already draws this line in the engine — the
Orchestrator reuses the agents' compiled graphs and never their `*_agent_api.py` wrappers,
because the wrappers are the machinery of being separately reachable.

This phase draws the same line through **storage**, and it is the reason for the naming:

| | Standalone agents | Orchestrator |
|---|---|---|
| Concept | **Artifact** | **Deliverable** |
| Table | `artifacts` | `orchestrator_deliverables` |
| Approval | `approval_status` defaults to `pending`; a human approves | **none** — D5, the Project Admin already owns all nine agents |
| Surface | Project Artifacts page | Orchestrator Deliverables tab |

An `artifacts` row needs approval **because** a standalone agent wrote it. Reusing that
table for Orchestrator output would drag an approval concept into a surface the spec says
has none, and would blur two things the user explicitly asked be kept apart. A Deliverable
is never written to `artifacts`; promoting one to the project's shared record, if that is
ever wanted, is a separate deliberate act and out of scope here.

## 2. What is already built (and must not be rebuilt)

Verified against the repo, not recalled:

- `shared/services/orchestrator/artifacts_view.py` — `sections_from_run()` and
  `parse_design_markdown()` are **pure and IO-free**, already shared by the Copilot socket
  and the runs REST API. `parse_design_markdown` is reused verbatim.
- `components/orchestrator/artifacts-panel.tsx` — already groups agent-wise
  (`groupByStage`) and already synthesises the Development code tree.
- `/runs/{id}/artifacts`, `/runs/{id}/workspace/*`, `/runs/{id}/stage-files/{source}/*`
  already exist.
- `_run_dev_work_dir()` resolves the Development clone **by `run_id`**, and `orchestrator2`
  already passes `run_id` as the session id — so the ADO code pull lands where the code
  tree reads with no change.

**A gap found while auditing:** `sections_from_run` has no `plan` branch. `plan_artifacts`
is written by nothing and read by nothing, so the Project Manager agent's output renders
nowhere. This is the same shape as the §2.4 dispatch gap that prompted the rebuild, one
layer further out. Phase 4 closes it.

## 3. Decisions

| # | Decision | Source |
|---|---|---|
| D11 | Orchestrator output is called a **Deliverable**, stored separately from `artifacts`, with no approval concept. | User, direct |
| D12 | **Every version is kept**, newest first, under its agent's heading. A re-run never destroys the earlier document. | User, chosen from options |
| D13 | **The latest version per agent feeds agent context.** All versions are visible; downstream agents are never handed two contradictory PRDs. | User, chosen from options |
| D14 | Its **own table**, not the run's JSONB columns. Versioning becomes a query rather than a whole-blob rewrite that races. | User, chosen from options |
| D15 | Only `deliverable.ready` is emitted. The `open`/`delta`/`end` streaming trio is **not** built — text already streams to chat, so they would be three unused event types. | Claude, YAGNI |
| D16 | The Deliverable event and schema live in `lib/orchestrator/`, **not** imported from `lib/copilot/`, which Phase 5 deletes. | Claude |
| D17 | The shared panel is adapted **at the boundary**. Its tab label becomes a prop defaulting to `"Artifacts"`, so the still-live Copilot is untouched. | Claude |

## 4. Data model

Migration `0044_orchestrator_deliverables`, following `0043_deployments` conventions.

```
orchestrator_deliverables
  id           uuid pk
  run_id       uuid  -> runs.id      ON DELETE CASCADE
  tenant_id    uuid  not null
  project_id   uuid  -> projects.id  (nullable, matching runs.project_id)
  agent_id     varchar(50)  not null   CHECK in the nine
  kind         varchar(20)  not null   CHECK in ArtifactKind
  title        text not null
  content      text
  url          text
  language     varchar(50)
  source       varchar(50)
  created_at   timestamptz not null default now()
```

Indexes: `(run_id, created_at DESC)` — the panel read is "this run, newest first";
`tenant_id`; `project_id`.

**Tenant isolation.** The `app.current_tenant_id` RLS policy pair plus `FORCE`, copied from
0043 including its warning about the `app.tenant_id` name that matches nothing and reads as
a permanently empty table. **And** an explicit `tenant_id` predicate on every read, because
carried debt 1 records that RLS is inert in this deployment — the app connects as a
`rolbypassrls` superuser, so a policy is not load-bearing today. Neither mechanism is the
only thing standing between tenants.

**No `approval_status` column.** Its absence is the schema-level statement of §1.

**Drift guard.** The `agent_id` CHECK duplicates the registry, so a test asserts the
constraint's value set equals `AGENT_IDS` — the same shape as the existing
`AGENT_IDS == STAGE_ORDER` import-time assertion.

## 5. Backend

### 5.1 `orchestrator2/deliverables.py`

Split the way `artifacts_view.py` is — a pure core and a thin IO shell, so the interesting
logic is testable without a database:

- **pure** `render(agent_id, reply_text, *, has_files, dev_pointer) -> list[dict]`
  Turn text into deliverable rows. No IO, no imports that touch the DB.
- `capture(...)` — calls `render`, persists, returns the panel-ready list.
- `deliverables_for_run(run_id, tenant_id)` — newest-first read, tenant-predicated. Serves
  both the REST replay and `context.py`.
- `latest_per_agent(run_id, tenant_id)` — D13's reader for context.

### 5.2 Where capture runs

Inside `dispatch.run_agent`, immediately **before** it yields `stream_end`. `run_agent`
already yields every `stream_chunk`, so it accumulates the same text the user saw.

Capturing in `ws.py` after the `async for` loop was considered and rejected: `stream_end`
comes from inside the generator, so `deliverable.ready` would land *after* the turn had
already been declared finished.

**Capture failure must never fail the turn.** The agent has done its work and the user has
read it; losing the persistence step is a smaller harm than losing the reply. Capture is
wrapped so a failure emits a typed `error` and still yields `stream_end` — but it is
logged at `exception` level, never swallowed silently. That silent-swallow is precisely how
§2.4's bug survived.

### 5.3 What each agent produces

A turn becomes a Deliverable when its reply is **substantive** — the Copilot's 200-character
precedent, which leaves clarifying questions ("which service do you mean?") in chat where
they belong. On top of that baseline:

| Agent | Additional |
|---|---|
| `design` | Split into HLD / LLD / C4 / API contracts / DB schema / ADRs / tech stack by the existing pure `parse_design_markdown`. Reused, not reimplemented. |
| `development` | A `code-tree` pointer (`dev-code`) and a PR `link` when one exists. |
| `plan` | Nothing special — the baseline, which is what closes §2's gap. |
| any | A `file-tree` pointer when that agent generated files on disk. |

**Pointers are synthesized on read, never stored** (corrected while planning). The code
tree, the per-agent file trees and the PR link are not documents — they are references to
state that already lives elsewhere (the run workspace, disk, Azure DevOps). Persisting one
per turn would stack a duplicate row on every turn the agent ran, and their ids must stay
**stable** anyway, because the panel de-dupes the Development tree on the literal id
`dev-code`. So `pointers_for_run()` is pure and the REST read supplies them; deliverable
rows are only ever documents.

Which agents wrote files is decided by the **REST layer**, the only place that can look at
the disk, and passed into the pure function. An agent that generated nothing gets no tree:
an empty tree reads as a pull that failed rather than as a stage with no files.

During a live turn the panel already synthesises the Development tree itself whenever
Development is the active agent, so nothing is missing before the first REST refresh.

**`runs.development_artifacts` keeps being written** with `repo_url` / `branch_name` /
`base_sha`. That is a **workspace recovery pointer, not a Deliverable**:
`_run_dev_work_dir`'s last-resort branch re-clones from it, so dropping it would break the
Development code tree after a backend restart. It is deliberately not migrated to the new
table, because it is not the same kind of thing.

### 5.4 REST

`GET /runs/{run_id}/deliverables` returns `{deliverables: [...]}`, mirroring
`get_run_artifacts`. It is **new attack surface**, so it carries the same access control as
its neighbours: `_get_run_or_404` with the tenant predicate. `assert_all_routes_protected`
covers REST routes (it skips only WebSocket routes — carried debt 4), so this endpoint is
checked systemically.

### 5.5 Context — `context.py`

Moves from `ARTIFACT_COLUMNS` (run columns) to the table, taking **the latest version per
agent** (D13). Its import-time validation currently proves every agent resolves a real
`runs` column; the equivalent proof against the new table is that `agent_id`'s CHECK
matches `AGENT_IDS` (§4), so the validation moves rather than disappears.

Its equal-share truncation budget is **unchanged**. Carried debt 6 — a PRD cut to ~2,400
characters on a full nine-agent run — stays open and still needs a product decision. It is
recorded here so this phase is not mistaken for having addressed it.

## 6. Frontend

- `lib/orchestrator/deliverables.ts` — Zod `Deliverable` + `DeliverableReadyEvent`, owned by
  the Orchestrator (D16).
- `lib/orchestrator/use-orchestrator-socket.ts` — the dead `break` on the four artifact
  cases becomes real accumulation; exposes `deliverables`.
- `components/orchestrator/cockpit.tsx` — passes the real `runIdRef` value instead of `""`,
  and the accumulated deliverables instead of `[]`.
- Reload/replay — `GET /api/runs/{id}/deliverables` through the BFF.
- `artifacts-panel.tsx` — a `Deliverable` is structurally what the panel already renders, so
  it maps directly at the boundary. New: a `tabLabel` prop (default `"Artifacts"`, so the
  Copilot is untouched), newest-first ordering within each agent group, and a timestamp on
  each version.

### 6.1 The protocol trap

`protocol.ts` validates every inbound frame with `safeParse` and **drops** what fails. This
has already cost two production-shaped bugs on this branch (an `ErrorEvent.agent` enum that
guaranteed the "unknown agent" error could never arrive; a `StreamChunkEvent.content` that
dropped Anthropic's block-list content). The existing
`test_every_emitted_event_type_is_in_the_frontend_union` compares **types only**.

So this phase adds a test comparing the **field shapes** of what the backend emits against
the Zod schema.

## 7. RBAC and project scope — what must not regress

Phase 3 established these; Phase 4 must leave them intact and is the phase most likely to
erode them, because it adds a read path.

- **Project Admin only**, checked server-side on the socket before the handshake is
  accepted.
- **Per-project**, not per-tenant: a Project Admin of project A cannot drive project B's
  run. The turn's project comes from the verified `runs` row and **never** from the client
  frame.
- **Models and connectors are the project's.** BYOK resolution is project-scoped —
  `run_agent` takes `project_id` as a keyword with no default, deliberately, so a future
  call site cannot drop project scoping silently. Deliverable capture inherits this
  `project_id` rather than re-deriving it.
- The new REST endpoint is tenant-scoped and run-scoped (§5.4).

## 8. Testing

Per the branch standard, which caught every significant defect here:

- One agent implements, a **different** agent reviews.
- **A green suite is not evidence.** Each test is proven by breaking the implementation and
  watching it fail. A mutation reporting SURVIVED is not a result until the diff is shown —
  `str.replace` no-ops silently on a non-matching pattern and the suite then prints green
  for an unmodified file.
- Unit tests for the pure renderer (no DB). Integration tests for capture, the tenant
  predicate, and cross-tenant refusal. Frontend tests for socket accumulation, panel
  grouping, version ordering, and the field-shape contract of §6.1.

## 9. Out of scope

Streaming documents into the panel live (D15); promoting a Deliverable into the project
`artifacts` library; the context truncation budget (carried debt 6); retiring the old
engines (Phase 5); the e2e rewrite the user deferred in Phase 1.
