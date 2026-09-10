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

## 2. Phase 0 — make the orchestration engine track-aware (prerequisite)

Nothing agent-specific yet. This phase makes it *safe* to add Track 3 agents at all.

1. **`AGENT_REGISTRY` gets a `track: str` field** on `AgentDefinition` (or, cleaner:
   split into `AGENT_REGISTRY` (all agents, any track) filtered by a new
   `agents_for_track(track: str) -> dict[str, AgentDefinition]` that reads
   `TRACK_PORTFOLIOS[track]`). `STAGE_ORDER` becomes `stage_order_for_track(track)`
   instead of a module-level constant — every caller of the bare `STAGE_ORDER` (found
   via `grep -rn STAGE_ORDER backend/`) needs to pass a track through.
2. **`orchestrator2/registry.py`'s `REGISTRY`/`AGENT_IDS`** become per-track too:
   `registry_for_track(track)` returning the filtered `AgentCapability` map, with the
   same import-time coverage assertion but scoped to that track's portfolio.
3. **`orchestrator2/router.py`'s `DISPLAY_NAMES`, `_CAPABILITIES`, and
   `_PROMPT_TEMPLATE`** need a Track 3 variant. This is where "the orchestrator talks
   like code modernization" actually lives — the prompt template's bullet list ("THE
   AGENTS REACH THE PROJECT'S CONNECTED TOOLS... Development clones the repository,
   branches, commits, pushes and opens pull requests") is Track-1-flavored prose. A
   Track 3 prompt needs its own version: two repos not one, equivalence and cutover
   vocabulary, no "first-release requirements" framing. Concretely: `_system_prompt()`
   takes a `track` argument and selects between `_PROMPT_TEMPLATE_GREENFIELD` and a new
   `_PROMPT_TEMPLATE_MODERNIZATION`, with per-track `_CAPABILITIES`/`DISPLAY_NAMES`
   dicts, each still asserted at import to cover exactly that track's `AGENT_IDS`.
4. **`dispatch.run_agent`** already takes `project_id`; add a `track` lookup at the top
   (read `projects.track`, default `"greenfield"` for existing rows since the column is
   nullable) and pass it through to `registry_for_track`/`router.route`. One extra read,
   no signature change to callers beyond that.
5. **Test to write first, before any Track 3 agent exists**: a Greenfield project's
   Orchestrator turn must never offer or route to a Track-3-only agent id, and
   vice versa. This is the regression Phase 0 exists to prevent — write it red against
   the current flat registry, then make it pass by doing the split above.

This phase touches no agent logic and ships no new capability. It's pure plumbing, and
skipping it means Track 3 agents get bolted onto Track 1's roster instead of getting
their own — exactly the "same name, different track" confusion the user is flagging.

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
