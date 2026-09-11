# Track 3 Phase 1 — Requirements (migration intent) + Discovery & Assessment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Track 3 (Code Modernization) project shows its own ten-agent roster with the first two agents — Requirements (migration intent) and Discovery & Assessment — real and clickable, each with its own capabilities, usable standalone from its own page and through the Orchestrator, which speaks in Track 3's voice.

**Architecture:** Two new, independent agent ids (`requirements_modernization`, `discovery`) are added across the stack the same way the nine Portfolio 1 agents are wired: a LangGraph agent+tools graph per agent, a standalone WS router per agent gated on agent access **and** project track, an `orchestrator2` capability entry, and a frontend page. The analysis Discovery performs (inventory, manifest parsing, dependency graph, EOL table, risk scoring) is deterministic Python with no model in the loop, so it is unit-testable and its numbers are explainable; the model only drives the conversation and narrates. Phase 0's track scoping (`TRACK_PORTFOLIOS`, `registry_for_track`, `route(track=)`, `run_agent(track=)`) keeps both agents out of Greenfield/Enhancement projects.

**Tech Stack:** FastAPI, LangGraph, langchain-litellm (BYOK via `resolve_model_for_run`), SQLAlchemy/Alembic (Postgres), pytest (against `sdlc_product_test`), Next.js 15 / React / TanStack Query / zod, vitest.

**Spec:** `help/multi-track-agent-access-design.md` (Portfolio 2 table, Parts 1.5, 2.2, 2.3, 4.3, 5), `help/track3-phase1-requirements-discovery.md`, `help/track3-frontend-plan.md`, `help/track3-agent-build-plan.md`, `help/track3-implementation-plan.md` §2 (Phase 0, done).

## Global Constraints

- Agent ids: `requirements_modernization` (Track 3 Requirements, migration-intent) and `discovery` (Discovery & Assessment). Frontend `Phase` ids are identical. Routes: `/projects/[id]/requirements-modernization`, `/projects/[id]/discovery`. WS: `/sdlc/agent/requirements-modernization/ws`, `/sdlc/agent/discovery/ws`.
- Owners (one agent, one role): `requirements_modernization` → `ba`; `discovery` → `ba` (user decision 2026-09-10, overriding the design doc's Architect); `project_admin` is owner (fallback) of both. The Orchestrator stays Project-Admin-only. Org Admin / BU Admin: no agent access.
- Gates (design doc Portfolio 2): Requirements — board writes are Consequential, baselining the brief is a Sign-off. Discovery — accepting the assessment as the planning baseline is a Sign-off. Sign-off reuses the existing artifact submit/approve flow on the exported document.
- `TRACK_PORTFOLIOS["modernization"] == ["requirements_modernization", "discovery"]`. Greenfield/Enhancement portfolios unchanged, byte-identical greenfield router prompt.
- A Track 3 agent's standalone endpoint refuses a project whose track does not include it. Track 1 handlers are not changed.
- Discovery never writes to the legacy repository: shallow clone, push URL disabled, no commit/push tools.
- No env-key model fallback anywhere; project-scoped BYOK like every other agent.
- Say "Business Unit" in prose, never "workspace" (code identifiers keep `workspace`).
- Tests run against `sdlc_product_test` only (`backend/.env.test`). Run backend tests with `uv run python -m pytest` from `backend/`.
- Commits happen only when the user asks. Checkpoint steps below say "checkpoint" instead of "commit".

---

## File Structure

### Backend — new

```
backend/agents_orchestrator/modernization_common/
  __init__.py
  graph.py                 # build_tool_agent_graph(): agent node (BYOK) + async tools node
  standalone.py            # serve_agent_socket(): WS turn loop shared by both agents
  files.py                 # output dir + /generated URL + register as stage artifact

backend/agents_orchestrator/discovery_agent/
  __init__.py
  analysis/__init__.py
  analysis/inventory.py    # languages, LOC, modules, test presence, platform blockers
  analysis/manifests.py    # .csproj/packages.config/pom.xml/gradle/package.json/python
  analysis/eol.py          # curated runtime/framework EOL table + deprecated packages
  analysis/graph.py        # module→module and module→package dependency graph
  analysis/risk.py         # per-module score 0-100, tier, factors
  analysis/assessment.py   # assess_repository() → discovery_artifacts dict + markdown
  tools/__init__.py
  tools/repo_tools.py      # list repos, read-only clone (ADO / GitHub / allow-listed public https)
  tools/assessment_tools.py# assess, module detail, graph, export report
  prompts/__init__.py
  prompts/discovery_prompt.py   # DISCOVERY_SYS_MESSAGE
  agents/__init__.py
  agents/assessor.py       # `app`, DISCOVERY_SYS_MESSAGE re-export
  discovery_agent_api.py   # APIRouter: WS /ws

backend/agents_orchestrator/requirements_modernization_agent/
  __init__.py
  brief.py                 # MigrationIntentBrief model, completeness, markdown
  tools/__init__.py
  tools/brief_tools.py     # record brief, export brief, board read + gated board write
  prompts/__init__.py
  prompts/migration_intent_prompt.py  # MIGRATION_INTENT_SYS_MESSAGE
  agents/__init__.py
  agents/intake.py         # `app`
  requirements_modernization_agent_api.py  # APIRouter: WS /ws

backend/shared/routers/modernization.py   # GET latest brief / assessment for a project
backend/migrations/versions/0057_track3_phase1_agents.py
```

### Backend — modified

```
config/agent_registry.py              AGENT_REGISTRY + TRACK_PORTFOLIOS + _OWNER_OF
agents_orchestrator/orchestrator2/registry.py   REGISTRY entries
agents_orchestrator/orchestrator2/router.py     DISPLAY_NAMES, _CAPABILITIES, track-aware prefilter, Track 3 prompt
agents_orchestrator/orchestrator2/deliverables.py  DISPLAY_NAME
shared/authz/agent_access.py          assert_agent_access_for_chat_on_track
shared/authz/permissions.py           _PHASE_PERMISSION, catalog, role grants
shared/governance/routing.py          AGENT_OWNER_ROLE["requirements_modernization"]
shared/models/orm.py                  Run.migration_intent_payload, Run.discovery_artifacts
shared/services/artifact_service.py   _COLUMN_MAP
config/context_broker.py              _ARTIFACT_FIELDS + formatters
shared/routers/agent_profiles.py      PIPELINE_ORDER
shared/routers/runs.py                stage output dirs for the two agents
process_api.py                        mount the two WS routers + modernization router
tests/orchestrator2/test_deliverables_schema.py   read the newest CHECK definition
```

### Frontend — modified / new

```
lib/schemas/enums.ts          AgentType + Phase gain requirements_modernization
lib/tracks.ts                 modernization roster starts with requirements_modernization
lib/agents.ts                 labels, descriptions, gate, route, builtAgentsForTrack()
lib/roles.ts                  ALL_NONE/ALL_OWNER, ba owner, AGENT_OWNER_ROLE
lib/orchestrator/agents.ts    ORCHESTRATOR_AGENT_IDS gains both ids
lib/orchestrator/types.ts     PHASE_FOR_AGENT
app/api/chat/route.ts         agentWsPath cases (+ pinned test)
components/app/tools-stage-picker.tsx   track-aware stages (+ callers pass track)
app/(app)/projects/[id]/page.tsx        tile state uses builtAgentsForTrack(track)
lib/api/modernization.ts, lib/schemas/modernization.ts   (new)
app/(app)/projects/[id]/requirements-modernization/page.tsx  (new)
app/(app)/projects/[id]/discovery/page.tsx                    (replace stub)
components/modernization/*.tsx  migration brief card, assessment views (new)
```

---

### Task 1: Discovery analysis core (pure, no model)

**Files:** Create `backend/agents_orchestrator/discovery_agent/analysis/{inventory,manifests,eol,graph,risk,assessment}.py`, `__init__.py`s. Test: `backend/tests/discovery/test_analysis.py` (fixture repos built in `tmp_path`).

**Interfaces (Produces):**
- `scan_inventory(root: Path) -> Inventory` — `Inventory.modules: list[ModuleFacts]` where `ModuleFacts` has `name, path (posix, relative), ecosystem, manifest, files, loc, languages: dict[str,int], has_tests, blockers: list[str]`.
- `parse_module_manifest(root: Path, module: ModuleFacts) -> ManifestFacts` with `runtime: Runtime | None` (`name`, `version`), `dependencies: list[Dependency]` (`name, version, kind` in `{"package","framework-assembly"}`), `project_references: list[str]` (relative module paths).
- `runtime_status(runtime, as_of: date) -> RuntimeStatus` (`status` in `{"eol","approaching","legacy","supported","unknown"}`, `eol_date`, `note`); `deprecated_reason(ecosystem, package) -> str | None`.
- `build_dependency_graph(modules) -> {"nodes": [...], "edges": [...]}` plus `fan_in(modules) -> dict[str,int]`.
- `score_module(facts, *, runtime_status, deprecated, vulnerable, fan_in, cross_language) -> ModuleRisk` (`score: int 0..100`, `tier` in `{"mechanical","llm_assisted","manual"}`, `factors: list[{"factor","points","detail"}]`).
- `assess_repository(root, *, as_of=None, vulnerabilities=None, repository=None, target_stack="") -> dict` (the `discovery_artifacts` shape below) and `assessment_markdown(artifacts) -> str`.

`discovery_artifacts` (schema_version 1):
```json
{"schema_version":1,"generated_at":"ISO","target_stack":"","repository":{"url":"","branch":"","commit":"","provider":""},
 "summary":{"module_count":0,"file_count":0,"loc":0,"languages":{},"ecosystems":[],
            "tier_counts":{"mechanical":0,"llm_assisted":0,"manual":0},"risk":{"average":0,"max":0},
            "flag_counts":{"eol":0,"deprecated":0,"vulnerable":0}},
 "modules":[{"name":"","path":"","ecosystem":"","loc":0,"files":0,"languages":{},"has_tests":false,
             "runtime":{"name":"","version":"","status":"","eol_date":null,"note":""},
             "dependencies":[{"name":"","version":"","kind":"package","status":"ok|deprecated|vulnerable","note":"","vulnerabilities":[]}],
             "depends_on":[],"dependents":[],"blockers":[],
             "risk":{"score":0,"tier":"","factors":[]}}],
 "dependency_graph":{"nodes":[],"edges":[]},
 "flags":{"eol":[],"deprecated":[],"vulnerable":[]},
 "scanners":{"trivy":"ok|unavailable|error|skipped","note":""},
 "golden_master":{"status":"not_captured","ref":null,"note":"..."}}
```

Risk scoring (deterministic, each factor recorded):
- size: LOC <500 → 2, <2k → 6, <10k → 12, <50k → 16, else 20
- runtime: eol → 15, approaching → 8, legacy → 5
- platform blockers (System.Web/WebForms `.aspx`, WCF server `.svc`/`System.ServiceModel`, WinForms/WPF, .NET Remoting): 10 each, cap 25
- external dependency count: `min(10, n // 3)`
- deprecated dependencies: 5 each, cap 10; vulnerable: critical/high 10, else 4, cap 10
- fan-in: 2 per dependent module, cap 10
- no tests in module: 5
- tier: `manual` if a WebForms/WCF/Remoting blocker or score ≥ 70; `mechanical` if no blockers, score < 35 and not cross-language; else `llm_assisted`.

- [x] **Step 1: Write failing tests** — build a legacy .NET Framework fixture (SDK-less `.csproj` with `<TargetFrameworkVersion>v4.5.2`, `packages.config` with `Newtonsoft.Json 9.0.1` and `WindowsAzure.Storage 8.1.4`, a `Default.aspx`, a `<Reference Include="System.Web" />`, a `<ProjectReference>` to a class library), an SDK-style `net6.0` library with `PackageReference`s and a tests project, a `package.json` module (engines node 14), a `pom.xml` module (java 8). Assert: modules found with correct ecosystems; runtimes parsed (`.NET Framework 4.5.2`, `.NET 6.0`, `Node.js 14`, `Java 8`); `.NET Framework 4.5.2` is `eol` as of 2026-09-10; `WindowsAzure.Storage` flagged deprecated; graph has edge web→lib; web module tier `manual` (WebForms blocker) with a `platform` factor; the small SDK-style lib scores lower than the web app; `assess_repository` summary counts equal module-level sums; `assessment_markdown` has ≥2 headings.
- [x] **Step 2: Run tests → FAIL** (`uv run python -m pytest tests/discovery/test_analysis.py -q`, ModuleNotFoundError).
- [x] **Step 3: Implement the six modules.**
- [x] **Step 4: Run tests → PASS.**
- [x] **Step 5: Checkpoint.**

### Task 2: Shared agent graph + output-file helper

**Files:** Create `backend/agents_orchestrator/modernization_common/{__init__,graph,files}.py`. Test: `backend/tests/discovery/test_common_graph.py`.

**Interfaces (Produces):**
- `class ToolAgentState(TypedDict, total=False)`: `messages` (add_messages), `tenant_id`, `model_id`, `offering_id`, `project_id`, `resolved_model`.
- `build_tool_agent_graph(*, agent_type: str, tools: list, checkpoint_name: str, max_tokens: int = 8096)` → compiled graph with nodes `agent` (resolves the model through `resolve_model_for_run(tenant, model_id, offering_id=, project_id=)` unless the dispatch contextvar already holds one, binds `tools + get_skill_tools(agent_type) + get_mcp_tools()`, calls `guarded_completion`, repairs tool pairs with `sanitize_tool_call_pairing`, returns `friendly_model_error` text on failure) and `tools` (async; awaits each tool, caps output at 12 000 chars, one error `ToolMessage` per failing call, re-establishes the resolved model from state).
- `output_dir(segment: str) -> str` (`{FILES}/<user>/<segment>/<session>/output`), `generated_url(segment, filename)`, `async announce_generated_file(segment, filename, path, *, stage) -> str` (broadcasts `file_generated`, calls `register_generated_file(..., stage=stage)`, returns the URL).

- [x] Step 1: failing test — the graph compiles with a fake tool; the tools node runs an async tool and caps a 20 000-char result; a raising tool yields an error ToolMessage with the call's id.
- [x] Step 2: run → FAIL. Step 3: implement. Step 4: run → PASS. Step 5: checkpoint.

### Task 3: Discovery tools, prompt, graph

**Files:** `discovery_agent/tools/{repo_tools,assessment_tools}.py`, `prompts/discovery_prompt.py`, `agents/assessor.py`. Test: `backend/tests/discovery/test_discovery_tools.py`.

**Interfaces:**
- Session state `discovery_agent.tools.repo_tools.session(session_id) -> DiscoverySession` (`work_dir`, `repo_url`, `branch`, `commit`, `provider`, `repos: dict[name,url]`, `assessment: dict | None`).
- Tools: `list_legacy_repositories(project: str = "")`, `clone_legacy_repository(repository: str, branch: str = "")`, `assess_legacy_repository(target_stack: str = "")`, `get_module_detail(module: str)`, `get_dependency_graph(module: str = "")`, `export_assessment_report(filename: str = "discovery_assessment.docx")`.
- Clone rules: credentials only from the bound connector (ADO PAT / GitHub token); a bare https URL is accepted only for hosts in `{"github.com","dev.azure.com","gitlab.com","bitbucket.org"}` or `*.visualstudio.com`; `git clone --depth 1 --single-branch [--branch B]`, 300 s timeout, then `git remote set-url --push origin DISABLED` so nothing can push; the PAT is scrubbed from every message.
- `assess_legacy_repository` runs Trivy through `security_agent.tools.trivy_tool.run_trivy_scan` (degrades to `unavailable`), calls `assess_repository`, persists with `persist_artifact(get_run_id(), "discovery", artifacts)` when a run id is bound, and returns a capped JSON digest (summary, flags, top 10 modules by score) for the model.
- `DISCOVERY_SYS_MESSAGE`: Discovery & Assessment for a Code Modernization project; reads the migration-intent brief from context; asks which repository (never invents one); clones read-only; assesses; reports as a markdown document with headings (Executive summary, Inventory, Dependency graph, EOL & vulnerable dependencies, Module risk & tiers, Recommended next steps); tells the user the Architect accepts the assessment as the planning baseline by approving the exported report; never modifies the legacy repo.
- `agents/assessor.py`: `app = build_tool_agent_graph(agent_type="discovery", tools=TOOLS, checkpoint_name="discovery")`.

- [x] Step 1: failing tests — clone refuses `http://`, `file://`, and `https://internal.corp/x.git`; with a local fixture repo (`git init` in tmp, bypass through an injected path hook) `assess_legacy_repository` returns a digest and stores the assessment in session; `export_assessment_report` writes a `.docx`; `get_module_detail` on an unknown module lists known ones; prompt mentions "read-only" and "planning baseline".
- [x] Step 2–4: FAIL → implement → PASS. Step 5: checkpoint.

### Task 4: Requirements (migration intent) brief, tools, prompt, graph

**Files:** `requirements_modernization_agent/brief.py`, `tools/brief_tools.py`, `prompts/migration_intent_prompt.py`, `agents/intake.py`. Test: `backend/tests/requirements_modernization/test_brief.py`, `test_brief_tools.py`.

**Interfaces:**
- `MigrationIntentBrief` (pydantic): `system_name: str`, `business_drivers: list[str]`, `current_state: StackState(stack, description)`, `target_state: StackState`, `in_scope`, `out_of_scope`, `constraints`, `success_criteria`, `stakeholders: list[Stakeholder(name, role)]`, `assumptions`, `risks`, `open_questions`, `legacy_repository: LegacyRepo | None(provider, project, name, url)`. `missing_sections() -> list[str]` (required: system_name, business_drivers, current_state.stack, target_state.stack, in_scope, constraints, success_criteria). `to_markdown() -> str` (title `# Migration Intent Brief — {system}` + one `##` per section).
- Tools: `record_migration_intent(brief_json: str)` (validates; incomplete → returns the missing sections and does not persist; complete → persists `persist_artifact(run_id, "requirements_modernization", brief.model_dump())` and returns `MIGRATION_INTENT_PAYLOAD::` + markdown), `export_migration_brief(filename)`, `list_board_projects()` (read, bound connector), `create_migration_work_items(project: str, items_json: str)` (Consequential: `authorize_consequential("requirements_modernization", action=...)` first; creates one Epic then child items via the connector's `write_adapter("create_item", ...)`).
- `MIGRATION_INTENT_SYS_MESSAGE`: intake order — why (drivers), from→to (current and target stack), scope in/out, constraints (deadline, budget, compliance, change-freeze, interfaces that must not change), success criteria (measurable), legacy repository location; at most three focused questions per turn; never invents facts; records the brief with the tool once the required sections are known; replies with the brief as a markdown document; next step is Discovery & Assessment; board writes only after explicit confirmation.

- [x] Step 1: failing tests — `missing_sections` on an empty brief lists all seven; a complete brief renders `## Why this modernization` etc.; `record_migration_intent` with a missing `success_criteria` does not call `persist_artifact` (monkeypatched) and names the gap; with no actor bound `create_migration_work_items` refuses before touching a connector.
- [x] Step 2–4. Step 5: checkpoint.

### Task 5: Data model — migration 0057

**Files:** Create `backend/migrations/versions/0057_track3_phase1_agents.py`; modify `shared/models/orm.py` (Run columns), `shared/services/artifact_service.py` (`_COLUMN_MAP`), `config/context_broker.py` (`_ARTIFACT_FIELDS`, `_ARTIFACT_FORMATTERS` for `migration_intent_payload`), `tests/orchestrator2/test_deliverables_schema.py`.

Migration upgrade:
1. `runs.migration_intent_payload JSONB NULL`, `runs.discovery_artifacts JSONB NULL`.
2. Drop + recreate `ck_orchestrator_deliverables_agent_id` with the eleven ids.
3. `INSERT INTO permissions` `artifact:approve_requirements_modernization`, `artifact:approve_discovery`; grants: (`ba`, `project_admin`) and (`architect`, `project_admin`) respectively. Downgrade reverses all three.

- [x] Step 1: update `test_deliverables_schema.py` to read the CHECK from the newest migration that defines it; add a test that `Run` has both columns. Run → FAIL.
- [x] Step 2: implement migration + ORM + maps. Upgrade the TEST database only: `POSTGRES_MIGRATIONS_CONN_STRING=<.env.test DSN> uv run alembic upgrade head`.
- [x] Step 3: run → PASS (after Task 6 lands the ids in `AGENT_IDS`). Step 4: checkpoint.

### Task 6: Registry, RBAC and permission wiring (backend)

**Files:** `config/agent_registry.py`, `agents_orchestrator/orchestrator2/registry.py`, `agents_orchestrator/orchestrator2/deliverables.py`, `shared/authz/permissions.py`, `shared/governance/routing.py`, `shared/routers/agent_profiles.py`, `shared/routers/runs.py`, `tests/orchestrator2/test_track_scoping.py` (new real-agent tests).

`AGENT_REGISTRY` entries:
```python
"requirements_modernization": AgentDefinition(
    id="requirements_modernization", name="Requirements Agent (Migration Intent)",
    pipeline_position=1, input_artifacts=[], output_artifact="migration_intent_payload",
    route_path="/requirements-modernization", gate_type="approval_required",
    required_capabilities=["req.migration_intent.capture", "req.migration_intent.brief",
                           "board.read", "artifact.write"],
    optional_capabilities=["board.write", "doc.export"]),
"discovery": AgentDefinition(
    id="discovery", name="Discovery & Assessment Agent", pipeline_position=2,
    input_artifacts=["migration_intent_payload"], output_artifact="discovery_artifacts",
    route_path="/discovery", gate_type="approval_required",
    required_capabilities=["discovery.repo.clone", "discovery.dependency.graph.build",
                           "discovery.dependency.eol.scan", "discovery.dependency.cve.scan",
                           "discovery.module.risk.score", "discovery.module.tier.classify",
                           "artifact.write"],
    optional_capabilities=["doc.export"]),
```
`TRACK_PORTFOLIOS["modernization"] = ["requirements_modernization", "discovery"]` (after the REGISTRY entries exist). `_OWNER_OF` gains both. `_PHASE_PERMISSION`, `_PERMISSION_CATALOG`, `_ROLE_PERMISSIONS` (ba, architect, project_admin) gain the two approve permissions. `routing.AGENT_OWNER_ROLE["requirements_modernization"] = "ba"`. `PIPELINE_ORDER` gains both. `_run_stage_output_dir` maps both to their `output` segment.

- [x] Step 1: failing tests in `test_track_scoping.py` — `agent_ids_for_track("modernization") == ("requirements_modernization", "discovery")`; greenfield portfolio unchanged; `run_agent("discovery", track="greenfield")` yields an error and never `agent.selected`; `run_agent("requirements", track="modernization")` likewise; `registry_for_track("modernization")` resolves both capabilities; `verify_agent_ownership() == []`.
- [x] Step 2: FAIL → implement → run the whole baseline set (`tests/orchestrator2 tests/test_agent_reach_matches_frontend.py tests/test_agent_access.py tests/test_agent_ownership_is_single_sourced.py`), fix every "nine agents" pin that is now eleven by deriving from the registry rather than re-hardcoding, → PASS. Step 3: checkpoint.

### Task 7: Router — Track 3 voice, track-aware name matching

**Files:** `agents_orchestrator/orchestrator2/router.py`; tests `tests/orchestrator2/test_router_track3.py`.

- `DISPLAY_NAMES["requirements_modernization"] = "Requirements (migration intent)"`, `DISPLAY_NAMES["discovery"] = "Discovery & Assessment"`; `_CAPABILITIES` for both (routing text).
- `_NAME_TO_IDS: dict[str, tuple[str, ...]]` built from display names, raw ids and `_EXTRA_NAMES = {"requirements_modernization": ("requirements", "migration intent"), "discovery": ("discovery and assessment",)}`. `prefilter(text, valid_ids=None)` returns the single matching id inside `valid_ids` (default: `agent_ids_for_track("greenfield")`), else `None`.
- `_MODERNIZATION_PROMPT_TEMPLATE` (own template, same routing rules, Track 3 vocabulary, greeting behaviour: introduce Code Modernization, start with Requirements capturing migration intent, Discovery next, ask them to describe the modernization; roster generated as today; names the unbuilt Track 3 agents as not available yet). `_system_prompt(capabilities, track="greenfield")` and `_system_prompt_with_continuity(last_agent, capabilities, track=...)` choose the template; `route()` passes `track`.

- [x] Step 1: failing tests — greenfield prompt byte-identical to today's (existing pin); modernization prompt contains "Code Modernization", "migration intent", "Discovery & Assessment" and no `route_to_` outside the roster; `prefilter("run the requirements agent")` → `requirements`; `prefilter("run the requirements agent", valid_ids=("requirements_modernization","discovery"))` → `requirements_modernization`; `prefilter("run the discovery agent", valid_ids=...)` → `discovery`; `route(... track="modernization")` binds exactly two tools (fake LLM).
- [x] Step 2–4. Step 5: checkpoint.

### Task 8: Standalone access — agent access AND track

**Files:** `shared/authz/agent_access.py`; test `tests/test_agent_access_track.py` (real test DB, `org_project` pattern from `tests/test_agent_access.py`).

- `assert_agent_access_for_chat_on_track(db, *, tenant_id, project_id, user_id, agent_id) -> str` — same project resolution/membership/role reach as `assert_agent_access_for_chat`, then 403 `"The {label} agent is not part of this project's delivery track."` unless `agent_id in TRACK_PORTFOLIOS[project.track or "greenfield"]`. Shares a private `_resolve_chat_project` helper with the existing function so Track 1 behaviour is byte-for-byte unchanged.

- [x] Step 1: failing tests — BA on a modernization project reaches `requirements_modernization`; BA on a greenfield project is refused it (403, track message); architect on modernization reaches `discovery`, developer is refused (reach); non-member 404.
- [x] Step 2–4. Step 5: checkpoint.

### Task 9: Standalone WS routers + read endpoints

**Files:** `modernization_common/standalone.py`, `discovery_agent/discovery_agent_api.py`, `requirements_modernization_agent/requirements_modernization_agent_api.py`, `shared/routers/modernization.py`, `process_api.py`; tests `tests/test_modernization_standalone.py`.

- `serve_agent_socket(websocket, *, agent_id, label, graph, system_prompt)` — ticket redeem (4401), connect, per `user_message_with_files` turn: project from `pipeline_context.project_id`; `assert_agent_access_for_chat_on_track`; contextvars (session/user/tenant/project, run = chat run for (project, agent)); consent from `is_approval_message(task_intent)`; `bound_connector(agent_id, ...)`; upstream context from `build_context_for_project`; attachments; `persist_turn` user+agent; stream `stream_chunk`; always `stream_end` then `activity_update{type: complete}`; errors as an `agent_response` from "Error Agent" (sanitised).
- Read endpoints (behind `_VIEW_DEP` + `require_agent_access(<agent>)` + track check): `GET /projects/{project_id}/modernization/migration-intent`, `GET /projects/{project_id}/modernization/discovery` → `{"runId", "updatedAt", "payload"}` from the newest run holding the column, `payload: null` when none.
- Mount: `/sdlc/agent/requirements-modernization`, `/sdlc/agent/discovery`, and the modernization router.

- [x] Step 1: failing tests — WS without ticket closes 4401 (TestClient); a turn for a greenfield project produces the track refusal and a terminal `activity_update`; read endpoint returns the newest payload and 403s a developer on discovery.
- [x] Step 2–4. Step 5: checkpoint.

### Task 10: Frontend identity, RBAC mirror, rosters, Orchestrator enum, chat map, stage picker

**Files:** see Frontend list; tests: `lib/__tests__/track3-roster.test.ts`, update `app/api/__tests__/chat-agent-map.test.ts`, orchestrator protocol test for new ids.

- `builtAgentsForTrack(track)` → greenfield/enhancement: `BUILT_AGENTS`; modernization: `["requirements_modernization","discovery"]`; rpa_infra/data_engineering: `BUILT_AGENTS` (unchanged behaviour — flagged as a separate decision).
- `phaseRoute("requirements_modernization") === "requirements-modernization"`.
- `ToolsStagePicker` takes `track?: DeliveryTrack`; stages = `agentsForTrack(track)` mapped to agent ids (`review`→`code_review`); settings page and create dialog pass the track.

- [x] Step 1: failing vitest — modernization roster first two are the new ids; tile state on a modernization project: BA → owner on requirements_modernization, architect → owner on discovery, design → coming_soon; `agentWsPath` maps both; `OrchestratorAgentId.parse("discovery")` succeeds; stage picker for modernization lists "Discovery & Assessment".
- [x] Step 2: FAIL → implement → `npx vitest run` subset + `npx tsc --noEmit` → PASS. Step 3: checkpoint.

### Task 11: Frontend pages

**Files:** `lib/schemas/modernization.ts`, `lib/api/modernization.ts`, `lib/api/query-keys.ts`, `components/modernization/{migration-brief-card,assessment-summary,module-risk-table,dependency-list,flags-panel}.tsx`, the two pages; tests `components/modernization/__tests__/*.test.tsx`.

- Requirements (migration intent) page: header + model picker + "Run Requirements agent"; migration brief card (why, from → to, scope, constraints, success criteria, legacy repo, open questions) or empty state; documents list (stage `requirements_modernization`) for the Sign-off; chat drawer on `agent: "requirements_modernization"`.
- Discovery page: header + model picker + "Run Discovery & Assessment"; summary strip; tabs Modules (tier filter, score bar, tier badge, detail with factors) / Dependencies / Flags; documents list (stage `discovery`) for the Sign-off; chat drawer on `agent: "discovery"`.

- [x] Step 1: failing render tests with fixture payloads (brief card shows drivers and target stack; module table filters to `manual`; flags panel lists an EOL runtime).
- [x] Step 2–4. Step 5: checkpoint.

### Task 12: End-to-end verification on the running stack

- [x] Start Docker Desktop → `docker compose up -d redis`; backend `uv run uvicorn process_api:app --reload --port 8004 --ws-max-size 1000000`; frontend `npm run dev`; dev DB migrated to 0057.
- [ ] As the project's BU Admin/Project Admin persona (`DEV_LOGINS.txt`): create a Track 3 project with Azure DevOps wired to the Discovery stage. *(2026-09-10: Track 3 project created as `sarthakk2004`; no connector wired — Discovery cloned a public GitHub repo. The private-clone credential path is unit-tested only.)*
- [x] Standalone: drive both WS endpoints with a script (ticket → turns) — brief recorded, repository cloned read-only, assessment persisted; read endpoints return both payloads.
- [x] Orchestrator: `orchestrator2` WS script — "hi" gets the Track 3 introduction; a migration description routes to Requirements (migration intent); "proceed to discovery and assessment" routes to Discovery; Deliverables lists both documents; a greenfield project still offers the nine. *(Greenfield checked by the router tests' byte-identical prompt pin, not live.)*
- [ ] Ask the user to confirm the pages and the Orchestrator in their own browser (Chrome MCP cannot reach this dev server).

## Self-review notes

- Spec coverage: Portfolio 2 rows 1–2 (capabilities, owners, gates) → Tasks 1, 3, 4, 6; §2.2 tile states → Task 10; §2.3 cockpit roster by track + per-agent access → Phase 0 + Tasks 6–7; §4.3 enforcement → Task 8; Part 5 checklist → Tasks 3–6, 9, 12.
- Explicitly out of scope: Design/Strategy/Development/... for Track 3 (still "Coming soon"), golden-master capture (field reserved, `not_captured`), target-repo half of the dual-repo config, Track 4/5 tile behaviour, standalone track checks on the nine Track 1 handlers.
