# Requirements + Design — end-to-end build plan

**Scope.** Take `requirements` (owner: BA) and `design` (owner: Architect) from
"access-hardened, not live" to `builtAgents`-eligible: real connector writes, MCP tools,
BYOK models, and the read/write/both tenancy actually enforced on both.

**Companion docs, same folder.** [`multi-track-agent-access-design.md`](./multi-track-agent-access-design.md)
(Parts 1–3 for what each agent does; Part 5 for the build checklist) and
[`portfolio-1-agent-status.md`](./portfolio-1-agent-status.md) (per-agent status).
This plan follows Part 5's checklist and does not replace either.

---

## 0. Read this first — two things in `portfolio-1-agent-status.md` are now WRONG

That file is dated 2026-08-20 and says "keep this file current". Two of its most
load-bearing claims have since been overtaken, and both are repeated in several
sections, so anyone picking up an agent cold will act on them:

| Claim in the status doc | Reality, verified 2026-08-31 |
|---|---|
| "`resolve_chat_model` **does not exist anywhere in the backend** … BYOK still does not functionally work" (repeated in the Security, Documentation and Code Review sections) | **It exists** — `shared/services/model_resolver.py:263`. The four agents importing it are no longer falling through to the `.env` key. |
| "`shared/services/notification_targets.py` **does not exist as a file anywhere** … all three SharePoint tools currently always fail" | **It exists.** SharePoint publishing is no longer dead code by that route. |

Verify before relying on either — `grep -rn "def resolve_chat_model" backend/shared/`
and `ls backend/shared/services/notification_targets.py`. Neither claim was wrong when
written; both are wrong now. Whoever next touches the status doc should fix those
sections rather than leaving a reader to discover it the hard way.

**Nothing else in that doc was found to be stale.** The access-hardening status,
the Semgrep/Trivy/Gitleaks environment gotchas, and the RTM traceability finding all
still hold.

---

## 1. What actually exists today (verified, not assumed)

Part 5's opening warning is "don't assume done because a file with the right name is
there". Applied to these two:

| | Requirements | Design |
|---|---|---|
| Python, excl. tests | ~4,270 lines | ~3,960 lines |
| Real LangGraph agent | yes | yes |
| `assert_agent_access_for_chat` on chat paths | 6 call sites | 4 call sites |
| `require_agent_access(...)` as a **router dependency** | no — and it would be a no-op, see G3 | no — same |
| MCP tool injection (`make_dynamic_tool_node`) | yes — `agents/planning.py` | yes — `agents/architecture.py` |
| BYOK model | **correct** — `resolve_model_for_run` + `set_resolved_model` | **correct** — same |
| Connector usage | **none** | Figma only — `tools/figma_tools.py` |
| In frontend `builtAgents` | no | no |
| Registry entry | `pipeline_position=1`, out `requirements_payload` | `pipeline_position=2`, in `requirements_payload`, out `design_artifacts` |

**Neither is a stub.** The work is not "build an agent"; it is closing four specific
gaps. That reframing matters for estimating.

### The four gaps

**G1 — Requirements cannot write to the board at all.** Its registry entry declares
`board.read` as *required* and `board.write` / `board.comment` as optional, and Part 3
describes its headline behaviour as "writes back to the board". There is no connector
call anywhere in the agent. This is the single biggest gap in either agent: its
Consequential tier (§1.5) is currently empty, so its whole approval story is untestable.

**G2 — WITHDRAWN. I had this backwards; these two are the correct ones.**
The first draft said Requirements and Design "build `ChatLiteLLM` themselves" and should
be converged onto `resolve_chat_model`. Reading the resolver properly shows the two
functions are **complementary halves, not rival paths**:

- `resolve_model_for_run(tenant_id, model_id, offering_id)` — the RUN-level resolution.
  This is the half that enforces budgets, grants and rate limits, and stashes the result
  via `set_resolved_model()`.
- `resolve_chat_model(...)` — a NODE-level convenience whose own docstring says it
  *"Reads the ResolvedModel the run's primary node stashed via set_resolved_model()"*.
  With nothing stashed it falls through to the raw `ANTHROPIC_API_KEY`
  (`model_resolver.py` ~line 313), or fails closed under `AGENT_RUNTIME_MODE=enterprise`.

Requirements (`planning.py:2342`) and Design (`architecture.py:1106`) both call
`resolve_model_for_run` + `set_resolved_model`, with typed handling for
`NoModelConfiguredError` / `ModelNotEnabledError` and a legible user-facing message —
the same shape Testing and Deployment use. **They are doing the more complete thing.**

The real asymmetry runs the other way: **Code Review and Security call only
`resolve_chat_model`**, which is a no-op unless some other node stashed a model first.
Whether those two ever receive a tenant's BYOK model, or always land on the env-key
fallback, is worth their owners checking — it is not part of this build.

**Action: none.** Converging these two onto `resolve_chat_model` would replace
budget-and-grant-enforcing resolution with a read of a stash nothing fills.

**G3 — RESTATED. The prescribed fix would have been a no-op; the real gap is narrower
and worse.**

The first draft said to add `Depends(require_agent_access("<id>"))` to both routers, per
Part 5 step 4. Reading the dependency: it does
`request.path_params.get(project_id_param)` and returns immediately when that path
parameter is absent — its own docstring says *"a route with no `{project_id_param}` path
parameter passes through untouched"*.

Neither router has such a route. Every path is project-less:

```
requirements:  /ado/work-item   /test-ws   /ws   /chat/   /download/{filename}   /sessions
design:        /ws   /chat/   /sessions
```

So adding it would enforce nothing while making the router *read* as gated — worse than
leaving it off, because the next person trusts the decorator. **Do not add it** unless a
route is genuinely re-addressed as `/{project_id}/...`, which is a frontend contract
change nobody has asked for.

**What the gap actually is.** The chat routes are covered — `assert_agent_access_for_chat`
runs per message. What is NOT covered is the non-chat routes, and one of them matters:

> `GET /sdlc/agent/requirement/download/{filename}` — `download_generated_file(filename: str)`

It takes no `Request`, so it cannot check tenant, project or agent access. Path traversal
IS guarded (`realpath` + a `startswith` check) and the route sits behind the router's
`artifact:view` dependency, so this is not an unauthenticated arbitrary-file read. But
`outputs/` is a **flat, process-wide directory** and the prompts that write into it use
fixed names — `outputs/brd.docx`, `outputs/pdd.docx`, `outputs/risk_register.docx`
(`deployment_agent/api.py:44`, `main.py:47`). There is no tenant or run segment in the
path at all.

Two consequences, both real: one tenant's generated BRD **overwrites** another's, and any
authenticated user holding `artifact:view` can download whichever one is currently on
disk. This is precisely the failure the Blob path convention in §4
(`{tenant_id}/{run_id}/{artifact_type}/{filename}`) exists to prevent, and it is the
strongest argument for doing §4 before flipping either tile live.

**G4 — Design's only connector is Figma.** Its gate is "mark epic Design Complete", a
board write. Same shape as G1, smaller: it has one connector, but not the one its gate
needs.

---

## 2. The tenancy model — read / write / both

This is already built and is the part you should *not* redesign. Understanding it is
most of the work.

**Where the level lives.** On the project's **stage**, in `projects.tool_access_modes`
(migration 0024) — not on the Business Unit's grant. Since 0024 a unit grant records
only *that* a unit may reach an integration at all; the stage decides read vs write.

**It is a lattice, not a ladder** (`shared/authz/connector_access.py`):

```
        read_write
        /        \
     read        write
        \        /
         (nothing)
```

`read` and `write` are **incomparable** — neither contains the other. `permits()` is a
subset test, not `>=` on an integer, precisely so `write` cannot quietly imply `read`.
The UI calls the widest level "both"; this module calls it `read_write`. Same thing.

**What enforces it.** `config/connectors/scoped.ScopedConnector` wraps a connector and
gates `read_adapter` / `write_adapter` on the level. Agents must obtain connectors
through the scoped path — never construct a raw connector — or the level is bypassed.

**Default is `read`** (`DEFAULT_ACCESS`). Least privilege: an admin who wants a stage to
write must say so.

### How this lands on these two agents

| Agent | Tier | Action | Level needed |
|---|---|---|---|
| Requirements | Safe | ingest, draft BRD/PDD, extract stories, quality checks | none |
| Requirements | Safe | read board context | `read` |
| Requirements | **Consequential** | write stories back to the board | `write` |
| Requirements | **Sign-off** | baseline requirements | n/a (artifact) |
| Design | Safe | read requirements artifact, generate the 8-section package | none |
| Design | Safe | read Figma frames | `read` |
| Design | **Consequential** | mark epic "Design Complete" | `write` |
| Design | **Sign-off** | accept the design | n/a (artifact) |

A stage left at the `read` default therefore gets a **fully working agent with its
Consequential tier disabled** — which is the correct, legible failure, not a bug. Test
this case explicitly; it will be the common one.

---

## 3. Plan

Ordered so each phase is independently verifiable. Requirements before Design, because
Design consumes `requirements_payload` and a real hand-off cannot be tested before the
producer is real (Part 5 step 6).

### Phase 1 — Converge the seams (both agents, ~small)

1. ~~**G2**: converge both agents onto `resolve_chat_model`.~~ **CUT — see G2 above.**
   Both already resolve BYOK correctly via `resolve_model_for_run`; making this change
   would have been a regression. Phase 1 is therefore step 2 only.
2. **G3**: add `dependencies=[Depends(require_agent_access("requirements"))]` /
   `("design")` to both routers. The factory exists and is ready —
   `shared/authz/agent_access.py:168`, signature
   `require_agent_access(agent_id, project_id_param="project_id")`. Note the second
   parameter: it reads the project id from a **path/query param of that name**, so a
   route whose project id arrives in a Form field or inside a JSON body needs the
   per-message check instead. Design's REST `/chat/` takes `project_id` as a Form
   field (added during access-hardening), so the router dependency covers its other
   routes, not that one.

   Keep every per-message `assert_agent_access_for_chat` call regardless — it also
   checks project membership, which the router dependency does not.
3. Regression tests for both, mirroring `test_security_agent_tools.py`'s
   `_resolve_model` pair.

**Done when:** existing chat-access tests still pass, plus two new `_resolve_model`
tests per agent.

### Phase 2 — Requirements: the board connector (CORRECTED — see below)

> **This section as originally written was wrong, and the correction is the useful part.**
>
> It assumed the Requirements agent had no board tools and that Phase 2 meant building
> them. It has about twenty, defined in `agents/planning.py` and bound at line 1978:
> `list_board_items`, `create_board_item`, `update_board_item`, `delete_board_item`,
> `write_stories_to_board`, `add_board_comment` and the rest. They are already
> provider-neutral and already go through the scoped connector via `_board_connector(mode)`
> (`planning.py:1169`), which checks `permits(level, mode)` **before** attempting the
> call so a refusal reads as a permission statement rather than a board rejection an LLM
> would retry.
>
> Steps 4–6 and 8 below were therefore already done, and done well. Writing a
> `read_board_context` tool — which is what "read tool first" produced — duplicated
> `list_board_items` and was deleted rather than shipped: two tools with the same job
> make tool selection worse, not better.

**What was actually broken.** The tools were fine; the connector handed to them was not.
Two independent defects, both of which made every board tool answer as though the board
were unreachable:

4. **The stage was never named.** `workers/requirements_worker.py`, `design_worker.py`,
   `development_worker.py` and `shared/services/agent_run.py::agent_run_scope` all called
   `get_connector_for_session(project_id=...)` without `agent_id`. Since migration 0024
   the level is stored per `(stage, tool)`, so `effective_access` returns `None` for a
   caller that names no stage (`shared/authz/connector_grants.py:196`), and
   `permits(None, mode)` is `False` for every mode
   (`shared/authz/connector_access.py:87`). **Every board tool in every queued run and
   every chat turn was denied, whatever an administrator had granted.**

   It fails in the safe direction, which is why it survived: nothing errors loudly, the
   feature is just quietly dead behind a plausible message. `copilot_api.py` already
   passed `agent_id` with a comment explaining exactly this — these four sites were left
   behind when that fix landed.

5. **The board kind was hardcoded.** All three workers asked for `kind="azure_devops"`.
   A tenant whose Requirements stage is wired to Jira had its grant looked up under
   `target_ref="azure_devops"`, found nothing, and never touched the Jira board it had
   connected. Fixed by reusing `shared/services/agent_run.py::_stage_board_kind`, the
   chat path's existing resolver — which deliberately returns `None` rather than falling
   back to the legacy `provider_kind` column, because that column defaults to
   `azure_devops` for nearly every project. `None` now means *inject no connector*, so
   the tools say "connect a board" instead of reporting a permission error about a
   provider the tenant never chose.

   Covered by `tests/test_worker_connector_scope.py` (20 tests).

**Still open — the Consequential gate (the genuinely remaining work).**

6. `create_board_item`, `update_board_item`, `delete_board_item` and
   `write_stories_to_board` are bound to the model with **no approval step**. They check
   the access *level* (`_board_connector("write")`), which is a different question from
   whether the owning role approved this particular write. Per §1.5 these are
   Consequential; `delete_board_item`'s own docstring calls itself "IRREVERSIBLE".
7. **Gate them in code, not in prompt text.** The status doc records, for Documentation,
   that `open_docs_pr` and `publish_to_sharepoint` are *"enforced by prompt text only"* —
   the tool node executes whatever the model emits. **Do not repeat that here.** This is
   the cross-agent tool-authorization gap; Requirements is a good place to establish the
   pattern because its write is genuinely destructive.

**Done when:** a live run against a real Jira or ADO project creates work items only
after approval; the same run against a `read` stage refuses with a legible message.

### Phase 3 — Design: inherit and mark complete (G4) — DONE

9. **The hand-off was BROKEN, and silently.** Verifying it turned out to be the whole
   point of the step. The payload changes stores on the way across:

   ```
   persist_artifact        -> runs.requirements_payload        (store A)
   pipeline_session        -> _read_run_upstream(run_id)       reads A
                           -> upsert_agent_session(...)        writes agent_sessions (store B)
   build_context           -> fetch_session_artifacts          reads B
   ```

   `_read_run_upstream` opened a **superuser** session, on the stated reasoning that a
   run-keyed pipeline read is a system operation rather than a tenant request. `runs` is
   FORCE RLS, so a session with no `app.tenant_id` GUC reads **zero rows** — not an
   error, just nothing. Measured directly:

   ```
   tenant-scoped session sees the run: 1
   SUPERUSER session sees the run:     0
   _read_run_upstream returned keys:   EMPTY
   ```

   So it returned `{}` for every run, the mirror wrote nothing, `build_context` returned
   `""`, and **the Design agent never received the Requirements payload at all**.
   Nothing logged: "no upstream yet" is legitimate on the first stage, and
   `upsert_agent_session` swallows its own failures. `persist_artifact`'s docstring
   already warned about this exact trap ("once Plan 04 enables FORCE RLS this fallback
   will return zero rows unless the role is BYPASSRLS").

   Fixed by threading `tenant_id` (which `pipeline_session` already has) into the read.
   `tests/test_requirements_to_design_handoff.py` covers it against real Postgres,
   including a control that fails if the mirror is ever removed, and a test that pins
   the unscoped-read behaviour so the fix cannot be quietly reverted.

10. `update_ado_epic_design_complete` (the "mark design complete" tool) is now gated on
    the owning role — see Phase 2's §Consequential gate — and no longer returns `{exc}`
    into the model's context, which was leaking the board's instance URL and API path.
11. **Figma — DONE, and it was broken.** `_figma_session` was on the scoped path
    already, but passed only `kind` and `tenant_id`. The factory documents that exact
    combination as permitting NOTHING, so every Figma read raised
    `ConnectorAccessDenied` — and `_explain` had no branch for it, so the agent saw the
    bare class name. Fixed by passing `project_id=get_project_id()` and
    `agent_id="design"` (both are set by `design_architecture_agent_api` before the
    graph runs), plus an `_explain` branch that names the access level as the thing to
    change.

    Worth stating plainly: naming the stage **loosens nothing**. The connector went from
    permitting nothing at all to permitting exactly what the grant says — which, for a
    project that never wired Figma to its design stage, is still nothing.

**Done when:** ✅ the hand-off passes end to end on real seeded data.

### Phase 4 — MCP (both) — DONE

12. Verified against real Postgres, `tests/test_mcp_tenant_isolation.py`. The finding
    worth recording: **`resolve_server_configs` has no tenant predicate at all** — it is
    `select(McpServer).where(McpServer.id.in_(ids))`. Isolation is entirely the FORCE
    RLS policy from migration 0023, reached through `get_db_session_for_tenant`'s GUC.
    That matters because `server_ids` come from `projects.mcp_servers[stage]`, a plain
    JSONB map: an id pasted from another tenant into that map is the entire attack, and
    the policy is the only thing that stops it. Confirmed it does — same id, other
    tenant, zero configs. Also pinned: the per-stage map (a `design` server does not
    appear for `requirements`), `MCP_ENABLED` winning over a populated map, and
    `is_active=false` excluding a revoked server.
13. Consequential-by-default for MCP tools is **not** implemented and should not be
    assumed. The `owner_approved` rule from Phase 2 is the mechanism to apply, but MCP
    tools are injected dynamically by `make_dynamic_tool_node` and have no equivalent
    choke point — every board write funnels through `_board_connector("write")`, and
    there is no analogous single seam for MCP. Open.

### Phase 5 — Go live — DONE

14. `"requirements"` and `"design"` added to `builtAgents` in
    `frontend/app/(app)/projects/[id]/page.tsx`, alongside `security` and
    `documentation`. Both conditions still apply — `agentsForTrack(track).includes(phase)`
    **and** `builtAgents.includes(phase)` — and the comment there now records what
    "verified" meant for these two, with the test file for each claim.

---

## 4. Artifact storage — Azure Blob

### What already exists (do not rebuild)

| Piece | Where | State |
|---|---|---|
| `Artifact` ORM row | `shared/models/orm.py:148` | **exists** — `run_id`, `tenant_id`, `artifact_type`, `blob_url`, `blob_path`, `content_type`, `size_bytes` |
| Blob client | `shared/storage/azure_blob.py:29` | **exists** — `upload_bytes`, `download_bytes`, `get_url`, `close` |
| Time-limited download URL | `azure_blob.py:91` `generate_evidence_sas_url` | **exists** |
| Client lifecycle | `process_api.py` lifespan → `app.state.blob_client` | **exists**, created only when `AZURE_BLOB_ACCOUNT_URL` is set |
| Frontend download affordance | `components/app/artifact-list.tsx` | **exists** — renders a Download button when `downloadUrl` is set |
| `downloadUrl` on the schema | `lib/schemas/artifact.ts:257` | **exists** (`nullish`) |

### The gap

Both agents build a Pydantic artifact — `RequirementsArtifact`
(`requirements_agent/agents/planning.py:1147`) and `DesignArtifact`
(`design_architecture_agent/agents/architecture.py:512`) — and persist it as **JSONB
only**, into `runs.requirements_payload` / `runs.design_artifacts`. Neither uploads
anything to Blob, and neither writes an `Artifact` row. So the Download button in
`artifact-list.tsx` has nothing to point at for these two.

**JSONB and Blob are not competing — keep both.** The JSONB payload is the *structured
hand-off* the next agent reads (Design's registry input is literally
`requirements_payload`). The Blob is the *human deliverable* — the BRD, the PDD, the
risk register, the C4 diagrams — which are documents, not fields, and do not belong in
a JSONB column. Replacing the payload with a blob would break the hand-off; adding the
blob alongside it is the change.

### Steps

15. **Blob naming is a security boundary, not a convention.** `upload_bytes`'s own
    docstring: `blob_name` must be `{tenant_id}/{run_id}/{artifact_type}/{filename}`,
    and *"never accept blob_name directly from user input"*. The model chooses the
    document title; the **code** composes the path from the verified
    `request.state.tenant_id` and the run id. A model-supplied filename is sanitised to
    a leaf name and never allowed to contain `/` or `..` — Documentation's
    `save_document` already has a filename-sanitisation guard and a test for it; reuse
    that shape rather than writing a second one.
16. **One helper, both agents.** Add `shared/services/artifact_store.py` with a single
    `store_artifact(db, *, tenant_id, run_id, artifact_type, filename, data,
    content_type) -> Artifact`. It composes the path, uploads, and writes the `Artifact`
    row in one place. Two copies of this is how one of them forgets the tenant prefix.
17. **Degrade, do not crash, with no blob configured.** `AZURE_BLOB_ACCOUNT_URL` is
    unset in most dev environments, and `process_api` already sets
    `app.state.blob_client = None` in that case. `store_artifact` must return a row with
    `blob_url = None` (or skip cleanly) and the agent must still finish. A generated BRD
    that could not be uploaded is not a failed run.
18. **Serve downloads through a SAS URL, never the raw blob URL.** `blob_url` is a
    container path that is useless without credentials; `generate_evidence_sas_url` is
    the existing pattern for handing a browser a time-limited link. The artifacts
    endpoint populates `downloadUrl` from it at read time — it is never stored.
19. **Tenant isolation on read.** The `Artifact` row carries `tenant_id`; the endpoint
    must filter on the caller's verified tenant, not on the path. The path convention
    prevents *writing* across tenants; the query is what prevents *reading* across them.

**Done when:** a Requirements run produces a downloadable BRD in `artifact-list.tsx`;
the same run with `AZURE_BLOB_ACCOUNT_URL` unset still completes; and a second tenant
cannot fetch the first tenant's artifact by id.

---

## 5. Approval mechanism

### What already exists (do not rebuild)

The gate machinery is complete end to end, and it is **not** the governance-request
system used for access requests:

```
run.gate_pending = true, run.current_stage = "requirements"
        |
        v
_pending_gates()                         shared/routers/approvals.py:85
  stage -> permission via _PHASE_PERMISSION   shared/authz/permissions.py:221
        |
        v
GET /approvals -> ApprovalGateOut { phase, requiredPermission,
                                    capabilityClass: "consequential", mandatory }
        |
        v
approval-queue.tsx / approval-gate-row.tsx / approval-card.tsx
```

`_PHASE_PERMISSION` already maps `requirements -> artifact:approve_requirements` and
`design -> artifact:approve_design`; both permissions exist in the catalog and are
granted to `ba` and `architect` respectively.

### The two gaps — BOTH CORRECTED, one of them was not a gap

**A1 — WRONG, withdraw it.** I claimed `artifact:approve_*` appears "in no route
anywhere". It is enforced: `_handle_gate_decision` calls
`can_user_approve(perms, stage)` before doing anything, and that delegates to
`_PHASE_PERMISSION` and `has_permission` (so the `admin:*` wildcard behaves
consistently). The grep that produced the claim looked for the permission STRING; the
enforcement resolves it from a map, so the string never appears at the call site. A
grep-shaped conclusion about a lookup-shaped mechanism.

Likewise, `approval_requests.decide()` already blocks self-approval
(`shared/services/approval_requests.py:188`) and refuses a request whose initiator is
unknown. My step 22 said this rule was "enforced nowhere"; it was enforced for approval
requests and only missing for **gates**.

**A2 — PARTLY WRONG.** `gate_pending` is written in `copilot_api.py` *and* in
`process_api.py::_handle_artifact_ready`, so the Orchestrator is not the only writer.
The substantive half stands: a standalone chat run does not raise a gate.

### What was actually missing, and is now done

20. **No self-approval on gates — IMPLEMENTED.** The real gap, and it could not have
    been closed as written: `runs` had **no record of who started the run**, so the rule
    had no left-hand side. Migration `0038_run_created_by` adds `runs.created_by`
    (nullable, and the migration explains at length why nullable is the honest choice —
    webhook runs have no human initiator and pre-0038 rows have none recorded).
    `_handle_gate_decision` now refuses an approve from the run's initiator, with a
    message that says who else can do it.

    Two deliberate carve-outs, both tested:
    * **Reject is still allowed** from the initiator. Sending your own work back is the
      one decision they cannot abuse, and blocking it strands a run nobody can move.
    * **Unknown initiator does not block.** `NULL` means "cannot prove self-approval",
      not "deny" — refusing instead would block every pre-0038 and every webhook run.
      The residual risk is a run created through a path that does not set `created_by`;
      `tests/test_gate_self_approval.py` pins the manual route that matters.

21. Not needed — see A1.
22. Done, above.
23. **Two gates, and the Consequential one is IMPLEMENTED.**
    `shared/authz/consequential.py::owner_approved(stage)` is the in-code rule, wired at
    `_board_connector("write")` (covering all nine Requirements write tools at once,
    including any added later) and at Design's `update_ado_epic_design_complete`. It
    asks whether **this person** may authorise the action — distinct from
    `permits(level, "write")`, which asks whether **this project's stage** may write at
    all. Both must hold.

    **It collided with two existing tests, and that collision is informative.**
    `tests/test_agent_connector_access.py` asserted that a `read_write` project "is
    refused nothing" — a statement about the ACCESS LATTICE that never established a
    person, so it began failing on the new tier check. The tests were right about their
    own subject and were updated to state the precondition they had always implicitly
    assumed (an owner driving the turn), plus a new
    `test_a_write_needs_an_owner_even_on_a_read_write_project` that pins the interaction
    rather than hiding it. The distinction to hold on to: the project's grant is not a
    person's authority, and neither implies the other.

    It fails closed on every uncertainty, and one consequence is worth stating in
    advance rather than discovering later: **a queued worker run sets no user**
    (`set_user_id` is called only on the copilot WebSocket path), so a board write from
    a background run now refuses. Under §1.5 that is the correct answer — nobody
    approved it — but it is a real behaviour change from "writes always went through".
    The proper fix is for an autonomous run to raise a gate and wait, not to hand
    background runs a blanket exemption. **That is the main piece of §5 still open.**

**Done when:** ✅ only a `ba` can decide a Requirements gate, ✅ the person who ran it
cannot, ✅ deciding it advances the run. **Still open:** a standalone chat run does not
raise a gate at all (A2), and an autonomous worker run cannot perform a Consequential
write because there is no way for it to ask.

---

## 6. UI plan

Almost everything needed already exists as a component. The work is wiring, plus two
genuinely new surfaces and two states nobody has designed yet.

### Reuse as-is

| Component | Role in this flow |
|---|---|
| `agent-chat-drawer.tsx` | the agent conversation itself |
| `artifact-list.tsx` | lists artifacts, renders Download when `downloadUrl` is set |
| `approval-queue.tsx`, `approval-gate-row.tsx`, `approval-card.tsx` | the gate queue and the decision |
| `projects/[id]/orchestrator` | the cockpit a run is driven from |
| `lib/agent-access.ts::tileStateFor` | owner / use / locked / coming_soon tile states |

### What changes

24. **Flip the tiles last.** `tileStateFor` requires *both*
    `agentsForTrack(track).includes(phase)` **and** `builtAgents.includes(phase)`.
    Adding `"requirements"` and `"design"` to `builtAgents` is the entire go-live
    switch — and per Part 5 step 6 it happens after end-to-end verification, never
    before.
25. **Artifact panel on the project page — new wiring.** `artifact-list.tsx` exists but
    nothing routes these two agents' output into it. Wire `GET /runs/{id}/artifacts`
    (the BFF route already exists at `app/api/runs/[id]/artifacts`) and render per
    phase.
26. **Approval affordance in the agent view — new.** When a run is `gate_pending` for
    the phase being viewed, show the gate inline with Approve / Request changes, gated
    on the viewer holding that phase's permission. Someone without it sees the gate and
    who it is waiting on — never a dead button with no explanation.
27. **The read-only stage needs a real empty state.** Per §2, a stage left at the `read`
    default has a fully working agent with its Consequential tier disabled. The UI must
    say *"this stage can read Jira but not write to it — ask an admin to change the
    stage's access level"*, not grey a button out with no reason. This is the most
    common configuration and currently has no design.
28. **Self-approval refusal is a UI state, not just a 403.** Per step 22 the person who
    ran the agent will see the gate and must be told *why* they cannot decide it —
    "you ran this; someone else on your team accepts it" — rather than meeting a
    disabled control or an error toast.

---

## 7. For teammates on the other six agents

Reusable from this plan regardless of which agent you own:

- **§0** — check `resolve_chat_model` and `notification_targets` yourself before
  believing the status doc's "does not exist" notes. Both now exist.
- **§2's lattice** — every agent with an outside-world write needs the same
  read/write/both reasoning. `read` is the default, `read` and `write` are
  incomparable, and `ScopedConnector` is the only thing enforcing it. If your agent
  builds a connector directly, the level is bypassed.
- **Phase 2 step 7** — the "gated by prompt text only" gap is cross-agent and applies
  to `open_docs_pr`, `publish_to_sharepoint`, and every Consequential tool in every
  agent. If Requirements establishes an in-code approval check, copy it rather than
  inventing a second one.
- **The environment gotchas** in the status doc's Security section (Semgrep's `--config
  auto` silently skipping untracked files; winget not refreshing `PATH` in running
  shells) are still accurate and still undeclared as project dependencies.

---

## 8. Open questions

1. **Which board is authoritative per project** — **PARTLY ANSWERED from the code.**
   `project_integration_config (project_id, kind, target_id, base_url)` records the
   connectors a project is configured for (migration 0033; `target_id` is the connector
   kind, e.g. `jira` / `azure_devops`). So the board is normally derivable: use the one
   the project has configured. What remains genuinely undecided is the tie-break when a
   project has **both** — pick one, ask the user, or refuse. Narrow enough to not block
   Phase 2''s structure, but it must be answered before the write tool ships.
2. ~~Where does the Consequential approval live?~~ **ANSWERED while writing §5**: the
   run-gate machinery (`runs.gate_pending` + `_PHASE_PERMISSION` + `approvals.py`),
   NOT the governance-request tables — those carry access requests. What is genuinely
   undecided is step 20: whether a standalone agent run may raise its own gate, or
   whether only the Orchestrator can.
3. **Track 2 (Enhancement)** runs this same portfolio but Requirements produces an
   impact assessment rather than first-release requirements, and Design is skipped
   unless needed (§Part 3). Is Track 2 in scope for this build, or Track 1 only?

---

*Written 2026-08-31 against the code as it stands on `chore/backend-cleanup-rbac`.
Every "verified" claim above was checked by reading the code, not inferred from the
other docs — including the two corrections in §0.*
