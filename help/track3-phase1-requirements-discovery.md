# Track 3 Phase 1 — Requirements (migration-intent) + Discovery & Assessment

> **§1 below is corrected by `help/track3-frontend-plan.md` §2 — read that first.**
> This doc was written from the backend alone, before anyone had checked what the
> frontend already has. The frontend ships a full 13-agent `AgentType`/`Phase`
> enum, a per-track roster (`lib/tracks.ts`), and even stub pages for `discovery`
> and `strategy` already — all written against the id `discovery`, not
> `discovery_assessment` as proposed below, and against `requirements` (the SAME
> id as Track 1, not a new one) for Track 3's Requirements agent. §1's naming
> proposal is superseded; everything else in this doc (the file-level build plan,
> the standalone track-enforcement gap, the build order) still holds.

**Scope note (supersedes the "build all 10 agents" framing in the earlier docs):**
the near-term goal has narrowed. We are building exactly **two** agents first —
Requirements in its migration-intent mode, and Discovery & Assessment — and each
must work in **both** of this platform's two agent-access modes:

1. **Orchestrator** — reachable through a Track 3 project's Orchestrator chat
   (`agents_orchestrator/orchestrator2/`), the track-scoped routing/dispatch layer
   built in Phase 0 (`help/track3-implementation-plan.md` §2, committed on this
   `track-3` branch).
2. **Standalone** — reachable directly, its own chat surface outside the
   Orchestrator, the same way Track 1's Requirements agent has its own page and
   its own WS/REST endpoints independent of `orchestrator2`.

Everything below is the concrete, file-level plan for building both agents in both
modes, grounded in how the existing Track 1 agents are actually wired — not the
target-shape description from the earlier docs. Read this alongside:

- `help/multi-track-agent-access-design.md` — the 10-agent Track 3 portfolio shape
  and gate definitions (§Portfolio 2).
- `help/track3-agent-build-plan.md` — why these two specifically can't reuse Track
  1's `requirements` agent as-is (both are full rebuilds, not extensions).
- `help/track3-implementation-plan.md` — Phase 0 (done) and the original 10-agent
  build order (superseded in ORDER — Discovery & Assessment and Requirements are now
  the whole near-term target, not just the first two of ten — but its per-agent
  technical notes for these two still apply).

## 1. The naming decision this phase depends on

**Track 3's Requirements agent cannot reuse the id `"requirements"`.**

`config/agent_registry.py`'s `AGENT_REGISTRY` and
`agents_orchestrator/orchestrator2/registry.py`'s `REGISTRY` are both flat dicts —
**one entry per agent id, globally**, not one per `(agent id, track)` pair. Phase 0's
track scoping works by *filtering* which ids a track's portfolio may see
(`agents_for_track`/`registry_for_track`); it does not — and structurally cannot —
let the same id `"requirements"` mean two different graphs depending on which
project is asking. `help/track3-agent-build-plan.md` already concluded Track 3's
Requirements agent is a full rebuild (different output schema, different prompt, no
INVEST/Gherkin generation) — since it can't share the implementation, it must not
share the id either, or the two would collide the moment both are registered.

**Decision: new, distinct agent ids.** Proposed:

- `discovery_assessment` — no collision risk, nothing named this exists yet.
- `requirements_modernization` — keeps the `requirements` root recognisable (matches
  the existing convention where `plan` is displayed as "Project Manager" but the id
  stays semantic) while being unambiguous in every place ids are compared as strings:
  `AGENT_REGISTRY`, `REGISTRY`, `_OWNER_OF`/`AGENT_DEFAULT_REACH`, the deliverables
  DB CHECK constraint, the frontend `ORCHESTRATOR_AGENT_IDS` enum, route paths.

**If a different name is preferred, pick it before writing code** — it touches every
file in §3 and §4 below, and changing it after the standalone router is mounted means
also changing its URL prefix, which is a breaking change for anyone who has already
linked to it.

## 2. The gap this phase must close that Phase 0 did not: standalone access is not track-scoped at all

Phase 0 made `orchestrator2` track-aware end to end — the router only offers a
project's own track's agents, and `dispatch.run_agent` refuses to run an id outside
the project's track even if the Orchestrator's routing is bypassed
(`help/track3-implementation-plan.md` §2, point 4).

**The standalone path has no equivalent check today, at all.** Read
`shared/authz/agent_access.py::check_agent_access` (the function every standalone
`*_agent_api.py` WS/REST handler calls via `assert_agent_access_for_chat`): its
resolution order is *person-level override → role-level override →
`AGENT_DEFAULT_REACH` → deny*. Nowhere in that chain is `projects.track` read. A
role that owns `requirements_modernization` (say, `ba`, mirroring `_OWNER_OF`) would
reach `/sdlc/agent/requirements_modernization` on **any** project, Greenfield
included, once the route exists and is mounted — track membership plays no part in
standalone authorization.

This did not matter before because every standalone agent belonged to the one
portfolio every project could reach. It matters now: **a Track 3 agent's standalone
endpoint must refuse to run on a project whose track does not include it**, or a
Greenfield project could accidentally (or deliberately) drive a migration-flavored
Requirements agent that expects `discovery_artifacts` as upstream context that will
never exist on that project.

**Recommended fix, scoped narrowly (mirrors Phase 0's own shape):** add a track check
alongside the existing agent-access check in each new agent's standalone handler —
resolve the project's `track` (one read, same as `orchestrator2/ws.py`'s
`_resolve_run` does) and refuse (403 or a typed chat error, matching how
`assert_agent_access_for_chat` already fails) if the agent id is not in
`agents_for_track(track)`. Do **not** bake this into `check_agent_access` itself
(would force every Track 1 agent's standalone handler to pass a track it doesn't
need); add a small wrapper — e.g. `assert_agent_access_for_chat_and_track(...)` in
`shared/authz/agent_access.py` — that Track 3's two new handlers call in place of
the plain one, so Track 1/2 standalone handlers are untouched. Write this as its own
small, testable function before wiring either agent's router, the same discipline
Phase 0 used for `_capability_for_track` in `dispatch.py`.

## 3. Discovery & Assessment — build plan

Net new (no Track 1 analog). Follows the existing per-agent package shape exactly —
compare against `agents_orchestrator/requirements_agent/` for the pattern.

**Files to create:**

```
backend/agents_orchestrator/discovery_assessment_agent/
  __init__.py
  agents/
    __init__.py
    assessor.py              # LangGraph `app` — mirrors agents/planning.py's shape
  prompts/
    __init__.py
    discovery_prompt.py       # DISCOVERY_SYS_MESSAGE (module constant, like INGESTION_SYS_MESSAGE)
  tools/
    __init__.py
    repo_scan_tools.py         # read-only clone + dependency-manifest parsing
    risk_scoring_tools.py       # per-module risk score + tier classification
    golden_master_tools.py       # behavior-baseline capture (§5 of track3-agent-build-plan.md)
  config/
    __init__.py
    session_state.py           # mirrors development_agent/config/session_state.py's shape
  discovery_assessment_agent_api.py   # standalone FastAPI router (WS + REST), mirrors
                                        # requirements_agent_api.py's structure
```

**Tools, concretely:**

- **Read-only clone.** Reuse `agents_orchestrator/development_agent/tools/git_tools.py`'s
  clone path — literally the same function if it already supports a read-only mode,
  or a thin wrapper around it that never calls the write/push path. Do not
  reimplement git plumbing.
- **Dependency manifest parsing**, per source ecosystem the pilot targets first (the
  earlier docs' running example is .NET — start with `.csproj`/`packages.config`
  parsing only; add Java/`pom.xml`, Python/`requirements.txt` etc. later rather than
  building a generic multi-ecosystem parser up front for a pilot with one target).
- **EOL/CVE lookup.** Call the platform's existing `quality.sca.scan` capability
  (Security agent's SCA tool) rather than standing up a second dependency-vulnerability
  scanner — reuse, per `help/track3-implementation-plan.md` §4.1's own note.
- **Golden-master capture.** This is the one genuinely novel piece: recording actual
  input→output behavior of the legacy system before anything touches it, so Testing
  (a later phase) has something to diff against. For Phase 1, scope this to *recording
  the mechanism and a data shape*, not a working differential-test harness — Testing
  itself is out of scope for this phase. Concretely: `discovery_artifacts` should
  reserve a field for it (e.g. `golden_master_ref`) even if what's captured this phase
  is minimal (a pointer to a fixture set, not live traffic capture).

**Output artifact.** New `discovery_artifacts` JSONB column on `runs` (migration
after whatever the current head is — check `alembic heads` before numbering; do not
guess the next number). Same pattern as `design_artifacts`, `plan_artifacts` etc. in
`shared/models/orm.py`'s `Run` model.

**Registry wiring (orchestrator2):**

1. `config/agent_registry.py`: add a `"discovery_assessment"` entry to
   `AGENT_REGISTRY` (pipeline_position, `input_artifacts=[]` since it's the first
   real stage after migration-intent Requirements, `output_artifact="discovery_artifacts"`,
   `route_path="/chat-discovery-assessment-agent"`, `gate_type="approval_required"`
   per the design doc's "Accept the assessment as planning baseline (Sign-off)").
   Add `"discovery_assessment"` to `TRACK_PORTFOLIOS["modernization"]` — **only after**
   the `AgentCapability` below exists, per the build-order guard Phase 0 added
   (`registry_for_track` raises `UnknownAgentError` otherwise — that's the test to
   run to confirm you did the two steps in the right order).
2. `agents_orchestrator/orchestrator2/registry.py`: add `_load_graph_discovery_assessment`
   / `_load_prompt_discovery_assessment` (lazy imports, matching every existing entry's
   shape) and a `"discovery_assessment": AgentCapability(...)` entry in `REGISTRY`.
3. `agents_orchestrator/orchestrator2/router.py`: add `"discovery_assessment"` to
   `DISPLAY_NAMES` (e.g. `"Discovery & Assessment"`) and `_CAPABILITIES` (a real
   description of what it does — the model routes on this text, see the module's own
   `_MIN_CAPABILITY_CHARS` floor). **These two dicts are still the full, global
   tables** (Phase 0 left them that way — see `track3-implementation-plan.md` §2,
   point 3) — adding an entry here makes it describable to any track's router that is
   given this id in its scoped `capabilities` map; it does not, by itself, offer it to
   Track 1/2 (`registry_for_track("greenfield")` still won't include it, because it's
   not in that portfolio).

**Standalone wiring:**

1. Build `discovery_assessment_agent_api.py`'s `APIRouter()`, WS + REST handlers,
   mirroring `requirements_agent_api.py`'s shape (session state, attachment handling,
   the RBAC gate via `assert_agent_access_for_chat` — extended per §2 above to also
   check track).
2. Mount it in `process_api.py`, alongside the existing `include_router(...)` calls
   (e.g. `app.include_router(discovery_router, prefix="/sdlc/agent/discovery_assessment", ...)`).
3. `_OWNER_OF["discovery_assessment"] = "architect"` in `config/agent_registry.py`
   (matches the design doc's "Owner: Architect" for this agent), which
   `AGENT_DEFAULT_REACH` derives from automatically.
4. Frontend: add the route/page for direct navigation (`route_path` above), and add
   `"discovery_assessment"` to whatever frontend enum/table mirrors
   `AGENT_DEFAULT_REACH` (`frontend/lib/roles.ts`, per the comment in
   `config/agent_registry.py` that the two must match —
   `tests/test_agent_reach_matches_frontend.py` pins this).

## 4. Requirements (migration-intent) — build plan

Rebuild of `agents_orchestrator/requirements_agent/agents/planning.py`'s graph, new
id `requirements_modernization` (see §1). Same package-shape approach as Discovery &
Assessment, but starting from a copy of the existing structure rather than from
nothing:

```
backend/agents_orchestrator/requirements_modernization_agent/
  __init__.py
  agents/
    __init__.py
    planning.py                # smaller graph — no INVEST/Gherkin story generation
  prompts/
    __init__.py
    migration_intent_prompt.py  # MIGRATION_INTENT_SYS_MESSAGE
  config/
    __init__.py
    session_state.py
  requirements_modernization_agent_api.py   # standalone router
```

**What's different from Track 1's Requirements, concretely** (per
`help/track3-agent-build-plan.md`'s verdict): output is a migration-intent brief —
scope, constraints, success criteria, why the modernization is happening — not a
BRD/PDD/user-story backlog. Reuses `req.ingest`/`board.read`/`artifact.write`
plumbing (the board-write/document-ingestion tools), drops
`story.generate`/`story.ac.normalize`/`doc.generate.brd` entirely (no user stories
in migration-intent mode).

**Output artifact.** Track 3's Requirements agent can reuse the existing
`requirements_payload` column — the shape stored there is a JSONB blob either way,
and nothing downstream assumes it holds Track 1's exact schema (each consumer reads
whatever fields it expects; migration-intent's consumer, Discovery & Assessment,
would just expect a different shape than Track 1's Design agent does). Confirm
this against `context.py`'s rendering logic before relying on it — if anything there
assumes Track 1's requirements_payload shape specifically, that's worth knowing before
building on the assumption.

**Registry + standalone wiring:** identical shape to §3's steps 1-4, with
`_OWNER_OF["requirements_modernization"] = "ba"` (matches Track 1's Requirements
ownership and the design doc's "Owner: BA").

## 5. Build and verification order

1. **Settle the naming decision (§1)** and the standalone track-check design (§2) —
   both are cross-cutting and expensive to change after either agent's router exists.
2. **Discovery & Assessment first, standalone-only, before touching the
   Orchestrator.** It's the harder agent (real repo I/O, dependency parsing, risk
   scoring) and the one with the least to lean on from existing code. Validate it end
   to end as a standalone agent against one real legacy repo before wiring it into
   `orchestrator2` at all — same reasoning as the original build-order note in
   `track3-implementation-plan.md` §6, just reordered to be THIS phase's actual first
   step rather than step 2 of ten.
3. **Wire Discovery & Assessment into orchestrator2** (registry.py, router.py,
   `TRACK_PORTFOLIOS["modernization"]`). Write the regression test first, following
   `tests/orchestrator2/test_track_scoping.py`'s pattern: a Track 3 project's
   Orchestrator can route to `discovery_assessment`; a Greenfield project's cannot,
   even with `override_agent` forcing the id (this should now be a REAL test against
   a REAL registered agent, not the monkeypatched stand-in Phase 0's tests used).
4. **Requirements (migration-intent), standalone, then orchestrator2** — same two-step
   shape, once Discovery & Assessment's pattern is proven out.
5. **End-to-end pass inside an actual Track 3 project**: create a project with
   `track="modernization"` via the UI, connect a legacy repo, run Requirements then
   Discovery & Assessment through the Orchestrator chat, then repeat the same two
   turns through each agent's standalone page. Confirm the Orchestrator's roster for
   this project shows exactly these two agents (once both are registered) and no
   others — Strategy/Design/Development/etc. still show as `[]` in
   `TRACK_PORTFOLIOS["modernization"]`, correctly, since they're not built yet.

## 6. What is explicitly NOT in this phase

Everything past these two agents — Design, Strategy, Development, Code Review,
Security, Testing, Deployment, Documentation for Track 3 — stays exactly as
described in `help/track3-agent-build-plan.md` and
`help/track3-implementation-plan.md` §4: planned, not started. The dual-repo config
decision (`track3-implementation-plan.md` §7) only needs to be resolved as far as
Discovery & Assessment's read-only legacy clone requires — the *target* repo side of
that decision (where Development pushes the port) is not needed until Development is
in scope.
