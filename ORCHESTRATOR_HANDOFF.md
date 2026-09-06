# Orchestrator rebuild — session handoff

**Written at the end of Phase 3. Phases 0–3 are complete, reviewed and green. Phase 4 is
next and nothing blocks it.**

Read this first, then `orchestrator_instruction.md` (the spec of record). Everything below
is fact checked against the repo at the time of writing, not recalled.

- Branch: **`feature/orchestrator-rebuild`** — 41 commits ahead of `f7c9bfd9`. Not pushed.
- Backend tests: **363 passing** (`tests/orchestrator2/` + one authz test)
- Frontend tests: **644 passing**, typecheck and lint clean on orchestrator files

---

## 1. What was asked for

The user's own framing, because the wording matters and has driven several decisions:

> There were two surfaces both called "the orchestrator": the **Copilot** (reached by
> "Run agent" on the Projects page) and the **Orchestrator** (sidebar tab). **Merge both
> into one thing called Orchestrator.**

Requirements, as given:

1. **Keep from the Copilot:** the right-hand **Artifacts / Activity / Context** tabs, and
   the chat.
2. **Drop the left pipeline rail.** *"There won't be a linearity that, after one agent,
   the next comes. Any agent can come at any time according to the chat."*
3. **No gates, no sign-off.** *"There is no sign-off or anything that we'll be doing in
   this orchestrator… because the project admin is running it."* The PIPELINE panel with
   sign-off badges became the Artifacts/Activity/Context panel.
4. **"Run Agent" leads directly to the Orchestrator tab.**
5. **Project Admin only** — that role is the only one holding access to all nine agents.
6. **Nine agents wired in, plus a Context Agent.** When the admin asks for something
   ("I need a PRD") *"the right agent starts automatically and does the task fully."*
7. **All generated documents in the Artifacts tab, grouped agent-wise**, with per-agent
   quirks carried over (e.g. Development pulling ADO code appears under Development).
   **← this is Phase 4, and it is the largest remaining piece.**
8. **Hierarchy is inside a project.** Every run belongs to a project and follows that
   project's integrations, connectors and models.
9. *"The frontend should be proper enterprise-level. First, fix the frontend fully based
   on what our needs are, and then route the agents into it."*

Later clarifications from the user:

- The ninth agent is the **Project Manager agent** (formerly "help agent") and must be
  called that. Its internal id is `plan` — that id is in route paths, artifact columns and
  the API contract, so only the *label* changed.
- **Build a new engine.** *"There were a lot of issues in the old one… It would not switch
  automatically, it would fault, and it would not properly do the stuff. It should be
  based on the capabilities of our current standalone agents."*
- *"What we are borrowing from our standalone agents are their capabilities, but not the
  full agents as is — those agents have a lot of permissions and everything that we don't
  need right now in the orchestrator."*
- **BYOK must be project-scoped:** *"If an orchestration is from a certain project, then
  that key that has been assigned to that project will work in that orchestrator."*
- E2E tests deferred: *"We can do the tests later… I don't think the tests are needed
  until everything has been built."* (Unit/integration tests were NOT deferred and are
  extensive.)
- *"Keep on noting down all decisions that you have taken and everything that you have
  done"* — hence `orchestrator_instruction.md` and the phase ledgers.

---

## 2. Where everything lives

### Spec and decisions

| Path | What it is |
|---|---|
| `orchestrator_instruction.md` | **The spec of record.** User's original message at the top, then requirements, architecture audit, decisions D1–D10a, design, procedure, and a section per phase outcome (§12 Phase 2, §13 Phase 3, §14 the Phase 3 blockers). Read §8 for the phase plan. |
| `docs/superpowers/plans/2026-09-05-orchestrator-phase-0-1.md` | Phase 0+1 plan |
| `docs/superpowers/plans/2026-09-05-orchestrator-phase-2.md` | Phase 2 plan |
| `docs/superpowers/plans/2026-09-05-orchestrator-phase-3.md` | Phase 3 plan |
| `.superpowers/sdd/2026-09-05-orchestrator-phase-{0-1,2,3}/progress.md` | **Phase ledgers.** Every ruling (P2-R1…, P3-R1…P3-R19) with its reasoning AND its stated cost if wrong. `.superpowers/` is gitignored, so these are local-only — read them before re-deciding anything. |
| `.superpowers/sdd/2026-09-05-orchestrator-phase-3/whole-branch-backend-review.md` | 853-line backend review (48 mutations, 19,800-case differential). All findings fixed. |
| `.superpowers/sdd/2026-09-05-orchestrator-phase-3/whole-branch-frontend-review.md` | 31KB frontend + protocol-contract review. All findings fixed. |

### The new engine (backend)

`backend/agents_orchestrator/orchestrator2/` — 3,046 lines:

| File | Lines | Role |
|---|---|---|
| `registry.py` | 362 | The nine agents, lazily-loaded graph + prompt each, `validate_registry()` |
| `dispatch.py` | 529 | `run_agent(...)` — one agent per turn, streams protocol events, resolves BYOK |
| `router.py` | 923 | `prefilter()` + `route()` — the Context Agent |
| `context.py` | 472 | `handoff_context()` — what earlier agents produced on the run |
| `ws.py` | 760 | The authenticated WebSocket; access checks; the turn loop |

Mounted at `backend/process_api.py:991` → `/sdlc/agent/orchestrator2/ws`.
`validate_registry()` is called in `lifespan` at `process_api.py:611` — **the boot refuses
to start if any agent's graph or prompt is missing.**

Tests: `backend/tests/orchestrator2/` — 5,184 lines across 11 files.
Plus `backend/tests/test_project_admin_tier_addressed_by_identity.py`.

### The frontend

- `frontend/lib/orchestrator/protocol.ts` — **the wire contract** (Zod union). Read this
  before changing anything on either side; see the trap in §6.
- `frontend/lib/orchestrator/use-orchestrator-socket.ts` — the socket hook
- `frontend/lib/orchestrator/access.ts` — `canUseOrchestrator(role) => role === "project_admin"`
- `frontend/lib/orchestrator/types.ts` — display names; `PHASE_LABEL` in `lib/agents.ts`
- `frontend/components/orchestrator/cockpit.tsx` — the main surface
- `frontend/components/orchestrator/artifacts-panel.tsx` — Artifacts / Activity / Context
- Routes: `app/(app)/orchestrator/page.tsx` (global) and
  `app/(app)/projects/[id]/orchestrator/page.tsx` (project-locked)

### The old engines — still live, deleted in Phase 5

- `backend/agents_orchestrator/orchestrator/copilot_api.py`
- `backend/agents_orchestrator/orchestrator/orchestrator_api.py`

**Do not delete these yet.** The Copilot is currently the only surface actually running
agents in production. Phase 1 *unlinked* it (nothing navigates there) but left the route
reachable on purpose, so there is never a window with no working orchestration.

### Tools

- `backend/scripts/live_routing_check.py` — **the only way to measure routing quality.**
  Makes real model calls against the seeded dev tenant. Run it after ANY change to the
  routing prompt or the capability descriptions. Currently 28/29 (one recorded boundary).

---

## 3. Starting the stack

`docs/local-setup.md` is the reference, but it is **wrong in two places** — both cost
real time this session:

| Docs say | Actually |
|---|---|
| FastAPI on port **8001** | **8004** — `frontend/.env.local` has `FASTAPI_INTERNAL_URL=http://localhost:8004` |
| PostgreSQL is native on 5432 | **Compose Postgres on 5433** — `backend/.env` has `POSTGRES_CONN_STRING=postgresql+asyncpg://postgres:…@localhost:5433/sdlc_product`. Note it is `backend/.env`, not the repo-root `.env`, which has neither. |

```bash
# 1. containers (Docker Desktop must be running)
docker compose up -d redis postgres     # sdlc-redis:6379, sdlc-postgres:5433, sdlc-litellm:4000
docker ps                               # confirm healthy before trusting anything

# 2. backend
cd backend && uv run uvicorn process_api:app --reload --port 8004

# 3. frontend
cd frontend && npm run dev              # :3000
```

**Running tests — this trips people up:**

```bash
cd backend && uv run python -m pytest tests/orchestrator2/ -q     # correct
cd backend && uv run pytest tests/orchestrator2/ -q               # FAILS: ModuleNotFoundError: config
```

Frontend: `npm run typecheck`, `npm run lint`, `npx vitest run`.
`npm run lint` has **2 pre-existing errors** in `agent-studio/__tests__/skills-tab.test.tsx`
and `app/__tests__/add-model-dialog.test.tsx` — not from this branch. Lint your own files
with `npx eslint components/orchestrator lib/orchestrator`.

**Seeded dev data** (read out of the DB, not invented):

- tenant `dfee0d2f-345e-430e-8084-7ab7276cc5b8`
- project `c3b0cd34-6657-4f91-b1f2-04f394502f81` ("reall") — has `project_model_selections`
- two providers with `status='valid'` and real secrets: "Anthropicnewkwy" (anthropic),
  "Azure key" (azure)
- `sarthakk2004@gmail.com` is the only `project_admin`. `DEV_LOGINS.txt` has the personas;
  `unseed_dev_personas.py` has been run, so most seeded personas no longer exist.

---

## 4. What is built

### Phase 0–1 — contract and frontend ✅

The merged Orchestrator UI against a pinned protocol. The mock engine was deleted. "Run
agent" moved and gated to Project Admin. The linear stage rail is gone.

### Phase 2 — registry and dispatch ✅

**The finding that explains the user's original complaint.** The old engine's
`_system_prompt_for()` handled **three** of nine stages. Verified by execution:

```
WITH a system prompt:    requirements, design, development
WITHOUT (no instructions at all): plan, code_review, security, testing,
                                  deployment, documentation
```

Its own docstring says such an agent *"will churn without producing a useful reply"*. And
`_graph_for()` had no `plan` branch at all. Both failed soft — `except Exception:
logger.warning(...)` and a `None` return — so the symptom was an agent that answered
vaguely or not at all, never an error anyone could chase.

**Only 3 of 9 agents were ever fully wired.** All nine now resolve graph + prompt, verified
by execution (requirements 25,844 chars of prompt, design 20,553, plan 6,616,
development 31,719, code_review 3,854, security 4,380, deployment 12,277,
documentation 8,035; `testing` is invoke-mode and takes none).

BYOK is project-scoped: the turn's project comes from the verified `runs` row, never the
client frame. `run_agent` takes `project_id` as a keyword with **no default** on purpose.

### Phase 3 — the Context Agent ✅

- **Pre-filter**: an explicit imperative naming an agent costs **no model call**. Requires
  a VERB then a NAME then the literal word "agent" — the old `stage_switch.py` matched an
  alias *anywhere*, which is why "document this function" hijacked a turn.
- **Router**: everything else goes to a model that reads for MEANING. All nine are
  candidates every turn. No ordering anywhere. A hallucinated id is refused visibly.
- **Context propagation**: `handoff_context()` includes everything on the run, labelled by
  producer, ordered by nothing. The old `_upstream_context` sliced by `STAGE_ORDER[:idx]`,
  so running Design after Development showed Design nothing — **the linearity the user
  rejected lived in the context layer too**, and fixing routing alone would not have found
  it.
- **Per-project access**: a Project Admin of project A cannot drive project B's run, even
  in the same tenant.
- **Frontend**: the picker defaults to "Let the Orchestrator choose"; picking an agent
  overrides the router for that turn.

**Live measurement** (`scripts/live_routing_check.py`, real BYOK, project-scoped):

```
"I need a PRD for the billing rework"  ->  requirements     (the headline requirement)
28 of 29 correct, 0 errors
```

---

## 5. How this branch was worked, and why

Adopt this or knowingly drop it — but know what it caught.

**Every task: one agent implements, a different agent reviews.** Independent review found
every significant defect on this branch. Self-review found comparatively little.

**A green suite is not evidence. Mutation is.** Break the implementation, run the tests,
confirm something fails. Across the branch this caught **tests that passed against
knowingly broken code in ten separate rounds**, including three mutations that survived an
entire 638-test frontend suite, and one that restored the exact defect the commit it
belonged to was *named after*.

Two harness rules, learned expensively:

- **A mutation reporting SURVIVED is not a result until you show the diff.**
  `str.replace` no-ops silently on a non-matching pattern and the suite then prints green
  for an unmodified file. Assert the anchor exists; print `git diff --numstat`. A kill is
  self-evidencing; a survival is not.
- **Print `sys.executable` in any harness.** A heredoc run as bare `python` uses the SYSTEM
  interpreter, not `backend/.venv`, and produces spurious `pkgutil.ImpImporter` failures
  that look like a boot failure and are not.
- Restore mutations in a `finally`. One harness here crashed mid-mutation and left it on
  disk.

**The recurring defect of this rebuild — NINE instances — is prose asserting a guarantee
the code does not provide.** Escalating in severity:

1. Comments claiming invariants the code only partly held (six instances).
2. A docstring **and a test together** certifying a silent data loss as deliberate — the
   router dropped every assistant turn this platform persists, because it accepted
   `assistant`/`ai` while the platform writes `agent`. Four turns in, two turns seen.
3. A **commit message** claiming a docstring sweep it had not done.
4. A test-file comment saying the access-control function was "exercised on its own in
   test_ws_project_scope.py" — and that file stubbed it too. **That comment is why nobody
   noticed the most security-critical function on the branch had zero coverage.**

When you write a comment asserting a guarantee, verify the code delivers it on every path
or narrow the wording.

---

## 6. Traps specific to this codebase

**The protocol union drops frames silently.** `frontend/lib/orchestrator/protocol.ts`
validates every inbound frame with `safeParse` and **drops** anything that fails. This has
cost two production-shaped bugs:

- `ErrorEvent.agent` is the nine-value enum; the backend put an *unresolved* id there, so
  the one event whose job was "that agent does not exist" was the one guaranteed not to
  arrive.
- `StreamChunkEvent.content` is `z.string()`; the backend forwarded Anthropic's block-LIST
  content raw. `json.dumps` accepts a list, so nothing failed server-side — the agent
  simply appeared to say nothing.

**Before changing either side of the wire, check the other.** There is a test
(`test_every_emitted_event_type_is_in_the_frontend_union`) but it does not cover field
shapes.

**`router._content_text` strips; `dispatch._stream_text` does not.** They look
interchangeable and are not — stripping is right for a routing decision and destroys a
stream ("Hello " + "world" → "Helloworld"). A test exists to stop a future tidy-up merging
them.

**`AGENT_REGISTRY[id].output_artifact` is the only source of an agent's artifact column.**
Do not type the list out. `requirements` writes `requirements_payload`, NOT
`requirements_artifacts` — and a `requirements_artifacts` column also exists, so a
hardcoded guess reads `None`, which is indistinguishable from "that stage has not run".

**`get_db_session_superuser()` bypasses row-level security.** Any read through it needs an
explicit tenant predicate. This has been found twice in `copilot_api.py`.

---

## 7. What is left

### Phase 4 — Artifacts and per-agent quirks ← **START HERE**

This is requirement 7, and the largest remaining piece of the user's spec.

Today the new cockpit passes the panel `artifacts={[]}` and `runId=""`. The new engine
writes **no artifacts at all**. Needed:

- artifact persistence from `orchestrator2`
- **agent-wise grouping** in the Artifacts tab (explicitly asked for)
- per-agent surfaces — the Development code tree (`code-tree-view.tsx` exists), ADO code
  pulls appearing under Development, and whatever each other agent surfaces

**Note the precedent:** the Activity tab was in exactly this state — declared in the
protocol, rendered by the panel, and wired to a literal empty array, which on screen is
indistinguishable from an agent that never uses tools. Do not leave the Artifacts tab
half-wired the same way.

### Phase 5 — retire the old engines

Delete `copilot_api.py`, `orchestrator_api.py` and the Copilot page, once the new engine is
proven in use. Not before.

### Carried debt — recorded, not forgotten

1. **RLS is inert in this deployment.** `POSTGRES_CONN_STRING` connects as `postgres`,
   which is `rolsuper` AND `rolbypassrls`. Superusers bypass row-level security
   unconditionally and `FORCE` does not apply to them, so any query relying on RLS instead
   of an explicit predicate reads **every tenant's rows**. Consequence: a person who is
   `project_admin` in tenant A and `developer` in tenant B resolves as `project_admin` in
   B and gets an accepted socket — what stops it becoming access is the explicit
   `Run.tenant_id` / `Project.tenant_id` predicates. **Fix is a non-superuser application
   role**; `POSTGRES_MIGRATIONS_CONN_STRING` already exists so `postgres` can stay for
   migrations. The two `tests/test_m7_rbac.py` cross-tenant failures are **real** and
   should stay red until then — they are the only tests in the repo that notice.
2. **No inbound WebSocket frame size limit.** `_remember` bounds what is retained, but a
   20 MB frame still arrives first. Server configuration (`--ws-max-size`).
3. **`copilot_api.py` has three known defects** — an RLS-bypassing read with no tenant
   filter, no `project_id` in model resolution, an env-key fallback in `_classify_switch`.
   All in the engine Phase 5 deletes, so weigh whether fixing is worth it.
4. **`assert_all_routes_protected` skips every WebSocket route**, so the socket's access
   control is guarded by its own tests and nothing systemic.
5. **The "no env fallback" guarantee is one layer deep.** `orchestrator2/dispatch.py` reads
   no env key, but `testing_agent/config/shared.py:154-176` still falls back to
   `ANTHROPIC_API_KEY`, and the other eight agents' executor boundaries are unaudited. A
   contextvar reaches only as far as the context does.
6. **Context truncation may be too aggressive.** On a full nine-agent run each artifact
   gets ~2,400 characters (~600 tokens). A PRD cut to 2,400 chars is close to useless. The
   real answer is summarisation or a token budget, not a bigger character cap. **Needs a
   product decision.**
7. **`"review this"` does not route** — recorded as a known boundary in
   `live_routing_check.py` and deliberately left failing rather than tuned away.
8. **E2E rewrite**, deferred by the user in Phase 1.

---

## 8. Standing preferences

- **Always ask before `git push` or opening/updating a PR**, even if an earlier push was
  approved this session.
- **Add `UjjwalTyagi5` as reviewer** on aisdlc-ui PRs.
- In prose say **Business Unit**, never "workspace" — code identifiers still literally say
  `workspace`.
- **Check `docker ps` before trusting any dev-server or live-verification work.**
- Use **real seeded personas** matching the actual role/scope for live verification, not
  synthetic one-offs.
- Tests run against `sdlc_product_test`, never `sdlc_product`. Verify a project's suite
  does not share a DSN with the live app before running it broadly.
- The user asked for decisions to be **written down as they are made**, with reasoning.
  Keep `orchestrator_instruction.md` and the phase ledger current.

---

## 9. First fifteen minutes of the next session

```bash
cd C:\Users\srk02\Downloads\Frontend\aisdlc-ui
git branch --show-current                 # expect feature/orchestrator-rebuild
git log --oneline -5
git status --porcelain -uno               # expect clean

docker ps                                 # sdlc-postgres, sdlc-redis, sdlc-litellm
cd backend && uv run python -m pytest tests/orchestrator2/ -q          # expect 354
cd ../frontend && npx vitest run                                        # expect 644
```

Then read, in order: `orchestrator_instruction.md` §1 (the requirement), §8 (the phase
plan), §14 (how Phase 3 closed), and
`.superpowers/sdd/2026-09-05-orchestrator-phase-3/progress.md` (the rulings).

Phase 4 has no plan written yet. Write one before touching code.
