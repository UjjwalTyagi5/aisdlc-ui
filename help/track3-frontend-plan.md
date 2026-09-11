# Track 3 frontend — what exists, what's wrong in the Phase 1 doc, what to build

Companion to `help/track3-phase1-requirements-discovery.md`. That doc was written
from the backend side only. This one is the frontend research it was missing, and
it **corrects a real naming mistake** in that doc's §1 — read §1 below before
building anything.

## 0. Direct answer: does a capability-based standalone page exist for either agent?

**No, for both — stated plainly so §1 below isn't misread as "the frontend is
done."** What §1 documents is *metadata and routing* (enums, per-track rosters,
gate copy, a stub route) — not a UI built around either agent's actual work
product. Concretely, as the code stands today:

| | Discovery & Assessment | Requirements (migration-intent) |
|---|---|---|
| **Page exists?** | Yes — `app/(app)/projects/[id]/discovery/page.tsx` | Yes — `app/(app)/projects/[id]/requirements/page.tsx` |
| **What it's built on** | The **generic** `StageWorkbench` shell — the same list/detail/approve/chat shell used for Code Review, Security, Documentation. 21 lines: a title, a description string, nothing else. Zero knowledge of dependency graphs, risk tiers, or EOL/CVE flags — those don't exist as concepts anywhere in this page. | **Track 1's Requirements capabilities only** — `BoardProjectDialog` (push stories to Jira/ADO), `TraceabilityPanel`, an artifact list built for INVEST stories/BRD/risk register. Checked directly: **zero references to `project.track` or `DeliveryTrack` anywhere in the file.** It has no idea a project can be on Track 3. |
| **What a Track 3 project would see today** | The generic shell, with its chat broken (next row) — no assessment-specific rendering at all. | The exact same story-pulling UI a Track 1 project gets — wrong for migration-intent output, which isn't stories. |
| **Chat works?** | **No.** `agentWsPath("discovery")` in `app/api/chat/route.ts` has no case for it — the `default` branch returns `null`, and the BFF refuses with `400 {code:"unknown_agent", detail:"no agent named discovery"}` the moment "Run Discovery & Assessment agent" is clicked. | Yes, but talks to Track 1's `requirement` WS endpoint (`agentWsPath` maps `"requirements"` → `/sdlc/agent/requirement/ws`) regardless of the project's actual track. |

So: **both need real frontend work.** Discovery needs a page built from nothing
(§4 below sketches what its secondary panel should show, once the backend agent's
output schema exists). Requirements needs, at minimum, a track-conditional
rendering path added to the existing page — not a rebuild, since the chat
plumbing and artifact-list mechanics already work, just not for migration-intent's
different output shape.

## 1. The frontend is already far ahead of the backend for Track 3 — on metadata and routing, not on either agent's actual UI

Before writing any backend code, a look at `frontend/lib/` shows the frontend side
of this platform was already built out for all five tracks, not just Track 1 —
someone did this PRD-to-code pass already:

- **`lib/schemas/enums.ts`** — `AgentType` and `Phase` are 13-value enums, not 9.
  The five track-specific ones are already there, named and documented:
  `discovery`, `strategy`, `migration_mapping`, `validation`, `data_engineering`.
  `DeliveryTrack` is `["greenfield", "enhancement", "modernization", "rpa_infra",
  "data_engineering"]` — the exact same five keys, in the same order, as backend's
  `TRACK_PORTFOLIOS` in `config/agent_registry.py`. That's not a coincidence; both
  sides were built from the same PRD.
- **`lib/tracks.ts`** — `TRACK_AGENTS`, a per-track roster in hand-off order,
  already fully specified for `modernization`:
  ```
  requirements → discovery → design → strategy → development → review →
  security → testing → deployment → documentation
  ```
  This is `TRACK_PORTFOLIOS["modernization"]`'s frontend twin, and it's already
  populated — backend's is still `[]`.
- **`lib/agents.ts`** — `PHASE_LABEL`, `PHASE_DESCRIPTION`, and `GATE_POLICY` are
  all filled in for `discovery` and `strategy` already (owner, gate type, sign-off
  copy — e.g. discovery's gate: *"The Architect accepts the as-is assessment as the
  planning baseline for every later agent"*). `BUILT_AGENTS`, the allowlist that
  decides whether a phase's tile is clickable or shows "Coming soon", currently
  holds only the nine Track 1 agents — `discovery` is **not** in it, so its tile
  renders as locked/coming-soon today even though its page exists (see next point).
- **Stub pages already exist**: `app/(app)/projects/[id]/discovery/page.tsx` and
  `.../strategy/page.tsx` are real, committed files (21 lines each) using a shared
  generic shell, `components/app/stage-workbench.tsx` (`StageWorkbench`) —
  artifact list + generic detail pane + approval gate + chat drawer, the same shell
  used for Code Review/Security/Documentation, agents whose UI needs nothing more
  bespoke than "list, read, approve, chat."

**None of this is wired to a working backend agent yet** — see §3.

## 2. Correction to `track3-phase1-requirements-discovery.md` §1: the agent-id naming decision

That doc proposed new ids `discovery_assessment` and `requirements_modernization`,
reasoning from the backend's flat `AGENT_REGISTRY`/`REGISTRY` alone, before this
frontend research existed. **Both proposals are wrong given what's already shipped
on the frontend:**

- **Discovery & Assessment's id must be `discovery`, not `discovery_assessment`.**
  The frontend's `AgentType`/`Phase` enum, `GATE_POLICY`, `PHASE_LABEL`, the stub
  page's `agent="discovery"` prop, and `TRACK_AGENTS["modernization"]` are all
  already written against `"discovery"`. Using a different backend id means either
  renaming all of that (touching code someone already wrote and presumably
  reviewed) or maintaining a translation layer nobody asked for. Follow the
  existing convention instead: `AGENT_REGISTRY["discovery"]` in
  `config/agent_registry.py`, `REGISTRY["discovery"]` in
  `agents_orchestrator/orchestrator2/registry.py`, `TRACK_PORTFOLIOS["modernization"]`
  gets `"discovery"` added.

- **Requirements for Track 3 is a genuinely open question, not a settled one —
  and the frontend's existing choice points the other way from what the Phase 1
  doc assumed.** `lib/tracks.ts`'s `TRACK_AGENTS["modernization"]` lists
  `"requirements"` — the *same* id Track 1 uses, on the *same* page
  (`app/(app)/projects/[id]/requirements/page.tsx`), not a second one. The
  frontend's own module docstring says why: *"a track is a configurable delivery
  template... over one shared control plane"* — the design intent is one agent
  whose behavior is inflected by context (track, entry point), not a forked agent
  per track. Track 2 (Enhancement) already claims this in the PRD text — Requirements
  "runs in triage / impact-analysis mode" — **though a check of the actual backend
  code (`agents_orchestrator/requirements_agent/agents/planning.py`,
  `requirements_agent_api.py`) found zero implemented track-conditional behavior
  today: `SYS_MESSAGE` is one module-level constant, always
  `INGESTION_SYS_MESSAGE`, for every project regardless of track.** So this is an
  aspiration in the PRD/frontend, not a working precedent to copy.

  **Two real options, and this needs a decision before Requirements work starts:**

  1. **One id, track-conditional prompt** (matches the frontend's existing
     assumption). `AgentCapability.load_prompt` in
     `agents_orchestrator/orchestrator2/registry.py` would need to become
     track-aware — today it's `Callable[[], str]`, called with no arguments; it
     would need to become `Callable[[str | None], str]` (or read track from a
     contextvar already set by `dispatch.run_agent`, avoiding a signature change
     to the fourteen other `load_prompt`/`load_graph` callables). The standalone
     `requirements_agent_api.py` WS handler would need the same: resolve the
     project's track before picking `INGESTION_SYS_MESSAGE` vs. a new
     `MIGRATION_INTENT_SYS_MESSAGE`. One page, one id, one route
     (`/projects/[id]/requirements`) continues to work for every track — no
     frontend change needed for routing, only for the page's own rendering (see
     §4).
  2. **A new id** (`track3-phase1-requirements-discovery.md`'s original proposal),
     which requires a NEW frontend page/route too — `TRACK_AGENTS["modernization"]`
     would need to name the new id instead of `"requirements"`, a real frontend
     change to a file someone already wrote, and a second route (e.g.
     `/projects/[id]/requirements-modernization`) that doesn't exist yet.

  **Recommendation: option 1**, specifically because it requires zero changes to
  already-shipped frontend code (`lib/tracks.ts`, `lib/agents.ts`, the
  `/requirements` route) and keeps the platform's "one control plane, track
  inflects behavior" story consistent for the one agent (Requirements) that
  genuinely spans every track. This is a call worth confirming with whoever owns
  the PRD/frontend architecture before backend work starts, since it's a real
  interface change (`load_prompt`'s signature) rather than a purely additive one
  like Discovery & Assessment's brand-new id.

## 3. The chat wiring gap: `agentWsPath` doesn't know about any Track 3 agent yet

`frontend/app/api/chat/route.ts`'s `agentWsPath(agent)` is the map from a page's
`agent` prop to the backend's standalone WebSocket path — e.g. `"requirements"` →
`/sdlc/agent/requirement/ws`. **It has no case for `"discovery"`, `"strategy"`, or
any other Track 3+ id** — the `default` branch returns `null`, and the comment
there is explicit that this is deliberate ("NO FALLBACK... every caller passes an
explicit agent, so reaching here is a bug, and it is reported as one"). Concretely:
**the `/discovery` stub page that already exists has a broken chat today** — its
`useAgentChat({ agent: "discovery" })` call would hit this `null` case and refuse,
by design, rather than silently talking to the wrong agent.

**Required addition**, once the standalone Discovery router is mounted (per
`track3-phase1-requirements-discovery.md` §3's standalone-wiring steps, using id
`discovery` per §2 above):

```ts
case "discovery":
  return "/sdlc/agent/discovery/ws";
```

`app/api/__tests__/chat-agent-map.test.ts` pins this table — add discovery's case
there too, in the same change, or the existing test won't catch a future regression
on this new entry (it currently only proves the NINE Track 1 entries are pinned).

## 4. New pages have to be built for these two agents — the generic stub is not the answer

Every Track 1 agent that has meaningfully different work products has its own
bespoke page, not the generic `StageWorkbench` shell. Two examples worth reading in
full before building Discovery & Assessment's page:

- **Requirements** (`app/(app)/projects/[id]/requirements/page.tsx`, ~520 lines):
  primary chat is the same `AgentChatDrawer`/`useAgentChat` every page uses; the
  bespoke part is `BoardProjectDialog` (which ADO/Jira project this run pulls
  from/writes to), `TraceabilityPanel` (requirement → story → downstream artifact
  links), and an artifact list scoped to `phase: "requirements"` (stories, BRD,
  risk register).
- **Development** (`app/(app)/projects/[id]/development/page.tsx`, ~520 lines):
  same chat pattern; the bespoke part is `RepoPickerDialog`, `RepoFileTree` +
  `CodeViewer` (a real file browser over the agent's sandboxed clone), and a
  files/PRs tab split (`listDevPrs`, `getWorkspaceChanges`, `getFileChangedLines`)
  — because "what did the agent build" is best shown as a code diff, not a
  document list.

**The pattern, stated generally**: primary chat is constant across every agent
(`AgentChatDrawer` + `useAgentChat`, right-side or drawer); the *secondary* surface
is custom per agent, built around what that agent's actual output looks like and
what a person needs to review it. `StageWorkbench`'s generic
list-detail-approve-chat shell is explicitly for agents that need nothing more than
that (Code Review, Security, Documentation, and the two Track 3 stubs *today*,
because nothing has told them otherwise yet).

**Discovery & Assessment's output is not a document or a code diff — it's a
dependency graph, a per-module risk score/tier, and EOL/CVE flags** (per
`help/track3-agent-build-plan.md`'s description of what this agent actually
produces). None of that renders usefully in `StageWorkbench`'s generic
`ArtifactBody` (raw markdown, or a JSON dump as a last resort — see
`stage-workbench.tsx`'s own fallback). **This needs a real, bespoke page**, closer
in shape to Development's than to Code Review's:

**Proposed structure for `app/(app)/projects/[id]/discovery/page.tsx` (replacing
the current stub):**

- Primary chat: unchanged — `AgentChatDrawer` + `useAgentChat({ agent: "discovery",
  ... })`, same as every other page.
- Secondary panel, left or as tabs (mirroring Development's `files`/`prs` tab
  split):
  - **Module list**, sortable/filterable by risk tier (mechanical /
    LLM-assisted / manual-only — the three-tier classification from
    `track3-agent-build-plan.md` §"On feasibility of the .NET → newer language
    case"), each row showing the module path, its tier, and a risk score.
  - **Dependency graph view** — even a simple tree/list grouped by
    direct-vs-transitive is more useful here than raw JSON; a real graph
    visualization (e.g. reusing whatever renders C4 diagrams for the Design page,
    if that component is reusable) is a stretch goal, not a Phase 1 requirement.
  - **Flags panel** — EOL/CVE dependencies, called out distinctly (this is
    functionally an SBOM-adjacent view; check whether `components/app/` already
    has something from the Security page's SBOM rendering that can be reused
    rather than building a new component from scratch).
- The approval gate stays exactly as `GATE_POLICY.discovery` already
  describes it (already written — see §1) — *"Accept the assessment as planning
  baseline"* — no new gate-copy work needed, only the rendering of what's being
  accepted.

**Requirements (migration-intent), if option 1 from §2 is chosen**: the existing
`/requirements` page likely needs only an *addition*, not a rebuild — a
migration-intent run's `requirements_payload` (scope/constraints/success-criteria,
not INVEST stories) needs a distinct rendering path in whatever renders the
artifact list/detail today, conditioned on `project.track === "modernization"`.
Confirm this once the backend's migration-intent output schema is nailed down
(`track3-phase1-requirements-discovery.md` §4) — a small "Migration Brief" summary
card alongside the existing artifact list is the likely shape, not a parallel
page.

## 5. Checklist for whoever picks this up

1. Confirm the Requirements naming decision (§2) — this gates whether any new
   frontend route is needed at all for Requirements, or just a rendering addition.
2. Build Discovery & Assessment's standalone backend agent under id `discovery`
   (not `discovery_assessment` — correcting `track3-phase1-requirements-discovery.md`
   §3 and §5 wherever they say otherwise).
3. Add the `"discovery"` case to `agentWsPath` (§3) and its pinned test.
4. Replace the `/discovery` stub page with a bespoke one (§4) once there's a real
   backend artifact shape to build the secondary panel against — building the UI
   before the backend's `discovery_artifacts` schema exists means guessing at a
   shape that will change.
5. Add `"discovery"` to `BUILT_AGENTS` once the page and backend are both real —
   not before, or the tile goes clickable while pointing at nothing (`404` on the
   WS, per §3's "no fallback" behavior).
6. Update `frontend/lib/roles.ts`'s ownership table (the frontend mirror of
   backend's `AGENT_DEFAULT_REACH`, per `config/agent_registry.py`'s own comment
   that the two must match — `backend/tests/test_agent_reach_matches_frontend.py`
   pins this) — confirm it already has `discovery`/`strategy` entries (likely yes,
   given how far ahead `lib/agents.ts` already is) or add them.
