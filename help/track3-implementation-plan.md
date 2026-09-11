# Track 3 (Code Modernization) — implementation plan

Companion to `help/multi-track-agent-access-design.md` (the 10-agent shape) and
`help/track3-agent-build-plan.md` (which of those 10 are net-new vs. rebuilds). This
doc is the third piece: the actual engineering plan to make a user's flow real —
*create a project, pick Track 3, the orchestrator runs the 10 modernization agents in
its own voice, takes a legacy repo, and ends with a working port in a new repo.*

Grounded in the code as it stands today, not the target design. Citations are to real
files; verify against current code before building against a line number, per the
usual caveat.

## 1. What already exists, and the one thing it assumes that breaks Track 3

- `projects.track` (migration `0024_project_track.py`) already has a CHECK constraint
  allowing `'modernization'` — project creation can already record the choice. No
  backend logic reads it yet.
- `backend/config/agent_registry.py`'s `AGENT_REGISTRY` is a **single flat dict**, and
  `TRACK_PORTFOLIOS["modernization"] = []` — confirmed empty.
- Critically: `shared/services/orchestrator/progression.py`'s `STAGE_ORDER` is
  **derived directly from `AGENT_REGISTRY`**, sorted by `pipeline_position`:
  ```python
  STAGE_ORDER = [aid for aid, _ in sorted(AGENT_REGISTRY.items(), key=...)]
  ```
  `agents_orchestrator/orchestrator2/registry.py`'s `AGENT_IDS` and `REGISTRY` are in
  turn derived from `STAGE_ORDER`, and `orchestrator2/router.py`'s `DISPLAY_NAMES` and
  `_CAPABILITIES` are hand-typed tables asserted to cover exactly that same set. **The
  whole orchestrator today assumes there is one global roster of agents, platform-wide,
  not one per track.** Add ten Track 3 entries to `AGENT_REGISTRY` as-is and they
  become candidates on *every* project's Orchestrator turn, Greenfield included — the
  router's LLM would be offered "Discovery & Assessment" on a brand-new project with no
  legacy repo to discover.

  **This is the real prerequisite work, and it has to happen before agent #1 of
  Track 3 is built**, not after: the engine needs to go from "one global roster" to
  "one roster per track," or Track 3 can never be added without corrupting Track 1/2's
  routing.

- `agents_orchestrator/development_agent/tools/git_tools.py` already has real git
  plumbing (clone into a sandbox, branch, commit, push, open a PR) wired through the
  project's connected repo (Azure Repos or GitHub, per `router.py`'s own capability
  line for `development`). This is the piece Track 3's Development agent needs most,
  and it's the one part of Track 1 that transfers with the least rework — it just needs
  to operate against *two* repo references instead of one (see §4).

## 2. Phase 0 — make the orchestration engine track-aware (DONE, `track-3` branch)

Built and merged into the `track-3` branch, additive-only, zero behavior change for
Track 1/2. Scoped narrower than the original sketch below (kept here struck through
for the record) once the real blast radius became clear: `AGENT_IDS`/`REGISTRY` are
depended on by ~30 files — the deliverables DB CHECK constraint, the frontend
`ORCHESTRATOR_AGENT_IDS` enum, transcript budgeting, context rendering — none of
which need to change until a Track 3 agent actually exists to render or budget for.
Rewriting those globally to be "per-track" would have been a large, high-risk change
for zero behavior payoff this session. What actually had to change is narrower and
was verified against the real risk: **what the router OFFERS, and what dispatch will
RUN, for a given project's track.**

What shipped:

1. **`config/agent_registry.py`**: `agents_for_track(track)` and
   `stage_order_for_track(track)`, filtering `AGENT_REGISTRY`/pipeline order down to
   `TRACK_PORTFOLIOS[track]`. Raises `UnknownTrackError` for a bad track string
   (never silently returns an empty portfolio for a typo) and raises `KeyError` if a
   portfolio ever names an id `AGENT_REGISTRY` doesn't have (a build-order bug,
   surfaced immediately rather than silently dropping the agent).
2. **`orchestrator2/registry.py`**: `agent_ids_for_track(track)` and
   `registry_for_track(track)`, the same filtering one layer up, against `REGISTRY`.
   `registry_for_track` raises `UnknownAgentError` if a track's portfolio names an id
   with no `AgentCapability` registered yet — the other half of the build-order
   guard (agent added to the portfolio before its graph/prompt is wired here).
   **`AGENT_IDS`/`REGISTRY` themselves are UNCHANGED** — every one of the ~30 files
   depending on the full nine-agent set keeps reading it, untouched.
3. **`orchestrator2/router.py`**: `route()` gains `track: str = "greenfield"`.
   `_tool_specs`, `_system_prompt`, `_system_prompt_with_continuity`, `_ask_model` and
   `_validated` all take an optional `capabilities`/`valid_ids` override
   (`None`/default → today's exact global behavior, proven byte-identical by
   `test_system_prompt_none_default_matches_full_registry`). `prefilter` itself
   stays track-agnostic on purpose (it's pure, and rebuilding its regex per track on
   every call would be needless cost) — instead `route()` discards a `prefilter`
   match that falls outside the scoped track's portfolio, falling through to the
   model instead of trusting it. A track with an empty portfolio (today:
   `modernization`, `rpa_infra`, `data_engineering`) short-circuits to a direct reply
   before any model call — no `bind_tools([])`.
   **The Track 3-flavored prompt text itself (two-repo framing, equivalence/cutover
   vocabulary) is deliberately NOT written yet** — there is nothing to describe until
   Discovery & Assessment exists. `_system_prompt_with_continuity`'s signature is
   ready for it (it renders whatever `_CAPABILITIES`/`DISPLAY_NAMES` subset it's
   given); the content is Phase 1+'s job, alongside the agent it describes.
4. **`orchestrator2/dispatch.py`**: `run_agent()` gains `track: str = "greenfield"`
   and resolves its capability via a new `_capability_for_track(agent_id, track)`
   instead of the unscoped `get_capability`. **This is the enforcement layer, not a
   restatement of the router's offer** — `ws.py`'s `override_agent` path lets a
   client name `agent_id` directly on the wire, bypassing `router.route` entirely, so
   the router being scoped correctly is not sufficient on its own. `dispatch` is
   where both paths (routed and user-forced) converge, so it's the one place this
   closes for both.
5. **`orchestrator2/ws.py`**: `RunSelection` gained a `track: Optional[str] = None`
   field (defaulted, so every pre-existing construction site — tests included — keeps
   compiling). `_resolve_run` now also reads `Project.track` via a second, tenant-
   already-verified query (kept as a *second* query rather than folding it into the
   existing `Run` lookup specifically so the original query's shape didn't change —
   several tests build a fake DB session mocking only `.scalar_one_or_none()` on that
   one query, and preserving it kept them passing unmodified rather than forcing a
   test-fixture rewrite for a plumbing change). The socket resolves
   `track = project_track or "greenfield"` once per turn and threads the SAME value
   into both `route(..., track=track)` and `run_agent(..., track=track)` — so the
   agent the router offered is provably the same track dispatch then enforces
   against, for that turn.
6. **Regression tests**: `tests/orchestrator2/test_track_scoping.py` (new, 19 tests) —
   proves the boundary holds using a monkeypatched `AGENT_REGISTRY`/`TRACK_PORTFOLIOS`
   entry standing in for a Track 3 agent that doesn't exist yet, since a real one
   can't be built before this phase lands. Covers: portfolio filtering, both
   build-order guards (agent-before-portfolio, portfolio-before-agent), the
   empty-portfolio direct-reply short-circuit, prefilter-match-outside-track being
   discarded, `_validated` rejecting an out-of-portfolio id, and — the actual
   enforcement proof — `run_agent` refusing an out-of-track id via the
   `override_agent`-shaped path with no router involved at all.

**Verified against the existing suite, not just the new one.** Ran the full
`tests/orchestrator2/` + agent-registry suite (690 passed) before and after: the
`_resolve_run` query-shape change initially broke 51 tests (two fixture-based DB
mocks expecting the old single-`Run` query, and one source-scan test pinning a
literal call-site string that a line-wrap had broken); all 51 were fixed by keeping
the original `Run` query byte-identical and adding the `track` read as a genuinely
separate second query, and by keeping the `_resolve_run(run_id, tenant_id)` call
on one line. The 3 that remain failing are confirmed pre-existing on the
pre-Phase-0 baseline too (a local DB grant gap on `conversation_messages` for the
`sdlc_app` role, and a stray `agents_orchestrator/orchestrator/__pycache__`
directory) — neither caused by, nor fixed by, this phase.

~~Original sketch (superseded by the narrower design above; kept for context)~~:
~~`AGENT_REGISTRY` gets a `track` field; `STAGE_ORDER` becomes
`stage_order_for_track(track)` and every one of its ~30 callers gets touched;
`DISPLAY_NAMES`/`_CAPABILITIES`/`_PROMPT_TEMPLATE` get full Track 3 variants
immediately.~~ Rejected: the global tables' ~30 dependents (deliverables schema,
frontend enum, context rendering) don't need per-track awareness until a Track 3
agent exists to need it, and touching them all for zero behavior change was the
wrong trade. Revisit when Phase 1 (Discovery & Assessment) actually lands and needs
`discovery_artifacts` to render somewhere.

## 3. Data model additions

`Run` (`shared/models/orm.py`) has one JSONB column per Track-1 artifact
(`requirements_payload`, `design_artifacts`, `plan_artifacts`, ...). Track 3 needs two
more, in a new migration after `0054` (check the current head with `alembic heads`
before numbering):

```python
discovery_artifacts: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
strategy_artifacts: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
```

These become each new agent's `output_artifact` in its `AgentDefinition`, exactly like
`design_artifacts` does for `design` today. `requirements_payload`,
`code_review_artifacts`, `security_artifacts`, `testing_artifacts`,
`deployment_artifacts`, `documentation_artifacts` are reused as-is — the column stays,
only the *shape* of what Track 3's version of each agent writes into it changes (per
`help/track3-agent-build-plan.md`'s per-agent breakdown).

**The dual-repo concept** is new and has no home yet. `projects.connectors` (JSONB,
`{agent_id: [connector_kind, ...]}`, migration 0025) already supports a project
declaring which connector a given agent stage uses — but it's shaped for one repo per
project. Track 3 needs *two* repo references per project: legacy (read-mostly, scanned
by Discovery, read by Development, read by Testing for the differential run) and target
(written by Development, the thing that ships). Cleanest fit: add
`projects.legacy_repo_connector` and reuse the existing `connectors` field for the
target repo — or, more consistent with the existing per-stage shape, extend
`tool_access_modes`' key convention (`"{agent_id}::{connector|mcp}::{ref}"`) with a new
`ref` value (`"legacy"` vs `"target"`) rather than adding bespoke columns. Worth a
deliberate design pass before Phase 1 lands, since every one of the ten agents reads
this.

## 4. Per-agent build plan

Same order and rebuild/net-new verdicts as `help/track3-agent-build-plan.md`. This
section adds *what to actually write*, per agent, following the existing file
layout — each Track 1 agent lives at
`agents_orchestrator/<name>_agent/agents/<module>.py` (LangGraph `app`) plus
`prompts/<name>_prompt.py` (`*_SYS_MESSAGE`), wired into `orchestrator2/registry.py`.
Track 3 agents follow the identical shape so they plug into the same
`load_graph`/`load_prompt`/`mode` contract — no changes to `dispatch.run_agent` or
`ws.py`'s turn loop are needed once Phase 0 lands; they're generic over any
`AgentCapability`.

1. **Discovery & Assessment** — `agents_orchestrator/discovery_agent/agents/assessor.py`.
   Net new. Build and validate this one **standalone, against a real legacy repo,
   before wiring it into the orchestrator at all** (§ build order in the companion
   doc). Tools it needs, alongside a read-only clone (reuse `git_tools.py`'s clone path,
   never its write path): a dependency-manifest parser per source ecosystem (`.csproj`/
   `packages.config` for .NET, `pom.xml`/`build.gradle` for Java, `requirements.txt` for
   Python...), an EOL/CVE lookup (the platform's existing `quality.sca.scan` capability
   from Security is the right thing to call here, not a reimplementation), and the
   module-risk-scoring logic itself, which is genuinely new. Output:
   `discovery_artifacts` = dependency graph + per-module risk score + tier
   (mechanical/LLM-assisted/manual) + the golden-master capture (§5 in the companion
   doc — this has to happen here, first, or Testing has nothing to diff against later).
2. **Requirements (migration-intent)** — rebuild of `requirements_agent`'s planning
   graph with a new prompt and a smaller output schema (no INVEST stories/Gherkin ACs;
   a migration-intent brief instead). Reuses `req.ingest`/`board.read`/`artifact.write`
   plumbing.
3. **Design** — rebuild of `design_architecture_agent`'s `architecture.py` graph.
   Reuses HLD/LLD/ADR/diagram-rendering tools; new decision node for migration pattern
   (rewrite/strangler-fig/branch-by-abstraction) and target-stack selection, consuming
   `discovery_artifacts` instead of a blank slate.
4. **Strategy** — net new. Consumes `discovery_artifacts` (risk tiers) +
   `design_artifacts` (target architecture) and produces `strategy_artifacts`: wave
   sequencing, per-module equivalence criteria, the legacy-side change-freeze policy.
   May borrow `pm_agent`'s scheduling tools for wave/capacity sequencing, nothing else.
5. **Development** — rebuild of `dev_agent.py`. The load-bearing change: it now needs
   **two repo handles**, not one — read from legacy (via the dual-repo config in §3),
   write to target. For each module in the current wave (per Strategy's
   `strategy_artifacts`), branch on the tier Discovery assigned: mechanical → invoke the
   ecosystem's codemod/upgrade tool as a sandboxed build/lint command (the same
   allow-listed-command mechanism `code.build`/`code.lint` already use, just pointed at
   `dotnet upgrade-assistant`/OpenRewrite/etc. instead of the project's own build); LLM-
   assisted → prompt-driven rewrite same as Track 1 Development's `code.generate`/
   `code.edit`, but with the legacy module's source and the equivalence criteria in
   context; manual-only → skip and flag for a human, don't attempt it. Also owns the
   build-system migration itself (`.csproj` format, CI pipeline files) as its own
   pass per module, not folded into the code changes silently.
6. **Code Review** — rebuild of `reviewer.py`'s conformance check: swap
   `review.requirements.coverage.map` for a new equivalence-criteria check against
   Strategy's per-module criteria, add a legacy-antipattern-carryover flag.
7. **Security** — lightest touch. Extend `scanner.py`'s existing scan stack with one
   additional scan pass that diffs secrets/insecure patterns between the legacy module
   and its migrated counterpart, rather than scanning the target alone.
8. **Testing** — rebuild, and the most technically novel of the ten. Needs to actually
   **execute the legacy code** in a sandbox to diff against the modernized output —
   this may mean a legacy-runtime container image per source ecosystem (e.g. a .NET
   Framework container alongside the platform's normal sandbox), provisioned per the
   module's Discovery-captured golden-master inputs. Add a performance-regression
   comparison alongside the functional diff, since cross-runtime moves change GC/
   threading/I/O behavior even when output matches.
9. **Deployment** — rebuild of `deployer.py`'s readiness/gate-aggregation logic,
   replacing the single-release model with phased-cutover planning: wave sequencing
   from Strategy, a parallel-run window, traffic-shift steps, and a legacy-decommission
   schedule as an explicit deliverable, not an afterthought.
10. **Documentation** — light rebuild of `compiler.py`: new generators for the
    old-to-new traceability map, the equivalence-evidence summary (pulled from
    Testing's differential results), and the decommission note. `doc.generate`/
    `vcs.pr.create` plumbing is unchanged.

## 5. End-to-end flow (what the user actually sees)

1. **Project creation.** User picks Track 3 in the project-creation UI (the `track`
   field already exists on `projects`; this is a frontend form addition plus passing
   `track="modernization"` through `POST /projects`). At creation (or as a follow-up
   step) the user connects two repos: the legacy source and the target (a new, empty
   repo the platform will push the port into) — the dual-repo config from §3.
2. **Orchestrator opens.** Because Phase 0 makes routing track-aware, the Orchestrator
   for this project now offers exactly the 10 modernization agents, described in
   modernization language, and the Context Agent's prompt (§2.3) frames its own role
   around "this project migrates an existing codebase," not "this project builds
   something new."
3. **The user describes the migration intent** in chat ("migrate this legacy .NET
   Framework billing service to .NET 8" or similar). The router's prefilter/model
   picks Requirements (migration-intent mode); the same "no fixed order, route on what
   the message asks for" model as Track 1 applies — nothing here reintroduces a rigid
   pipeline, agents still run because the conversation asks for their work, same as
   today.
4. **Requirements → Discovery & Assessment.** Discovery clones the legacy repo
   read-only, builds the dependency graph, flags EOL/vulnerable dependencies, scores
   and tiers every module, and captures the golden-master behavior baseline. Sign-off
   gate: user accepts the assessment as the planning baseline.
5. **Design → Strategy.** Design proposes the target architecture and migration
   pattern from the assessment; Strategy sequences modules into risk-ordered waves with
   per-module equivalence criteria. Both gated by sign-off, same `approval_required`
   mechanism Track 1 already uses.
6. **Development, per wave.** For each module in the current wave: codemod tooling
   where mature, LLM-assisted rewrite otherwise, manual-flag for the rest. Pushes to
   the **target repo**, opens a PR — the Consequential-gated push Track 1 Development
   already requires, unchanged.
7. **Code Review + Security run in parallel** on each migrated module's diff, same
   `can_parallel_with` mechanism as Track 1's `code_review`/`security`.
8. **Testing** runs the differential/equivalence suite per module — legacy vs.
   modernized, same inputs, diffed outputs, plus perf comparison. Mandatory sign-off
   before a module is considered migrated.
9. **Deployment** aggregates Testing + Security verdicts per wave into a go/no-go,
   manages the phased cutover with a parallel-run window, and eventually schedules
   legacy decommissioning once every wave has cut over.
10. **Documentation** produces the cutover pack — updated SDD, the old-to-new
    traceability map, equivalence evidence, the decommission note — as the project's
    final deliverable.

End state: the target repo contains the ported codebase in the new language/framework,
with a documented, evidenced trail from every legacy module to its modernized
counterpart and proof (Testing's differential results) that behavior was preserved.

## 6. Build order (restated with Phase 0 in front)

1. Phase 0 — track-aware registry/router split, with the regression test written first
2. Discovery & Assessment, validated standalone against a real legacy repo
3. Requirements (migration-intent)
4. Design
5. Strategy
6. Development
7. Code Review + Security (parallel)
8. Testing
9. Deployment
10. Documentation

Each agent, once built, gets its own `AgentDefinition` entry with `track:
"modernization"`, is added to `TRACK_PORTFOLIOS["modernization"]`, and gets a
`REGISTRY`/`DISPLAY_NAMES`/`_CAPABILITIES` entry in the Track-3 router variant — never
before it actually runs end-to-end, per the existing convention in
`agent_registry.py`'s own comment ("an agent id is added here only once it's actually
built and mounted").

## 7. Open decisions to make before Phase 0 starts

- **Dual-repo config shape** (§3): new columns vs. extending `tool_access_modes`'
  existing `ref` convention. Pick one before Discovery is built — it's the first agent
  that reads it.
- **Legacy-runtime sandboxing for Testing** (§4.8): needs a decision on how the
  platform provisions an arbitrary legacy runtime (a .NET Framework container, an old
  JDK, ...) safely and per-tenant, which is new infrastructure, not just new agent code.
- **Per-track router prompt ownership** (§2.3): whether Track 3 gets a fully separate
  prompt template or a shared template with track-conditional sections. Separate is
  more maintenance but keeps each track's voice honest instead of accreting
  conditionals that drift.
