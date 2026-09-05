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
