# Orchestrator Phase 5 — retirement, and real session history

**Status:** approved 2026-09-07. Implements `orchestrator_instruction.md` §8's Phase 5
and §5.3's server-backed sessions, which Phase 1 deferred and Phases 2–4 never resumed.

Phase 5 was specified as one thing — *"Remove `copilot_api.py` and `orchestrator_api.py`
once nothing depends on them"* — and grew a second when the scope was discussed:

> *"in the orchestrator, on the left side, there is a history of all the chats. Opening
> that history will open up that full chat that the person had, and he can continue that
> chat also."*

That is §5.3, unbuilt. It is **5B** below. The retirement is **5A**.

---

## 1. Why these two belong in one phase

They are the same statement from opposite sides. The Copilot is deleted because the
Orchestrator replaces it — and until the Orchestrator can reopen a past conversation,
that replacement is not true. Today the Copilot page is the only surface that can open a
historical run, which is exactly why Phase 1's ruling R10 left two links pointing at it.

5A removes the old surface; 5B gives the new one the capability that justified removing
it. Shipping 5A alone would take a capability away.

## 2. What the retirement actually touches

Measured, not estimated.

**Backend production coupling is two places.** `process_api.py:24-25` mounts both
routers, and `shared/routers/runs.py:1004` imports `request_turn_cancel` from
`copilot_api`. A grep for the two module names hits 39 files; everything else is
`tests/copilot/` (which goes with the engine) or prose in docstrings.

**`lib/copilot/` cannot simply be deleted.** The Orchestrator imports four of its
modules — this is the largest single risk in 5A:

| Module | Used by the Orchestrator for |
|---|---|
| `artifacts.ts` | `ArtifactKind`, `Artifact`, `ARTIFACT_EVENTS`, `rendererFor`, `ArtifactsRead`, `TranscriptRead` |
| `stages.ts` | `COPILOT_STAGES`, `stageLabel`, `ownerRoleLabel` — the panel's grouping order and labels |
| `types.ts` | `ChoiceCard`, `GateState` |
| `use-copilot.ts` | `CopilotActivityItem`, `CopilotConnState` — **types only**, not the hook |

These MOVE to `lib/orchestrator/`. Only the Copilot-specific remainder is deleted.

**The `/api/chat` default.** `agentWsPath()` maps all nine agents explicitly and falls
through to `/sdlc/agent/orchestrator/ws` — the engine being deleted. Every caller
(`useAgentChat`) passes an explicit agent, so the default is reachable only by an absent
or unmapped id. It becomes an explicit refusal rather than a silent route to some other
engine: an unmapped agent quietly answering as something else is the failure class this
whole rebuild exists to remove. `app/api/__tests__/chat-agent-map.test.ts` already pins
this table, written in anticipation of this phase.

## 3. Decisions

| # | Decision | Source |
|---|---|---|
| D18 | Retire `copilot_api.py`, `orchestrator_api.py`, the Copilot page, its components, its WS-ticket route and the three `/runs/{id}/copilot/*` endpoints. | Spec §8, user |
| D19 | The four `lib/copilot/` modules the Orchestrator depends on MOVE to `lib/orchestrator/` rather than being deleted or left behind in a deleted directory. | Claude |
| D20 | `/api/chat` refuses an unmapped agent (HTTP 400) instead of routing it anywhere. | Claude |
| D21 | The `/runs` page — a platform-wide table including pipeline and webhook runs — points at the existing read-only `/runs/[id]/conversation`, not at any chat surface. History is for reading; the Orchestrator rail is for continuing. | User, from the 5B discussion |
| D22 | **Orchestrator sessions become server-backed**, keyed `session_id == run_id`, the convention the Copilot already used and that `ensure_session_with_id` exists for. | User, direct |
| D23 | A new chat is not persisted until its first turn. An empty session has nothing to restore, and creating a run on rail-click would mint runs nobody used. | Claude |
| D24 | The WS frame-size limit and the route-protection sweep's WS blindness are fixed here, because after this the orchestrator2 socket is the only orchestration socket left. | User, chosen from options |

## 4. 5A — Retirement

### 4.1 Deleted

**Backend — the whole `agents_orchestrator/orchestrator/` package.** It holds exactly
four modules, and every importer of the other two is itself inside the deleted set:

- `copilot_api.py` (2,722 lines)
- `orchestrator_api.py` (1,970 lines)
- `copilot_cards.py` — imported only by `copilot_api.py` and `tests/copilot/`
- `stage_switch.py` — imported only by `copilot_api.py` and `tests/copilot/`

Plus:
- its two mounts in `process_api.py`
- `copilot_advance`, `copilot_set_stage`, `copilot_cancel_turn` in `shared/routers/runs.py`
- `tests/copilot/` (22 files) and the `copilot_api` import in `tests/test_gate_self_approval.py`

**`orchestrator2` mentions `stage_switch.py` five times, all in prose** — docstrings and
comments explaining what the old router did wrong and why this one is built differently.
None is an import. That reasoning is worth keeping, so those passages are reworded to say
the file was retired here rather than left pointing a reader at a path that no longer
exists. A comment naming a deleted file is the same defect as a comment asserting a
guarantee the code does not provide, and this branch has corrected that nine times.

**Frontend**
- `app/(app)/projects/[id]/copilot/page.tsx`
- `components/copilot/{copilot,copilot-chat,gate-inline,pipeline-rail}.tsx`
- `app/api/copilot/ws-ticket/route.ts`
- `app/api/runs/[id]/copilot/{advance,cancel-turn,set-stage}/route.ts`
- the hook in `lib/copilot/use-copilot.ts`

### 4.2 Moved

`lib/copilot/{artifacts,stages,types}.ts` → `lib/orchestrator/`, and the two types out
of `use-copilot.ts` into `lib/orchestrator/types.ts`. Every import updated.

`stages.ts` exports `COPILOT_STAGES`, which the Orchestrator's panel uses for group
ordering. The constant is renamed `AGENT_STAGES` on the move — a name saying "Copilot"
inside the surviving surface is the kind of prose-versus-reality drift this branch has
corrected nine times.

### 4.3 What must not regress

The Deliverables panel is the piece with the most to lose: it renders `ArtifactKind`,
groups by `COPILOT_STAGES` order, and types its activity feed from `use-copilot`. Its
tests (`deliverables-panel.test.tsx`, `artifacts-panel-approver.test.tsx`,
`cockpit-deliverables.test.tsx`) are the regression net and must stay green throughout,
not merely at the end.

### 4.4 Debt folded in

**WS frame size (debt #2).** `_remember` bounds what is retained; a 20 MB frame still
arrives and is parsed. `uvicorn.run(...)` at `process_api.py:1285` gains `ws_max_size`,
which covers the `python process_api.py` path; the CLI path (`uv run uvicorn …`) needs
`--ws-max-size`, so `docs/local-setup.md` records it. Because neither covers every
deployment, `orchestrator2/ws.py` also refuses an oversized frame explicitly and closes
with a policy-violation code — that is the only layer we fully control.

**WS routes in the boot sweep (debt #4).** `assert_all_routes_protected` skips
everything that is not an `APIRoute`, so all ~18 WebSocket routes are invisible to it —
which is how `/projects/[id]/orchestrator` shipped ungated in Phase 1. The sweep is
extended to enumerate WebSocket routes and require each to carry an explicit decision.

**Scoped deliberately:** the existing ~18 sockets across ten agents are seeded into an
allowlist marked "authenticates in-handler", mirroring the signals in-body exception
that already exists. Auditing all eighteen is a separate piece of work across every
agent, and pretending otherwise would either block the boot or bless them silently. What
this buys is that a NEW WebSocket route cannot ship without a recorded decision. The
backlog is recorded as carried debt, not closed.

## 5. 5B — Server-backed sessions

### 5.1 The gap

The rail is `zustand` + `persist` into `localStorage`, and says so on screen: *"Sessions
are stored in this browser only."* Transcripts live in the browser. Clearing site data
loses every chat; another device never had them; nothing is auditable.

### 5.2 What already exists, and is simply not wired

`shared/services/conversation_service.py` is complete and every standalone agent uses
it. `orchestrator2` only READS from it (`router._history_messages`) and never writes.

| Need | Existing |
|---|---|
| create a session whose id IS the run id | `ensure_session_with_id(...)` — written for the Copilot, whose `session_id` was also the `run_id` |
| record a turn | `persist_turn(session_id, role, content, tenant_id=…, author_id=…)` — best-effort, never raises, never blocks chat |
| list a person's chats | `GET /conversations?agent_id=…&project_id=…` — creator-scoped, newest first |
| replay one | `GET /conversations/{id}/messages` |
| rename / delete | `PATCH` / `DELETE /conversations/{id}` |

So 5B is wiring, not construction.

### 5.3 Design

**Backend.** On the first turn of a run, `ws.py` calls `ensure_session_with_id(run_id,
…, agent_id="orchestrator", project_id=<from the verified run row>, created_by=user_id)`,
then persists each user turn and each agent reply with `persist_turn`. `agent_id` is the
literal `"orchestrator"` so the rail can list Orchestrator chats without them colliding
with the nine standalone agents' own session lists.

Persistence is best-effort by construction — `persist_turn` swallows its own errors —
which is the correct trade: losing a transcript row must never cost the user their turn.
This mirrors the deliverables decision exactly.

**Frontend.** The rail stops being the source of truth and becomes a view of the server:

- lists from `GET /api/conversations?agent_id=orchestrator&project_id=<id>`
- selecting one sets the cockpit's `runId` and replays
  `GET /api/conversations/{id}/messages` into the thread
- because the run already exists, the next turn rejoins the same LangGraph thread, the
  same Deliverables and the same project scope — continuing works with no new machinery
- rename and delete proxy to the existing `PATCH`/`DELETE`
- the "stored in this browser only" line goes, because it stops being true

**A new chat is unsaved until its first turn** (D23). The rail shows it as a local
draft; the run — and therefore the session — is minted by `ensureRun` on send, exactly
as today. Clicking "New" repeatedly cannot litter the database with empty runs.

**`localStorage` is not migrated.** Existing local sessions are per-browser and
per-device, hold no run id for turns never sent, and cannot be attributed to a user
server-side. They are dropped, and the rail says so once. Migrating a shape that can no
longer be produced is the mistake Phase 1's ruling R13 already recorded.

### 5.4 Access

Sessions are creator-scoped (`created_by == user_id`) by the existing service, which is
what the platform already does for every agent's chats. Combined with the socket's
Project-Admin check and per-project run scoping, a person sees their own Orchestrator
chats and no one else's. The new BFF proxies carry the same session boundary as their
neighbours and are covered by the boot sweep.

## 6. Testing

Per the branch standard: one agent implements, another reviews; every test proven by
breaking the implementation and watching it fail; a SURVIVED mutation is not a result
until the diff is shown — and, learned in Phase 4, "the file changed" is not "the line I
meant changed", so every mutation asserts anchor uniqueness and probes the function
under test.

5A carries a specific risk unit tests cannot cover: a deletion that removes something
still needed compiles fine until the page renders. So 5A ends with `npm run build`
(which resolves every import) and a live check of the Orchestrator, not only `vitest`.

## 7. Out of scope

- **BYOK env-fallback audit (debt #5)** — nine agents' executor boundaries; `testing_agent`
  is known bad. Its own piece of work.
- **The e2e rewrite (debt #8)** — needs a running stack and a login not available here.
- **Auditing the ~18 existing WebSocket routes** — the sweep will list them; deciding
  each is separate (§4.4).
- **RLS inert (debt #1)**, **context truncation (debt #6)**, **`"review this"` routing
  (debt #7)** — unchanged.
