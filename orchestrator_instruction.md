# user input 
❯ So the next major task that we have to do is the creation of  THE orchestrator. Now on the main page, you would see that there is an orchestrator Tab Now we have to fully build this tab. Now in our previous versions of this application, you can see in the old version  There was an orchestrator and we would reach that page by clicking on "Run Agent" button on the top right on the Projects page tab. [Image #54] Now, clicking this button leads us to the older version of our orchestrator page. It asks to run pipeline, and when you click on "Run pipeline with that model" [Image #55] It leads to this page, which is the older version of our orchestrator. now, the next thing is, there's a newer version of our orchestrator, which is the page on the left side when you click on orchestrator. clicking that leads to [Image #58] This page, which is an entirely different page Let's call the older version Copilot and the newer version Orchestrator, like it does in the frontend. What we first have to do is merge both Into one single thing called an orchestrator There are some things about the copilot version that are good. For example, the right tab called "Artifact" which shows agent-wise artifacts And the activity tab and the context tab [Image #59] That needs to be incorporated in our new thing. But there are some things that need not be incorporated. [Image #60] Such as this left A pipeline thing . because in what we are going to make in our new orchestrator, there won't be a linearity that, after one agent, the next comes. Any agent can come at any time according to the chat. The first thing that we have to do is in the frontend We have to remove this copilot thing, the older one, and bring its features(artifcats activity context chat) to the orchestrator tab. And clicking the Run Agent button in our Projects tab leads directly to this Orchestrator tab. We have to make a proper frontend based on the functionality which our orchestrator will have, which is as follows: only a project admin that is, someone who has access to all agents has access to the orchestrator. Now, what is the orchestrator? The orchestrator is a chat which has all the agents that we have built till now incorporated into it. Plus a context agent whenever a project admin comes and talks to the orchestrator and ask for something, for example, I need a PRD, the automatically the agent for that task starts up. It fully does the task. All the generated documents are stored on the artfact tab for reference. All the extra frontend quirks that each of our agents has, for example, the development agent being able to pull this code. When our agent in our orchestrator pulls the code, in the artifacts tab, there are headings agent-wise. For example, when the development agent comes and pulls the code from the connected ADO The pull code is visible then on its artifacts tab. One last major thing is that, although the orchestrator is visible on the home page on the left side Its hierarchy is inside a project. Any run pertaining to that orchestrator is inside the project. So it will follow all the rules of the project, all the integrations, and the models provided to that project Only a project admin can use it. Because he has access to all the agents. Go through the architecture of our platform. Write an MD document explaining all these instructions and all the behaviors that 

# Orchestrator — instructions, architecture audit, and build plan

Working document. Section 1 is what the user asked for. Everything from Section 2
down is Claude's audit, design and plan, updated as the work proceeds.

Started 2026-09-05. Branch: `feature/development-agent-verification`.

---

## 1. What was asked for (source of truth)

These are the user's requirements, restated without invention. Where a requirement
was ambiguous and later settled by a direct answer, the settled version is written
here and the decision is recorded in Section 4.

### 1.1 Merge Copilot and Orchestrator into one thing

Two surfaces exist today and both are called the orchestrator:

- **Copilot** — reached from the **Run agent** button on the Projects page, at
  `/projects/[id]/copilot?run=<runId>`.
- **Orchestrator** — the sidebar tab, at `/orchestrator`.

They become **one** surface, called **Orchestrator**. The Copilot is removed from
the frontend.

**Keep from the Copilot:** the right-hand panel — **Artifacts**, **Activity**,
**Context** — and the chat itself.

**Do not keep:** the left-hand linear pipeline rail. There is no linearity in the
new Orchestrator. Any agent can run at any time depending on the conversation.

**Replace on the right:** the current Orchestrator's PIPELINE panel — the
stage list with SIGN-OFF badges and gate owners — is repurposed into the
Artifacts / Activity / Context tabs. There is **no sign-off and no gate** in the
Orchestrator, because the Project Admin running it already has access to every
agent.

### 1.2 Navigation

**Run agent** on the Projects page leads directly to the Orchestrator, not to the
Copilot. The button is **hidden for non-admins** — delivery roles keep working
through the agent tiles on the project Overview, which already enforce owner-only
RBAC.

### 1.3 What the Orchestrator is

A chat with every agent built so far wired into it, plus a **Context Agent**.

When the Project Admin asks for something — "I need a PRD" — the agent for that
task starts automatically, and does the task fully. The Context Agent is the
Orchestrator's brain: it reads the conversation, decides which delivery agent to
invoke, carries what earlier agents produced into the next one's prompt, and
answers directly when no delivery agent is needed.

Routing is driven by **context, not position**. The old engine advanced
requirements → design → development by list index. That is wrong and must go. The
user can jump to any agent at any time; the Orchestrator picks the right one from
what was said.

### 1.4 Artifacts

Every generated document is stored in the **Artifacts** tab for reference,
**grouped agent-wise**. Each agent's own frontend quirks carry over — for example,
when the Development agent pulls code from the connected Azure DevOps repo, that
pulled code appears under the Development heading in Artifacts.

### 1.5 Hierarchy and access

Although the Orchestrator appears in the left sidebar at the top level, **its
hierarchy is inside a project**. Every run belongs to that project and obeys all
of the project's rules: its integrations, its connectors, its allowed models.

**Only a Project Admin can use the Orchestrator**, because only that role has
access to all agents.

### 1.6 Quality bar

The frontend must be **enterprise-level**. Build the frontend properly against
these needs first, then route the agents into it.

### 1.7 The engine

**Build a new engine.** The old one had real problems: it would not switch agents
reliably, it faulted, and it did not do the work properly. The existing
1,970-line LangGraph file may be used **as a reference only**. The new engine is
built on the capabilities of the **current standalone agents**, which are the
hardened ones.

---

## 2. Architecture audit — what actually exists today

Everything in this section was verified against the code, not assumed. Evidence is
cited so it can be re-checked.

### 2.1 The Orchestrator tab is a mock

`frontend/lib/orchestrator/script.ts` declares it in its own header:

> DUMMY-DATA SEAM — the Orchestrator's stage narration. There is no backend
> (standing directive), so an orchestrated run is scripted here and revealed by
> the engine as if it were streaming.

Every agent reply on `/orchestrator` is a hardcoded string revealed on a
`setTimeout` (`CHUNK_MS = 45`, `WORDS_PER_CHUNK = 4`). A grep across
`lib/orchestrator/`, `components/orchestrator/` and `stores/orchestrator-store.ts`
finds **no network call in the chat engine at all** — only the project picker,
model picker and approvals list are real. No agent has ever run on this page.

Sessions are `localStorage` only ("Sessions are stored in this browser only").

### 2.2 The Copilot is the real engine

`frontend/lib/copilot/use-copilot.ts` (875 lines) opens an authenticated
WebSocket to FastAPI `/sdlc/agent/copilot/ws`, using a single-use 20-second Redis
ticket minted server-side by `frontend/app/api/copilot/ws-ticket/route.ts` so the
BFF JWT never reaches the browser. It streams tokens and tool calls, persists
transcripts, and loads real artifacts from `/runs/{id}/artifacts`.

`backend/agents_orchestrator/orchestrator/copilot_api.py` (2,722 lines) is the
turn loop. It lazily imports each stage's compiled LangGraph app and runs it.

### 2.3 Context-driven switching already exists — partly

`backend/agents_orchestrator/orchestrator/stage_switch.py` is a hybrid router:
deterministic verb+alias regex (`SWITCH_VERBS` × `STAGE_ALIASES`) with a cheap LLM
classifier for ambiguous turns. It is wired into the live turn loop at
`copilot_api.py:1610` and covered by `tests/copilot/test_stage_switch.py` and
`tests/copilot/test_switch_routing.py`.

It is **not** what the new Orchestrator needs, for two reasons:

1. **It only fires on alias matches.** "I need a PRD" contains no requirements
   alias, so it does not route. Maintaining the phrase list is a permanent tax.
2. **Progression is still positional.** `shared/services/orchestrator/progression.py`
   advances by `STAGE_ORDER.index(active) + 1` — list order, not meaning. This is
   the hard-coded behaviour the user called out.

### 2.4 The Project Manager agent is unreachable — a concrete instance of the reported faults

Verified by executing against the real registry:

```
STAGE_ORDER (9):  requirements design plan development code_review
                  security testing deployment documentation
dispatchable (8): requirements design ⋯ development code_review
                  security testing deployment documentation
MISSING:          plan
```

`copilot_api._graph_for()` has no branch for `plan`. The router will route
"make me a sprint plan" to `plan`, the engine finds no graph, and the failure is
swallowed by `except Exception: logger.warning(...)`. The turn produces nothing
and looks like the agent simply chose not to answer.

**This is the failure mode the new engine must make impossible.** A missing agent
must fail loudly at startup, not silently mid-conversation.

### 2.5 The Artifacts panel already does what is wanted

`frontend/components/copilot/artifacts-panel.tsx` (1,329 lines) already has the
three tabs — Artifacts, Activity, Context — and `groupByStage()` already groups
artifacts agent-wise, with the active agent's group expanded. It also special-cases
the Development stage to surface a live code tree (`code-tree-view.tsx`), which is
exactly the "pulled code appears under Development" requirement.

This panel is **reused**, not rebuilt. It is the single largest piece of finished
work on either page.

### 2.6 There are three engines, not two

| Engine | Mount | Lines | State |
|---|---|---|---|
| `copilot_api.py` | `/sdlc/agent/copilot` | 2,722 | Live. Real agents. Has the faults above. |
| `orchestrator_api.py` | `/sdlc/agent/orchestrator` | 1,970 | Own `/ws` + `/chat/`, own LangGraph handoff graph. **Reference only** per the user. |
| `lib/orchestrator/script.ts` | frontend only | 196 | Mock. Delete. |

`/sdlc/agent/orchestrator/ws` is also the **default target of the BFF `/api/chat`
route** when no `agent` is specified — so it is not dead code, and anything that
retires it must re-point that default. Flagged as a risk in Section 6.

### 2.7 Each standalone agent is already individually addressable

`frontend/app/api/chat/route.ts` maps an agent id to its own FastAPI WS path:
`/sdlc/agent/requirement/ws`, `/sdlc/agent/design/ws`, `/sdlc/agent/plan/ws`,
`/sdlc/agent/development/ws`, `/sdlc/agent/code-review/ws`,
`/sdlc/agent/security/ws`, `/sdlc/agent/testing/ws`, and so on.

These are the hardened standalone agents the new engine is to be built on. They
are in-process Python modules, so the engine can invoke their compiled graphs
directly rather than looping back through HTTP.

### 2.8 RBAC and the data model already support the requirements

- `frontend/lib/roles.ts` — `project_admin: { ...ALL_OWNER }`. The Project Admin
  already reaches every agent. No matrix change needed for Orchestrator access.
- `backend/shared/models/orm.py` — the `Run` model already carries a per-stage
  artifact column for **all nine** stages, including `plan_artifacts`, plus a
  related `Artifact` collection. One run holding many agents' output is already
  the intended shape.

---

## 3. The gap, stated plainly

The working engine is on the page to be deleted. The mock is on the page to be
kept. The routing intelligence needed is partly built but positionally bound and
alias-limited, and one of the nine agents cannot be dispatched at all.

So this work is **demolition, rewiring, and one genuinely new component** — the
Context Agent — rather than a rebuild of everything.

---

## 4. Decisions locked

| # | Decision | Source |
|---|---|---|
| D1 | Build a **new engine**. `orchestrator_api.py` is reference only. Capability base is the standalone agents. | User, direct |
| D2 | Context Agent = **router + context carrier**. Decides the agent, carries prior output forward, answers directly when no agent is needed. | User, chosen from options |
| D3 | Routing is **Method A — tool-calling router** with a thin deterministic pre-filter for unambiguous commands. | User approved |
| D4 | **Run agent** hidden for non-admins. Delivery roles keep the Overview agent tiles. | User, chosen from options |
| D5 | **No gates, no sign-off** inside the Orchestrator. | User, direct |
| D6 | Right panel = **Artifacts / Activity / Context**, reusing the Copilot panel. | User, direct |
| D7 | **No linear pipeline rail.** No positional progression anywhere. | User, direct |
| D8 | Orchestrator is **project-scoped**; Project Admin only. | User, direct |
| D9 | Frontend built first, but the **streaming event contract is pinned before either side**. | Claude proposed, user approved |
| D10 | **Borrow capabilities, not whole agents.** The Orchestrator gets its own per-agent implementation built from the standalone agent's tools + prompt; it does not run their compiled graphs, because their permission/gate/session machinery is exactly what the Orchestrator does not need. | User, direct |

---

## 5. Design

### 5.1 Routing core — the Context Agent

An LLM holding **one tool per delivery agent**: `run_requirements`, `run_design`,
`run_plan`, `run_development`, `run_code_review`, `run_security`, `run_testing`,
`run_deployment`, `run_documentation`.

Per turn it receives:

- the conversation so far,
- a compact summary of what the project has already produced (which agents have
  run, what artifacts exist),
- the project's available connectors and models.

It then either answers directly, or calls exactly one agent tool with a
hand-off payload. "I need a PRD" reaches Requirements because the model
understands the request — no alias table involved.

**Thin deterministic pre-filter.** An unambiguous imperative naming an agent
("run the testing agent") short-circuits straight to dispatch with no model call:
faster, free, and predictable. Everything else goes to the router. This keeps
Method B's reliability on the easy cases without inheriting its ceiling.

**No positional progression.** There is no `next_stage()` in this engine. Every
turn considers all nine agents. Sequence is an outcome of the conversation, never
of list order.

### 5.2 Capability registry — failing loudly

**D10 — borrow capabilities, not whole agents.** The Orchestrator does **not**
import and run the standalone agents' compiled graphs. It has its own
implementation per agent, assembled from what the standalone agent is *good at*
— its tools, its prompt, its output shape — and nothing else.

The reason is that a standalone agent is more than its capability. Each one
carries the machinery of being reachable on its own: per-role permission checks,
its own session and gate handling, its own approval routing. Inside the
Orchestrator none of that applies — the runner is a Project Admin who already
owns all nine agents, there are no gates (D5), and the only scope that matters is
*which project is this*. Dragging that machinery in means re-deciding, per turn,
questions that were already settled at the door, and it is a large part of what
made the old engine fragile.

So the registry maps agent id → **orchestrator-native implementation**, built
from the standalone agent's tools + prompt, plus the system prompt, required
connectors, and artifact writer. The standalone agents keep working unchanged for
the delivery roles on their own pages; the Orchestrator shares their capability,
not their plumbing.

The trade-off, stated plainly: one capability now has two call sites, so a tool
improved in the standalone agent does not automatically improve the Orchestrator's.
The mitigation is that **tools and prompts are shared modules** — only the graph
assembly and the permission wrapper differ. If a capability starts drifting, that
is the signal it was not factored far enough down.

At **startup**, the registry is validated: every agent in `STAGE_ORDER` must
resolve to an importable graph and a prompt. A missing entry **refuses the boot**,
in the same spirit as the existing `RbacCatalogDriftError` guard.

This converts §2.4's silent `plan` gap into a startup failure that cannot ship.
The router's tool list is generated *from* this registry, so the router
mathematically cannot offer an agent the engine cannot run.

### 5.3 Session and run model

One Orchestrator conversation = **one `Run` row**, scoped to the project.

- Many agents write into the same run — the `Run` model already has all nine
  per-stage artifact columns for exactly this.
- `current_stage` records the agent that ran most recently. It is a **record, not
  a pointer**: nothing reads it to decide what runs next.
- `gate_pending` stays `false` for Orchestrator runs (D5).
- Sessions move **server-side**. The current `localStorage` rail cannot satisfy
  "every run belongs to the project", survives no device change, and is invisible
  to cost and audit.

### 5.4 Context propagation

The Context Agent builds the hand-off payload from the run's existing artifact
columns before invoking an agent — the Design agent receives the Requirements
output, Development receives Design, and so on — regardless of the order the user
asked for them in. `config/pipeline_context.build_pipeline_context` already exists
for this and is the starting point.

### 5.5 Streaming event contract (pinned before implementation)

The socket emits a closed set of typed events. Draft, to be finalised as the first
build step:

| Event | Meaning |
|---|---|
| `token` | A chunk of assistant text. |
| `agent.selected` | The router chose an agent; carries agent id + one-line reason. |
| `tool.call` / `tool.result` | Feeds the Activity tab. |
| `artifact.created` / `artifact.updated` | Feeds the Artifacts tab; carries the owning agent. |
| `turn.end` | Turn complete. |
| `error` | Typed failure, surfaced in the UI rather than swallowed. |

`error` is deliberate: §2.4's bug survived because failures were logged and
dropped. The Orchestrator surfaces them.

### 5.6 Access control

Route and page gate on **Project Admin for the selected project**, enforced
server-side on the socket as well as in the UI. Sidebar entry stays visible for
users who hold Project Admin on at least one project, hidden otherwise. Governance
roles (`org_admin`, `bu_admin`) remain excluded — they have no agent access at all.

### 5.7 Frontend structure

```
Orchestrator (project-scoped)
├── Session rail        sessions for THIS project, server-backed
├── Header              project · model · status        (no Run pipeline button)
├── Thread              chat; agent switches shown inline as they happen
└── Right panel         Artifacts | Activity | Context   (reused from Copilot)
```

Removed: the linear pipeline rail, the PIPELINE/sign-off panel, Auto-advance, and
the "Run pipeline" button — every one of which asserts a linearity that no longer
exists.

### 5.8 What gets deleted

- `frontend/lib/orchestrator/script.ts` — the mock engine.
- `frontend/lib/orchestrator/use-orchestrator.ts` — the `setTimeout` reveal loop.
- `frontend/app/(app)/projects/[id]/copilot/page.tsx` — the Copilot page shell.
- `components/orchestrator/stage-rail.tsx`, `components/copilot/pipeline-rail.tsx`,
  `components/copilot/gate-inline.tsx` — linearity and gates.

Kept and moved: `components/copilot/artifacts-panel.tsx`, `artifact-viewer.tsx`,
`code-tree-view.tsx`, `choice-card.tsx`.

---

## 6. Risks

| Risk | Why it matters | Handling |
|---|---|---|
| `/api/chat` defaults to `/sdlc/agent/orchestrator/ws` | Retiring the old engine silently breaks every per-agent chat that omits `agent`. | Re-point the default before touching that engine; assert in a test. |
| Nine heterogeneous agent state schemas | The main source of mess in the old LangGraph file. | Registry owns the adapter per agent; agents are not modified. |
| Reused Copilot panel expects run-shaped props | Silent empty panels. | Keep the run contract; adapt at the boundary, not inside the panel. |
| Router picks a wrong agent | Expensive and confusing. | `agent.selected` states the choice and its reason in the thread; the user can redirect in one turn. |

---

## 7. Open questions

1. ~~**Nine agents or eight?**~~ **SETTLED — nine.** The ninth is the **Project
   Manager agent** (formerly the "help agent"), newly built. It is in scope and is
   called the **Project Manager agent** everywhere — never "Plan agent", never "PM
   agent" in user-facing text. Its registry id remains `plan`; that is an internal
   identifier and stays as-is. §2.4's dispatch gap is therefore a **must-fix**, not
   a curiosity: it is the reason this agent has never actually run in an
   orchestrated conversation.
2. **Does a delivery agent invoked by the Orchestrator still record an approval?**
   D5 removes gates from the Orchestrator, but the platform's approvals system is
   separate. Assumed **no approval rows** are created by Orchestrator runs.
3. **Cost attribution** across agents in one run — assumed already handled by
   existing per-run cost tracking; to verify.

---

## 8. Procedure

Phases are ordered so nothing is built against an unproven interface.

- **Phase 0 — Contract.** Finalise §5.5's event set and the capability registry
  shape. Re-point `/api/chat`'s default off the old engine. One page of spec, no UI.
- **Phase 1 — Frontend.** Build the merged Orchestrator against the pinned
  contract, with the reused right panel. Delete the **mock** engine. Move "Run
  agent" and gate it to Project Admin.

  **Sequencing correction (made while planning).** An earlier draft deleted the
  Copilot *page* here too. That is wrong: the new engine does not exist until
  Phase 3, and the Copilot is currently the only surface that actually runs
  agents. Deleting it in Phase 1 would leave the product with no working
  orchestration for the length of the backend work — immediately visible in a
  demo. Phase 1 therefore **unlinks** the Copilot (nothing navigates to it) but
  leaves the route reachable; it is deleted in Phase 5, once the new engine is
  proven. The mock engine still goes in Phase 1, because it runs nothing and
  keeping it invites the new UI being wired back to fake output.
- **Phase 2 — Registry + dispatch.** Build the capability registry with its
  startup validation, and in-process dispatch to all nine standalone agents. This
  is where the `plan` gap is closed permanently.
- **Phase 3 — Context Agent.** The tool-calling router with the deterministic
  pre-filter, plus context propagation from the run's artifact columns.
- **Phase 4 — Artifacts + per-agent quirks.** Agent-wise storage; the Development
  code tree; anything else each agent surfaces.
- **Phase 5 — Retire the old engines.** Remove `copilot_api.py` and
  `orchestrator_api.py` once nothing depends on them.

---

## 9. Progress log

| Date | Entry |
|---|---|
| 2026-09-05 | Architecture audited (§2). Decisions D1–D9 locked. Document created. No code written yet. |
| 2026-09-05 | Open question 1 settled: **nine** agents, the ninth being the **Project Manager agent** (formerly "help agent"). User approved the design and authorised the build. Implementation plan next. |
| 2026-09-05 | Phase 0+1 plan written: `docs/superpowers/plans/2026-09-05-orchestrator-phase-0-1.md` (9 tasks, TDD). Sequencing corrected in §8 — the Copilot page survives Phase 1 and is deleted in Phase 5, so there is never a window with no working orchestration. Protocol decided as *Copilot protocol minus gates, plus `agent.selected`*, reusing the existing Zod union rather than inventing one. |
| 2026-09-05 | **D10 added** — the Orchestrator gets its own per-agent implementations built from the standalone agents' tools and prompts, rather than running their compiled graphs. Their permission/gate/session machinery is precisely what the Orchestrator does not need. Affects Phases 2-3 only; Phase 1 (frontend) is unchanged and continues. |
| 2026-09-05 | **PHASE 1 COMPLETE.** Branch `feature/orchestrator-rebuild`, 18 commits, 34 files, +2203/−1306, 601 tests passing, typecheck clean. Not pushed. Details in §10. |
| 2026-09-06 | **PHASE 4 COMPLETE.** Deliverables: their own table, append-only, no approval concept. Design at `docs/superpowers/specs/2026-09-06-orchestrator-phase-4-deliverables-design.md`, plan at `docs/superpowers/plans/2026-09-06-orchestrator-phase-4.md`. Decisions D11–D17. Backend 397 tests, frontend 669, typecheck and lint clean, zero regressions against the pre-phase baseline. Details in §15. |
| 2026-09-07 | **PHASE 5 COMPLETE.** Both old engines retired; the Orchestrator's rail is server-backed history. Design at `docs/superpowers/specs/2026-09-07-orchestrator-phase-5-design.md`, plan at `docs/superpowers/plans/2026-09-07-orchestrator-phase-5.md`. Decisions D18–D24. Backend 3,322 passing at the recorded baseline (22 failed / 7 errors, all pre-existing); frontend 688 passing, typecheck, lint and `next build` clean. Details in §16. |
| 2026-09-07 | **CARRIED DEBT CLOSED.** Six of seven open items done: §1.5 self-approval reinstated, the BYOK env fallback made opt-in, all WebSocket sockets audited (four unauthenticated ones deleted), context truncation fixed, the e2e suite rewritten and passing. RLS (#1) is one `ALTER ROLE` away and needs the operator. Details in §17. |

---

## 10. Phase 1 outcome (2026-09-05)

### What shipped

The Orchestrator is now one project-scoped, Project-Admin-only surface. The mock engine is
deleted, all pipeline linearity and gates are gone, the Artifacts/Activity/Context panel has
moved out of the Copilot directory so retiring the Copilot later cannot take it down, and
"Run agent" opens the Orchestrator for a Project Admin and is hidden for everyone else.

The composer is deliberately **disabled** and says the engine arrives in the next phase.
That honesty is the point: the previous page looked finished while running nothing, and an
obviously-empty shell is better than a convincing fake.

The Copilot page still runs agents and is still reachable from run history. Nothing that
worked before this branch has stopped working.

### The bug this phase existed to prevent, found again

The final whole-branch review found that **`/projects/[id]/orchestrator` had no access check
at all**. Only `artifact:view` gated it, which every delivery role holds — so a BA, developer,
QA, or even a `bu_admin` (a tier with no agent access whatsoever) reached the full Orchestrator
by typing the URL. Three of the four surfaces had been gated; that one had not.

No per-task review could have caught it: each reviews a *diff*, and no task touched that file.
It is the argument for running a whole-branch review at all.

Two more of the same shape — edges left by otherwise-correct deletions:
- the Context tab still rendered a "Who approves" section, falling back to an invented
  "Product Manager" / "Approval-required gate" when no gate existed;
- `thread.tsx` still rendered **Approve & continue / Reject** buttons wired to a no-op, and
  `localStorage` sessions written by the deleted mock still carried gate messages. The writer
  was deleted in one commit; the reader survived in another.

All fixed, plus one regression the fix wave itself introduced (see R14).

### Rulings taken without asking

| # | Ruling | Cost if wrong |
|---|---|---|
| R1 | New branch `feature/orchestrator-rebuild`, not the RBAC branch with PR #37 open | Merge order matters; rebase is cheap |
| R2 | Nav test imports `deliverNav` — `NAV_ITEMS` never existed | None, verified |
| R3 | Added `requirePlatformRole` rather than repurposing the dead `requireRole` | One extra optional field |
| R4 | Replaced only the header "Run agent"; left the empty-state control | Checked: nothing else opened that dialog |
| R5 | Role from `effectivePlatformRole(session)`; explicit prop for testability | None material |
| R6 | Tasks 5+6 dispatched together (shared file) | Larger review surface |
| R7 | Batched tasks 1+2+3 and 7+8 | Larger diff per review |
| **R8** | **Deleted `cockpit.tsx`'s competing `canDrive` rule** (membership + agent reach), which admitted BA/Developer/QA. It was a documented earlier decision arguing *against* a Project-Admin binding; the spec supersedes it | Delivery roles lose an Orchestrator view; they keep their own agent pages, which is where §1.2 sends them |
| R9 | Deferred the e2e rewrite (user's call); skipped the obsolete spec with a comment rather than deleting it or leaving it permanently red | No browser-level proof this phase |
| **R10** | **Kept two Copilot links** (`runs/page.tsx:283`, `project-runs-table.tsx:99`) — they open past runs from history, and nothing replaces that until Phase 3 | Copilot reachable one phase longer than §1.2 literally reads |
| R11 | "Paused at a gate" → "Paused" | None |
| R12 | *Superseded* — Opus limit reset, so the final review ran on Opus as intended | — |
| R13 | Stale gate messages fixed by **stripping the reader**, not migrating the store — no writer remains, so a migration would preserve a shape nothing can produce again | Old sessions lose badge styling on already-orphaned data |
| **R14** | **Fixed the fix wave's own regression immediately** rather than parking it per convention: the Copilot's `gate` is `null` for most of a run, so gating the approver section on `gate` alone silently removed it from a live page | One extra commit |

### Deliberately not done in Phase 1

Server-backed sessions, the capability registry, the Context Agent, real agent dispatch,
deleting the Copilot route, and the e2e rewrite. Phases 2–5.

---

## 11. Phase 2 research — a finding that refines D10 (2026-09-05)

D10 says: borrow the standalone agents' capabilities, not the whole agents, because they
carry permission machinery the Orchestrator does not need. Checking where that machinery
actually lives changes what "rebuild" has to mean.

**Measured, by counting permission/session/gate patterns per file:**

| Layer | Hits |
|---|---|
| `requirements_agent_api.py` | 5 |
| `development_agent_api.py` | 3 |
| `pm_agent_api.py` | 3 |
| `development_agent/agents/dev_agent.py` (graph) | 0 |
| `pm_agent/agents/schedule.py` (graph) | 0 |
| `code_review_agent/agents/reviewer.py` (graph) | 0 |
| `design_architecture_agent/agents/architecture.py` (graph) | 1 |
| `requirements_agent/agents/planning.py` (graph) | 3 |

**The machinery is in the API wrappers, not the graphs.** Those wrappers are the
`*_agent_api.py` files that make each agent reachable on its own — WS tickets, per-role
permission checks, session handling. The Orchestrator would never call them anyway, so
D10's goal is achieved for free by not using them.

**What the few graph hits actually are** — and this matters:

- `shared/authz/consequential.authorize_consequential` (requirements, design)
- `shared/authz/connector_access.permits` (requirements)

Neither is role/RBAC machinery. `authorize_consequential` guards **real-world side effects** —
writing to Azure DevOps, pushing a branch — and `connector_access` checks a connector is
permitted. These are exactly the guards that should SURVIVE into the Orchestrator: a Project
Admin having every agent does not mean an agent should silently push to a repo unasked.

**Consequence for Phase 2.** Rebuilding all nine graphs from tools + prompts is a large,
drift-prone effort whose stated justification (shedding permission machinery) is mostly
already satisfied by skipping the API layer — and doing it carelessly would drop the
consequential-action guard, which is a safety regression, not a simplification.

### 11.1 D10 revised — D10a (user decision, 2026-09-05)

**Reuse the compiled graphs; never touch the `*_agent_api.py` wrappers.**

This supersedes D10's "build our own per-agent implementation". D10's *goal* stands
unchanged — the Orchestrator must not inherit the machinery of an agent being reachable on
its own — but the measurement above shows that machinery lives in the wrappers, so skipping
them achieves the goal at zero cost.

What this buys over rebuilding:

- **One capability, one implementation.** The drift risk D10 itself flagged ("a tool improved
  in the standalone agent does not automatically improve the Orchestrator's") disappears
  entirely, rather than being mitigated.
- **The side-effect guards survive.** `authorize_consequential` and `connector_access` stay
  where they are, so an agent still cannot push to a repo without passing the same check it
  passes today. A hand-rolled rebuild would have had to re-import both deliberately, and
  forgetting either is a silent safety regression.
- **Nine fewer implementations to write and keep in sync.**

What the Orchestrator still owns, and does NOT take from the standalone agents:
its own routing (the Context Agent), its own session/run handling, its own streaming
protocol, and its own registry with startup validation. The agents supply capability; the
Orchestrator supplies everything about being an orchestrator.

**Cost if wrong:** if some agent's graph turns out to depend on state its wrapper used to
provide, that agent needs an adapter — or, in the worst case, a native rebuild after all.
The registry is the natural place for such an adapter, so this stays reversible per agent
rather than being an all-or-nothing bet.

### 11.2 Why the old engine "would not properly do the stuff" — measured

§2.4 recorded that the Project Manager agent had no graph in `copilot_api._graph_for()`.
The prompt side is worse, and it explains the reported symptom directly.

`copilot_api._system_prompt_for()` handles **three** stages. Verified by executing against
the real registry:

```
stages WITH a system prompt:               requirements, design, development
stages WITHOUT (run with no instructions): plan, code_review, security, testing,
                                           deployment, documentation
```

The function's own docstring states the consequence:

> Without this prompt the stage agent has no instructions and will churn without producing
> a useful reply.

So **six of the nine agents ran with no instructions at all**, and the ninth (`plan`) had
neither a graph nor a prompt. Only requirements, design and development were ever fully
wired. Both gaps fail soft — `except Exception: logger.warning(...)` and a `None` return —
so the symptom was an agent that answered vaguely or not at all, never an error anyone
could chase.

This is not a subtlety to fix later. It is the single largest cause of the behaviour that
prompted this rebuild, and it is the reason the Phase 2 registry validates **graph AND
prompt for all nine agents at startup, refusing to boot if either is missing**. A missing
capability must be impossible to ship, not something a user discovers by being ignored.

---

## 12. Phase 2 outcome — the engine exists and all nine agents resolve

Phase 2 built `backend/agents_orchestrator/orchestrator2/` as a NEW package rather than
extending `copilot_api.py` (P2-R1). The old engine keeps running untouched; it is retired
in Phase 5, once this one is proven.

**What shipped**

- `registry.py` — the nine agents, each with a lazily-loaded graph and prompt, plus
  `validate_registry()`. The direct answer to §11.2: a missing graph or prompt now breaks
  the import instead of producing an agent that answers vaguely. `AGENT_IDS` is derived
  from `STAGE_ORDER` and asserted equal to it at import, so the two cannot drift.
  Verified by EXECUTION, not by inspection — every one of the nine resolves a real graph,
  and all eight stream-mode agents resolve a real prompt (requirements 25,844 chars,
  design 20,553, plan 6,616, development 31,719, code_review 3,854, security 4,380,
  deployment 12,277, documentation 8,035). `testing` is invoke-mode and takes none
  (P2-R4).
- `dispatch.py` — `run_agent(...)`, one agent per turn, streaming protocol events.
- `ws.py` — the authenticated socket. Project Admin only, checked **before** the handshake
  is accepted.

**BYOK is project-scoped, as you asked mid-phase.** The turn's project comes from the
verified `runs` row and never from the client frame — a client-named project would let a
caller borrow another project's model grant and another project's budget. `run_agent`
takes `project_id` as a keyword with **no default**, deliberately: a default of `None`
would let a future call site drop project scoping silently, which is the exact bug that
was being fixed.

**Two defects found in Phase 2 that were not in the plan**

1. `_run_model_offering` (copied from the old engine) read runs through a session that
   **bypasses row-level security**, with no tenant filter — so a Project Admin in one
   tenant could name a run in another and read its model selection. The new
   `_resolve_run` runs under the caller's tenant AND carries an explicit tenant predicate,
   because neither should be the only thing standing between tenants.
2. An `error` event announcing an unknown agent was **undeliverable**. The backend put the
   unresolved id in a field the frontend types as an enum of the nine valid ids, so the
   browser's validation dropped the frame — the one event whose entire job is to say "that
   agent does not exist" was the one guaranteed not to arrive. The trap was in the Phase 1
   protocol design, and it was fixed on the backend rather than by widening the contract.

**Deferred out of Phase 2, deliberately, and all now closed or scheduled**: `tool.call`
emission (Phase 3 Task 4), the per-project access check (Phase 3 Task 5), and two minor
residuals carried to the phase review (P2-R9).

---

## 13. Phase 3 — routing, context, and the last access gap

The phase that makes the Orchestrator behave the way you described: *"Any agent can come at
any time according to the chat."*

**Task 1 — the pre-filter** (done). An explicit imperative naming an agent ("run the
security agent") is answered with **no model call at all**, and the reason shown says so.
It requires a VERB followed by a NAME — the old `stage_switch.py` matched an agent alias
**anywhere** in the text, which is why "I need a PRD" routed nowhere while an incidental
mention of a name could hijack a turn.

**Task 2 — the Context Agent** (done). Everything the pre-filter does not answer goes to a
model that reads the message for MEANING and picks one of the nine, or answers directly.
**All nine are candidates on every turn** — there is no `STAGE_ORDER.index(active) + 1`, no
notion of a next agent, nothing about what ran before. A hallucinated agent id is refused
visibly rather than dropped. The router makes its own model call, and that call is
project-scoped like every other, so BYOK holds on this path too.

**Task 3 — context propagation** (in progress). Reading the old engine's version of this
function while briefing the work confirmed your complaint had a second cause nobody had
named: `_upstream_context` slices the run's artifacts by **pipeline position**
(`STAGE_ORDER[:idx]`). Run Design after Development and Design is shown **nothing** about
the development work. The linearity was in the context layer as well as the routing layer,
so removing hardcoded routing alone would not have fixed it. The replacement includes
everything that exists on the run, labelled by the agent that produced it, ordered by
nothing. That same old function also has the RLS-bypassing read described in §12, and
returns `""` on any failure — which reaches the agent as "no prior work exists", so it
re-asks you for work already done.

**Task 4 — wiring** (next). Routing into the socket; an explicit agent choice still
overrides the router (an LLM will sometimes be wrong, and with no override a wrong decision
is unrecoverable inside the conversation); `tool.call` events to the Activity tab. Two live
defects found while briefing it, both invisible to the current tests because they all fake
the graph: streamed content that arrives as a **list of typed blocks** — what Anthropic
actually returns — fails the frontend's validation and is dropped, so the user watches an
agent produce nothing; and tool OUTPUT is currently streamed to the user as if it were the
agent's own prose.

**Task 5 — the last access gap** (after Task 4). The socket asks "are you a Project Admin
*somewhere* in this tenant". Routing makes that load-bearing: a Project Admin of project A
could name a run belonging to project B in the same tenant and drive all nine agents
against it, on B's BYOK grant and B's budget. Plus the frontend change that makes the agent
picker an **optional override** defaulting to "Let the Orchestrator choose", which is the
visible half of everything above.

**Standing method.** Every task is implemented by one agent and reviewed by another, and no
fix is accepted on "the tests pass" — the implementation is deliberately broken to confirm
the test actually fails. That standard was adopted because it kept catching things: three
separate rounds this phase found tests that passed against a knowingly broken
implementation, including one change I committed myself on a green suite that turned out to
have no test covering it at all.

**Not in Phase 3**: server-backed sessions, artifact persistence from the new engine,
retiring the old engines (Phase 5), and the e2e rewrite you deferred in Phase 1.

---

## 14. Phase 3 closed out — what the blockers found

Two things stood between Phase 3 and Phase 4: an independent review of the whole branch,
and the fact that **nothing in this rebuild had ever run against a real model**.

### 14.1 The engine works, and measuring it changed the prompt

Every test to this point fakes model resolution, the LLM client and the agent graph. That
proves the plumbing — project-scoped BYOK, tool binding, message assembly, the event
contract — and says exactly nothing about whether the router sends "I need a PRD" to
Requirements. That was named as the biggest open item when the router was built, and it
stayed open until now.

Run against the seeded tenant's own BYOK provider, project-scoped, through the same
`route()` the socket calls:

| | correct |
|---|---|
| before | 11 / 16 |
| after | **16 / 16**, plus **7 / 7** held-out |

**Your headline requirement passed on the first run, before any tuning:** "I need a PRD
for the billing rework" started the Requirements agent, with no menu and no agent named.

All five failures were the same two prompt defects, and neither was visible to any unit
test:

1. **The router refused to route when a request lacked specifics.** "I'd be happy to help
   you design the architecture, but I don't have context on what service." The admin
   asked for work and got a question back, and no agent ran — a direct contradiction of
   the requirement that the right agent starts automatically and does the task fully.
   Gathering those specifics is the agent's own first job, and it can ask far better than
   a router can.
2. **"Answer directly when the message is a question" was far too broad.** It caught
   questions whose ANSWER IS an agent's work product: "are there any injection risks
   here?" is Security's output, "who is working on what, and when does this land?" is the
   Project Manager's.

The obvious way to over-correct a fix for under-routing is to make the thing route
everything, so the harness carries a **held-out set that informed none of the wording** —
six messages an agent must NOT be started for. All seven pass; "hello" and "what can you
do?" still answer directly. That is the result that makes the sixteen worth anything.

`backend/scripts/live_routing_check.py` is kept, because this is the only way to measure
routing quality and it will be needed again every time the prompt or the capability
descriptions change.

### 14.2 The review found one Critical, and it was in the UI, not the engine

Switching project on the global Orchestrator ran the **next turn against the previous
project's run**. The reset that clears the run and the transcript was keyed on the
session, and switching project repoints a session in place while keeping its id — so the
header, the model picker, the artifacts panel and the rail all changed to the new project
while the run did not. The turn then spent the OLD project's budget with its BYOK key and
joined its conversation thread, under a header naming the new one.

Nobody's permissions were exceeded. The damage is that the screen and the run disagreed
about which project was being worked on — misattributed spend, and a conversation filed
against the wrong project. The backend had been given an entire per-turn project check to
make exactly that impossible; the frontend was handing it over by accident.

Three further defects were tests that could not fail — including one that restored the
exact bug the commit it belonged to was named after, and left all 638 tests green.

### 14.3 One finding was mine, and it is worth recording as the pattern

A commit message of mine claimed "every docstring making the Phase 2 claim was rewritten".
I had changed one inline comment; the two most prominent instances were still on disk,
including one asserting "Nothing sends until an agent is named" — the exact line the code
no longer enforced.

That is the **eighth** instance in this rebuild of the same defect: prose asserting a
guarantee the code does not provide. The earlier ones were comments; one was a comment AND
a test certifying a silent data loss as deliberate; this one was a commit message
certifying a cleanup that had not happened. It is the reason every task on this branch is
reviewed by someone other than its author, and the reason the standard of proof is
"break the code and watch the test fail" rather than "the suite is green".

---

## 15. Phase 4 outcome — Deliverables (2026-09-06)

Requirement 7 of your original message: *"All the generated documents are stored on the
artifact tab for reference… in the artifacts tab, there are headings agent-wise."*

### 15.1 The distinction you drew, and what it changed

Asked where Orchestrator output should live, you said the existing `artifacts` table
belongs to the standalone agents — *"that is why it needs approval"* — and that the
Orchestrator's should be **a separate thing with a different name**.

That is the right cut, and it settled more than storage. An `artifacts` row carries
`approval_status` **because** a standalone agent wrote it and a human accepts it. The
Orchestrator has no gates (D5) and is driven by a Project Admin who already owns all
nine agents, so reusing that table would have dragged an approval concept into a
surface the spec says has none. Orchestrator output is now a **Deliverable**, in
`orchestrator_deliverables`, with no approval column anywhere in its shape — on the
table, in the ORM model, or on the wire. Three tests assert the absence.

It is the same line D10a already drew in the engine, where the Orchestrator reuses the
agents' compiled graphs and never their `*_agent_api.py` wrappers.

### 15.2 Decisions

| # | Decision | Source |
|---|---|---|
| D11 | Orchestrator output is a **Deliverable**, stored separately, never gated. | User, direct |
| D12 | **Every version is kept**, newest first. A re-run never destroys the earlier document. | User, chosen from options |
| D13 | **Only the latest per agent feeds context**, so nothing downstream is handed two contradictory PRDs. | User, chosen from options |
| D14 | Its **own table**, not the run's JSONB columns — versioning is a query, not a whole-blob rewrite that races. | User, chosen from options |
| D15 | Only `deliverable.ready` is emitted; no open/delta/end trio. Text already streams to chat. | Claude, YAGNI |
| D16 | The schema lives in `lib/orchestrator/`, not `lib/copilot/`, which Phase 5 deletes. | Claude |
| D17 | The shared panel is adapted at the boundary; its tab name is a prop defaulting to `"Artifacts"` so the live Copilot is untouched. | Claude |

**One correction made while planning:** pointers (the Development code tree, per-agent
file trees, the PR link) are **synthesized on read, never stored**. They reference state
that already lives elsewhere, so a stored row would stack a duplicate on every turn —
and their ids must stay stable anyway, because the panel de-dupes the tree on the
literal id `dev-code`.

### 15.3 A gap this closed that was not in the brief

`sections_from_run` had no `plan` branch. `plan_artifacts` was written by nothing and
read by nothing, so the **Project Manager agent's output rendered nowhere** — the same
shape as the §2.4 dispatch gap that prompted this rebuild, sitting one layer further
out. It is now handled like any other agent, and that it needed a special case at all
was the bug.

### 15.4 Proof

Three things had never been done before this phase, and all three now have been:

- **The real database.** Every Phase 4 unit test fakes the session, which proves the SQL
  is assembled correctly and nothing about whether Postgres accepts it.
  `scripts/live_deliverables_check.py` runs 16 checks against the seeded tenant — all
  pass, including that the database itself refuses an agent id outside the nine.
- **A real agent turn.** Every dispatch test fakes the graph. Running the **Project
  Manager agent** for real, on the project's own BYOK grant:
  `agent.selected -> stream_chunk -> tool.call -> deliverable.ready -> stream_end`, ready
  before `stream_end` as designed, 1,368 characters persisted, stored content identical
  to what the user saw, and it reached the next agent's context under its proper name.
- **A regression baseline.** The full backend suite has 22 failures. Running the same
  subset in a worktree at the pre-phase commit gives an **identical set** — all
  pre-existing (RLS inert per carried debt 1, plus two live-E2E fixtures failing on
  `invalid UUID 'test-tenant'`). Zero regressions.

### 15.5 What the mutation testing caught this phase

Six survivors, each a test that passed against knowingly broken code:

1. **`ORDER BY` flipped to ascending** — all 13 store tests stayed green, because the
   fake session sorted rows itself. Not cosmetic: `latest_per_agent` takes the FIRST row
   per agent, so an ascending read would have fed every downstream agent the **oldest**
   version of a document, silently.
2. **Bypassing `_get_run_or_404`** in the new REST endpoint — the source-inspection test
   grepped raw source, and the endpoint's own **docstring** names that function. The
   prose satisfied the check the code had stopped satisfying. Assertions now run against
   docstring-stripped source.
3. **The mutation hit the wrong line.** `run = await _get_run_or_404(...)` appears
   **fifteen times** in `runs.py`, so `replace(..., 1)` mutated a different route. "The
   file changed" is not "the line I meant changed" — the harness now asserts anchor
   uniqueness and probes the function under test.
4. **`created_at` tightened to required** broke nothing for a stored row, which always
   has a timestamp, while silently dropping every **pointer** — taking the Development
   code tree with it.
5. **`runId=""`, `activeStage=""` and `artifacts={[]}`** — reverting all three left the
   entire 83-test orchestrator suite green. These three literals *are* the requirement.
   Rendering the panel proves the panel works; only capturing its props proves the
   cockpit feeds it. This is the same failure the Activity tab had.

Two harness lessons, both of which produced a false SURVIVED before being fixed: print
`sys.executable` (a bare `python` heredoc uses the SYSTEM interpreter), and
`git diff --numstat` proves nothing for an **untracked** file — content length and
anchor-absence checks do.

### 15.6 Carried debt, unchanged

Context truncation (~2,400 characters per artifact on a full nine-agent run) is
**untouched** and still needs a product decision — it is recorded here so this phase is
not mistaken for having addressed it. RLS remains inert pending a non-superuser
application role. Phase 5 (retiring `copilot_api.py` and `orchestrator_api.py`) and the
deferred e2e rewrite are unchanged.

---

## 16. Phase 5 outcome — retirement, and real history (2026-09-07)

Phase 5 was specified as one thing — remove the two old engines — and grew a second when
the scope was discussed:

> *"in the orchestrator, on the left side, there is a history of all the chats. Opening
> that history will open up that full chat that the person had, and he can continue that
> chat also."*

That is §5.3, deferred in Phase 1 and never resumed. The two belong together: the Copilot
is deleted because the Orchestrator replaces it, and until the Orchestrator could reopen a
past conversation that replacement was not true — the Copilot page was the ONLY surface
that could open a historical run, which is why ruling R10 left two links pointing at it.
Shipping the retirement alone would have taken a capability away.

### 16.1 What went

The whole `agents_orchestrator/orchestrator/` package: `copilot_api.py` (2,722 lines),
`orchestrator_api.py` (1,970, reference-only from the start), and `copilot_cards.py` and
`stage_switch.py`, whose only importers were inside the deleted set. Plus the two mounts,
the three `/runs/{id}/copilot/*` endpoints, `tests/copilot/` (22 files), the Copilot page,
its four components, its WS-ticket route and its three BFF proxies. Net **-7,633 lines**
in the retirement commit alone.

`copilot_api` gave a system prompt to three of nine agents and had no `plan` branch at
all, both failing soft. It survived Phases 1-4 deliberately: until Phase 4 it was the only
surface that actually ran agents.

### 16.2 Decisions

| # | Decision | Source |
|---|---|---|
| D18 | Retire both engines, the Copilot page, its components, its WS-ticket route and the three `/runs/{id}/copilot/*` endpoints. | Spec §8, user |
| D19 | The four `lib/copilot/` modules the Orchestrator depends on MOVE to `lib/orchestrator/` rather than being deleted or stranded. | Claude |
| D20 | `/api/chat` refuses an unmapped agent (400) rather than routing it anywhere. | Claude |
| D21 | The `/runs` page points at the read-only `/runs/[id]/conversation`. History is for reading; the Orchestrator rail is for continuing. | User |
| D22 | **Orchestrator sessions are server-backed**, keyed `session_id == run_id`. | User, direct |
| D23 | A new chat is not persisted until its first turn. | Claude |
| D24 | The WS frame-size limit and the sweep's WS blindness are fixed here. | User, chosen from options |

### 16.3 The ordering that made it safe

`lib/copilot/` could not simply be deleted: **seven** Orchestrator modules imported from
it. Those moved FIRST, in their own commit, with a guard test forbidding the Orchestrator
from importing that directory again — proven by reintroducing one and watching it fail.
Without that ordering, deleting the Copilot would have broken the surface replacing it, at
RENDER time rather than build time, because a missing module still typechecks until
something resolves it.

Every deletion task therefore ended with a real `next build`, not only `vitest` and `tsc`.
That is the one failure mode unit tests structurally cannot catch here. Final proof: the
build's route table contains **zero** copilot routes, and `/api/runs/[id]/deliverables` is
present.

### 16.4 Real history

The rail was `zustand` + localStorage and said so on screen. Almost none of the fix was
new: `conversation_service` is complete and every standalone agent already uses it, while
`orchestrator2` only READ from it and never wrote. Wiring the writer, plus pointing the
rail at the server, was the whole job.

`session_id == run_id` is what makes continuing work — reopening a chat adopts that run,
so the next turn rejoins its LangGraph thread, its Deliverables and its project scope with
no mapping anywhere.

Two defects the wiring exposed, neither visible in tests until the behaviour was driven:

1. Selecting a saved chat left `active` null (a server chat is not in the local store) and
   `projectId` reads through `active` — so **the project silently became null the moment a
   chat was opened**, disabling the composer on the conversation just requested.
2. `handleProjectChange` asked `active.messages.length === 0` to mean "untouched, safe to
   repoint", which is unknowable once transcripts leave the browser. It now asks whether
   the session has a RUN — the truer question, since a chat that has run has Deliverables
   and a thread bound to its old project.

### 16.5 What the deletion uncovered

- **§1.5 is now enforced nowhere.** "Whoever ran the agent is never the one who accepts
  its own output" lived in exactly one place, `copilot_api._handle_gate_decision`, and was
  tested in exactly one file. `record_approval` checks the stage's approve PERMISSION but
  never whether the approver started the run; the artifact approve/reject path checks
  neither. `runs.created_by` (migration 0038, added to serve this rule) now has no
  consumer. **Recorded as carried debt, not quietly dropped** — reinstating it belongs to
  the approvals system, not to retiring an engine.
- **Dead machinery in the shared panel.** `showApprover` and `gate` existed only for the
  Copilot, and `chat-types.ts` carried the Copilot's whole WS protocol — 122 lines nothing
  imported. Both removed; a `GateState` type on a surface the spec says has no gates is
  worse than no type.
- **Four comments became false the moment the files went**, including the one justifying
  the `tabLabel` prop by saying the Copilot imports the panel. All corrected.

### 16.6 Debt closed, and debt honestly left open

**Closed:** copilot_api's three known defects (deleted with it); the unbounded inbound WS
frame (#2); the boot scan's blindness to WebSocket routes (#4); the credential-attribution
gap in copilot_api.

**Scoped deliberately, and said so in the code:** the WS sweep now lists all 21 sockets and
requires each to be recorded, seeded with those that existed. **Being in that allowlist is
not an audit** — the comment says so. Confirming each in-handler check is real is 21
sockets across ten agents and remains debt. What it buys is that a NEW socket cannot ship
unrecorded, which is exactly what happened in Phase 1.

**Still open:** §1.5 self-approval (above); the BYOK env-fallback audit across nine agents
(#5); the e2e rewrite (#8, needs credentials); RLS inert (#1); context truncation (#6);
`"review this"` routing (#7).

### 16.7 Proof

- Backend **3,322 passing**, and the failure set is byte-identical to the recorded baseline
  (22 failed, 7 errors — RLS inert, plus two live-E2E fixtures failing on
  `invalid UUID 'test-tenant'`). One genuinely new failure appeared and was a test
  asserting a known gap in `copilot_api.py` still existed; deleting the file resolved it.
- Frontend **688 passing** across 81 files, typecheck and lint clean, `next build` compiles
  every route.
- `scripts/live_sessions_check.py` — **12/12 against the real database**, including that
  `ensure_session` is genuinely idempotent (it runs on every turn), that another person's
  rail does not show the chat, and that it does not leak into a standalone agent's history.
- `scripts/live_deliverables_check.py` — 16/16, unchanged.
- `scripts/live_routing_check.py` — **28/29**, the recorded boundary only.
- The app boots with the D-05 scan reporting no offenders, now including WebSocket routes.

---

## 17. Carried debt, closed (2026-09-07)

Everything §16.6 left open, worked through. Six of seven done; the seventh needs one
command only an operator can run.

### 17.1 §1.5 — you cannot approve your own run ✅

Phase 5 recorded that deleting `copilot_api` left this enforced NOWHERE, with
`runs.created_by` (migration 0038, added to serve it) consumerless. It now lives in
`record_approval`, the surviving path that both identifies a run and writes an
approval.

The permission check it already had answers a different question — "may this ROLE
approve this stage" — and passes for exactly the person the rule exists to stop, since
a BA who starts a Requirements run holds `artifact:approve_requirements` by definition.

A run with no recorded initiator is deliberately **not** blocked: `created_by` is
nullable for webhook runs and rows predating 0038, and refusing there would make every
historical run permanently unapprovable.

### 17.2 BYOK env fallback (#5) ✅ — narrower, and worse, than recorded

The note said the guarantee was "one layer deep" and nine agents were unaudited. The
audit found only the **Testing** agent ever builds a model from `ANTHROPIC_API_KEY`;
every other agent resolves through `resolve_chat_model`, which fails closed.

But the guard was the runtime mode alone, and this deployment is `local` with the key
set — so the fallback was **live**. A dropped contextvar mid-run spent the *platform's*
key instead of the project's, bypassing that project's grant and its budget, silently,
with an answer that looked fine.

`ALLOW_PLATFORM_MODEL_FALLBACK` now gates it, defaulting to false. `build_llm`'s
docstring already claimed "There is NO platform fallback — fail CLOSED"; that was untrue
when written and is true now.

### 17.3 The WebSocket sockets ✅ — and four had no auth at all

Phase 5 taught the boot scan to SEE sockets and said plainly that being in the allowlist
was not an audit. This is the audit, and it found something: four `/test-ws` endpoints
in the requirements and ingestion agents called `websocket.accept()` immediately and
echoed whatever they were sent. No ticket, no tenant check. Unauthenticated sockets in
the running app, invisible to every check until the scan was taught to list them, and
referenced by nothing — debug scaffolding that outlived its debugging. Deleted.

The other 17 all redeem a single-use ticket before accepting, and a test now enforces
that for every socket, reading each handler with its **docstring stripped** because
several describe their ticket flow in prose.

### 17.4 Context truncation (#6) ✅ — how it was cut mattered more than how much

Truncation was `body[:share]`, head only. Each artifact got ~2,400 characters on a
nine-agent run, so a PRD arrived as its title, its background, and nothing else — the
requirements, the acceptance criteria and every decision live at the END of such a
document. The agent downstream read an introduction and inferred the rest, which is
worse than being told the document was unavailable.

`_shorten` keeps head AND tail with the seam marked. The ceiling also rose, 24,000 to
72,000 characters (~18k tokens, under 10% of a 200k window) — a judgement recorded as
one.

**Summarising each artifact with a model call is deliberately NOT done.** It would be
better and costs a call per artifact per turn; that remains a product decision.

### 17.5 The e2e rewrite (#8) ✅ — never actually blocked on credentials

Phase 1 skipped the spec and kept it "as the specification for what the Phase 3 rewrite
must cover". This is that rewrite, and the first browser-level proof this surface has
had.

The recorded blocker was wrong. The suite signs in through the **mock-mode role
picker** — no password. The real blocker was that Playwright's browsers were not
installed, which looks identical from outside: every test failing at launch. Six pass
now.

Scope is honest: the default project boots with MSW mocks and no backend, so it covers
access control and the surface, not a live agent turn. The three `live_*_check.py`
scripts are what exercise that path against a real database and a real model.

### 17.6 RLS (#1) ⏸ — one command away, and it is the operator's

Much closer than recorded. The `sdlc_app` role **already exists**, is correctly
non-superuser and `NOBYPASSRLS`, and its grants are applied and self-verified by
`scripts/grant_app_role.py`. `docs/local-setup.md` already says the application connects
as it. Only `backend/.env` drifted, still pointing `POSTGRES_CONN_STRING` at `postgres`.

What remains is giving the role a login password, which is a credential change:

```sql
ALTER ROLE sdlc_app WITH LOGIN PASSWORD '<pick one>';
```

Then point `POSTGRES_CONN_STRING` in `backend/.env` and `backend/.env.test` at
`sdlc_app`, keeping `POSTGRES_MIGRATIONS_CONN_STRING` as `postgres`. Most of the 22
baseline test failures exist *because* the app connects as a `rolbypassrls` superuser
and should go green.

### 17.7 `"review this"` (#7) — deliberately left

Recorded in `live_routing_check.py` as a known boundary and left failing rather than
tuned away. Re-tuning the routing prompt to catch it risks the **held-out set** — six
messages an agent must NOT be started for, which informed none of the wording and are
the only reason the sixteen tuned cases are worth anything. Marginal gain, real risk.

### 17.8 State

- Backend **3,350 passing**; the failure set is unchanged from the recorded baseline
  (22 failed, 7 errors — RLS inert, plus two live-E2E fixtures on `invalid UUID`).
- Frontend **688 passing**, typecheck and lint clean.
- **E2E 6 passing.**
- `live_deliverables_check` 16/16, `live_sessions_check` 12/12, routing 27/29
  (`"review this"` plus `"fix the tests"`, which flips between runs on both branches).
