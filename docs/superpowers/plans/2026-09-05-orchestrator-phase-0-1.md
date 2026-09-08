# Orchestrator Rebuild — Phase 0 + 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Orchestrator tab the single, project-scoped, Project-Admin-only
agent surface, with the Copilot's Artifacts/Activity/Context panel reused and every
trace of linear pipeline progression removed — against a pinned wire protocol the
new engine will implement in Phase 2.

**Architecture:** The Copilot's Zod-validated WebSocket protocol already exists and
the Artifacts panel already groups artifacts agent-wise. Phase 1 therefore *moves and
narrows* rather than rebuilds: the new Orchestrator protocol is the Copilot protocol
**minus gates, plus `agent.selected`**. The mock `script.ts` engine is deleted; the
Copilot page stays reachable but unlinked until Phase 3 lands the real engine, so
there is never a window with no working orchestration.

**Tech Stack:** Next.js 15 (App Router, Turbopack), React 19, TypeScript, Zod,
Zustand, TanStack Query, Vitest, Playwright, Tailwind.

**Spec:** `orchestrator_instruction.md` (repo root)

## Global Constraints

- **Naming:** the ninth agent is the **Project Manager agent** in all user-facing
  text. Never "Plan agent", never "PM agent". Its internal registry id stays `plan`.
- **Say "Business Unit", never "workspace", in prose.** Code identifiers keep
  `workspace`.
- **No positional progression.** No `next_stage()`, no auto-advance, no stage index
  arithmetic anywhere in Orchestrator code.
- **No gates or sign-off** in Orchestrator surfaces (spec D5).
- **Only `project_admin`** may use the Orchestrator (spec D8). `org_admin` and
  `bu_admin` are excluded entirely — they hold no agent access.
- **Nine agents:** `requirements`, `design`, `plan`, `development`, `code_review`,
  `security`, `testing`, `deployment`, `documentation`.
- Backend port is **8004** on this machine, not the 8001 in `docs/local-setup.md`.
- Lint runs with `--max-warnings=0`; unused imports fail the build.

---

## Sequencing correction (read before starting)

The spec's Phase 1 said "delete the Copilot page". **Do not delete it in Phase 1.**
The new engine does not exist until Phase 3, and the Copilot is currently the only
surface that actually runs agents. Deleting it first leaves the product with no
working orchestration for the length of the backend work — a regression the user
would hit immediately when demoing.

Phase 1 therefore:
- **deletes** the mock engine (`script.ts`, `use-orchestrator.ts`) — safe, it runs
  nothing;
- **unlinks** the Copilot (no navigation points at it) but leaves the route alive;
- **deletes** the Copilot route in Phase 5, once the new engine is proven.

---

## File Structure

**Created**
| File | Responsibility |
|---|---|
| `frontend/lib/orchestrator/protocol.ts` | The Orchestrator wire protocol: Zod discriminated union, server→client and client→server. Single source of truth for both stacks. |
| `frontend/lib/orchestrator/access.ts` | `canUseOrchestrator(role)` — the one place the Project-Admin rule is expressed. |
| `frontend/lib/orchestrator/__tests__/protocol.test.ts` | Protocol parse/reject tests. |
| `frontend/lib/orchestrator/__tests__/access.test.ts` | Access-rule tests, all 8 roles. |
| `frontend/app/api/__tests__/chat-agent-map.test.ts` | Pins the agent→WS mapping so retiring the legacy engine is visible, not silent. |

**Moved** (`git mv`, imports rewritten, no behaviour change)
| From | To |
|---|---|
| `components/copilot/artifacts-panel.tsx` | `components/orchestrator/artifacts-panel.tsx` |
| `components/copilot/artifact-viewer.tsx` | `components/orchestrator/artifact-viewer.tsx` |
| `components/copilot/code-tree-view.tsx` | `components/orchestrator/code-tree-view.tsx` |
| `components/copilot/choice-card.tsx` | `components/orchestrator/choice-card.tsx` |

**Modified**
| File | Change |
|---|---|
| `frontend/app/api/chat/route.ts` | Export `agentWsPath` for testing. No behaviour change. |
| `frontend/app/(app)/orchestrator/page.tsx` | Project-scoped, Project-Admin gated. |
| `frontend/components/orchestrator/cockpit.tsx` | Remove pipeline rail, Auto-advance, Run pipeline; mount the reused panel. |
| `frontend/lib/nav.ts` | Orchestrator entry gated to `project_admin`. |
| `frontend/components/app/phase-pipeline.tsx` (or wherever "Run agent" lives) | Points at Orchestrator; hidden for non-admins. |

**Deleted**
| File | Why |
|---|---|
| `frontend/lib/orchestrator/script.ts` | The mock engine. Runs nothing. |
| `frontend/lib/orchestrator/use-orchestrator.ts` | The `setTimeout` reveal loop. |
| `frontend/components/orchestrator/stage-rail.tsx` | Asserts linearity + gates. |

---

## Task 1: Pin the `/api/chat` agent→WS mapping

The BFF falls back to `/sdlc/agent/orchestrator/ws` — the legacy engine — for any
unrecognised agent id. Nothing records that today, so Phase 5's retirement of that
engine would silently break every per-agent chat that omits `agent`. This task makes
the mapping a tested fact first.

**Files:**
- Modify: `frontend/app/api/chat/route.ts:43-70`
- Create: `frontend/app/api/__tests__/chat-agent-map.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: `export function agentWsPath(agent?: string): string` from
  `app/api/chat/route.ts`.

- [ ] **Step 1: Write the failing test**

```ts
// frontend/app/api/__tests__/chat-agent-map.test.ts
import { describe, expect, it } from "vitest";

import { agentWsPath } from "@/app/api/chat/route";

/**
 * The BFF's agent → FastAPI WS mapping, pinned.
 *
 * The `default` case routes to the LEGACY orchestrator engine
 * (`/sdlc/agent/orchestrator/ws`), which Phase 5 retires. Without this test that
 * retirement silently breaks every caller that omits `agent`. If you are changing
 * the default, you are changing behaviour — update this test deliberately.
 */
describe("agentWsPath", () => {
  it.each([
    ["requirements", "/sdlc/agent/requirement/ws"],
    ["requirement", "/sdlc/agent/requirement/ws"],
    ["design", "/sdlc/agent/design/ws"],
    ["plan", "/sdlc/agent/plan/ws"],
    ["development", "/sdlc/agent/development/ws"],
    ["code_review", "/sdlc/agent/code-review/ws"],
    ["code-review", "/sdlc/agent/code-review/ws"],
    ["security", "/sdlc/agent/security/ws"],
    ["testing", "/sdlc/agent/testing/ws"],
    ["deployment", "/sdlc/agent/deployment/ws"],
    ["documentation", "/sdlc/agent/documentation/ws"],
  ])("maps %s to its own agent socket", (agent, path) => {
    expect(agentWsPath(agent)).toBe(path);
  });

  it("routes all nine agents to a dedicated socket, never the legacy engine", () => {
    const nine = [
      "requirements", "design", "plan", "development", "code_review",
      "security", "testing", "deployment", "documentation",
    ];
    for (const agent of nine) {
      expect(agentWsPath(agent)).not.toBe("/sdlc/agent/orchestrator/ws");
    }
  });

  it("falls back to the legacy orchestrator socket for unknown agents", () => {
    expect(agentWsPath(undefined)).toBe("/sdlc/agent/orchestrator/ws");
    expect(agentWsPath("nonsense")).toBe("/sdlc/agent/orchestrator/ws");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run app/api/__tests__/chat-agent-map.test.ts`
Expected: FAIL — `agentWsPath` is not exported (import resolves to undefined).

- [ ] **Step 3: Export the function**

In `frontend/app/api/chat/route.ts`, change the declaration only:

```ts
/** Map an agent id to its FastAPI WS path. Unknown/absent → orchestrator.
 *
 * Exported for `app/api/__tests__/chat-agent-map.test.ts`, which pins this table
 * so Phase 5's retirement of the legacy `/sdlc/agent/orchestrator/ws` engine
 * cannot silently change where an unmapped agent lands.
 */
export function agentWsPath(agent?: string): string {
```

Leave the body untouched.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run app/api/__tests__/chat-agent-map.test.ts`
Expected: PASS, 13 tests.

- [ ] **Step 5: Commit**

```bash
git add frontend/app/api/chat/route.ts frontend/app/api/__tests__/chat-agent-map.test.ts
git commit -m "test: pin the BFF agent-to-socket map before retiring the legacy engine"
```

---

## Task 2: The Orchestrator wire protocol

The Orchestrator's protocol is the Copilot's **minus gates, plus `agent.selected`**.
Defining it as its own module means Phase 2's engine has an exact target, and the
frontend cannot accidentally consume a gate event that the Orchestrator must never
emit.

**Files:**
- Create: `frontend/lib/orchestrator/protocol.ts`
- Create: `frontend/lib/orchestrator/__tests__/protocol.test.ts`

**Interfaces:**
- Consumes: `ARTIFACT_EVENTS` from `@/lib/copilot/artifacts`; `ChoiceCard` from
  `@/lib/copilot/types`.
- Produces:
  - `OrchestratorEvent` (Zod union) and its inferred type
  - `AgentSelectedEvent`
  - `OrchestratorOutbound` (client→server union)
  - `ORCHESTRATOR_AGENT_IDS: readonly string[]` — the nine

- [ ] **Step 1: Write the failing test**

```ts
// frontend/lib/orchestrator/__tests__/protocol.test.ts
import { describe, expect, it } from "vitest";

import {
  ORCHESTRATOR_AGENT_IDS,
  OrchestratorEvent,
} from "@/lib/orchestrator/protocol";

describe("OrchestratorEvent", () => {
  it("accepts a token chunk", () => {
    const parsed = OrchestratorEvent.safeParse({
      type: "stream_chunk",
      content: "hello",
    });
    expect(parsed.success).toBe(true);
  });

  it("accepts agent.selected and carries the reason", () => {
    const parsed = OrchestratorEvent.safeParse({
      type: "agent.selected",
      agent: "requirements",
      reason: "The user asked for a PRD.",
    });
    expect(parsed.success).toBe(true);
    if (parsed.success && parsed.data.type === "agent.selected") {
      expect(parsed.data.agent).toBe("requirements");
    }
  });

  it("rejects agent.selected naming an agent that does not exist", () => {
    const parsed = OrchestratorEvent.safeParse({
      type: "agent.selected",
      agent: "not_an_agent",
      reason: "x",
    });
    expect(parsed.success).toBe(false);
  });

  // The Orchestrator has no gates (spec D5). If the backend ever emits one it is a
  // bug, and the UI must drop it rather than render a gate the user cannot action.
  it("rejects gate events", () => {
    expect(
      OrchestratorEvent.safeParse({
        type: "gate.state",
        stage: "design",
        owner_role: "architect",
        can_approve: true,
      }).success,
    ).toBe(false);
  });

  it("covers all nine agents", () => {
    expect([...ORCHESTRATOR_AGENT_IDS].sort()).toEqual(
      [
        "code_review", "deployment", "design", "development", "documentation",
        "plan", "requirements", "security", "testing",
      ].sort(),
    );
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run lib/orchestrator/__tests__/protocol.test.ts`
Expected: FAIL — cannot resolve `@/lib/orchestrator/protocol`.

- [ ] **Step 3: Write the protocol**

```ts
// frontend/lib/orchestrator/protocol.ts
import { z } from "zod";

import { ARTIFACT_EVENTS } from "@/lib/copilot/artifacts";
import { ChoiceCard } from "@/lib/copilot/types";

/**
 * Orchestrator wire protocol — the contract between the Orchestrator UI and the
 * engine built in Phase 2.
 *
 * It is deliberately the Copilot protocol MINUS gates, PLUS `agent.selected`:
 *
 *  - Gates are gone because the Orchestrator is Project-Admin-only and there is
 *    nobody to sign off to (spec D5). A gate event here is a backend bug, so the
 *    union rejects it rather than rendering an unusable control.
 *  - `agent.selected` is new because routing is now a visible decision. The old
 *    engine advanced by list index and told the user nothing; when it picked
 *    wrong, there was no way to see that it had. Every routing decision is
 *    announced with its reason so the user can redirect in one turn.
 *  - There is no `stage.changed`: "stage" implied a position in a fixed sequence.
 *    `agent.selected` carries the same information without the false ordering.
 */

/** The nine agents. `plan` is the Project Manager agent — internal id only. */
export const ORCHESTRATOR_AGENT_IDS = [
  "requirements",
  "design",
  "plan",
  "development",
  "code_review",
  "security",
  "testing",
  "deployment",
  "documentation",
] as const;

export const OrchestratorAgentId = z.enum(ORCHESTRATOR_AGENT_IDS);
export type OrchestratorAgentId = z.infer<typeof OrchestratorAgentId>;

export const StreamChunkEvent = z.object({
  type: z.literal("stream_chunk"),
  content: z.string().default(""),
  session_id: z.string().optional(),
});

export const StreamEndEvent = z.object({
  type: z.literal("stream_end"),
  session_id: z.string().optional(),
});

/** The router chose an agent. Announced so a wrong choice is visible immediately. */
export const AgentSelectedEvent = z.object({
  type: z.literal("agent.selected"),
  agent: OrchestratorAgentId,
  reason: z.string().default(""),
  run_id: z.string().optional(),
});
export type AgentSelectedEvent = z.infer<typeof AgentSelectedEvent>;

export const ToolCallEvent = z.object({
  type: z.literal("tool.call"),
  run_id: z.string().optional(),
  name: z.string(),
  status: z.enum(["running", "done"]).default("running"),
});

export const ThinkingEvent = z.object({
  type: z.literal("agent.thinking"),
  run_id: z.string().optional(),
  delta: z.string().default(""),
});

export const ChoiceCardEvent = z.object({
  type: z.literal("choice.card"),
  run_id: z.string().optional(),
  card: ChoiceCard,
});

/**
 * A typed failure. The old engine logged failures and dropped them, which is how
 * the Project Manager agent stayed undispatchable without anyone noticing: the
 * turn simply produced nothing. Errors are surfaced here.
 */
export const ErrorEvent = z.object({
  type: z.literal("error"),
  message: z.string().optional(),
  detail: z.string().optional(),
  agent: OrchestratorAgentId.optional(),
});

export const OrchestratorEvent = z.discriminatedUnion("type", [
  StreamChunkEvent,
  StreamEndEvent,
  AgentSelectedEvent,
  ToolCallEvent,
  ThinkingEvent,
  ChoiceCardEvent,
  ErrorEvent,
  ...ARTIFACT_EVENTS,
]);
export type OrchestratorEvent = z.infer<typeof OrchestratorEvent>;

// ── client → server ────────────────────────────────────────────────────────

export interface OrchestratorUserMessage {
  type: "user_message";
  text: string;
  run_id: string;
  project_id: string;
}

export interface OrchestratorChoiceAnswer {
  type: "choice_answer";
  card_id: string;
  selected_ids: string[];
  free_text?: string;
  run_id: string;
}

export type OrchestratorOutbound =
  | OrchestratorUserMessage
  | OrchestratorChoiceAnswer;
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run lib/orchestrator/__tests__/protocol.test.ts`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/orchestrator/protocol.ts frontend/lib/orchestrator/__tests__/protocol.test.ts
git commit -m "feat(orchestrator): pin the wire protocol — no gates, routing announced"
```

---

## Task 3: The Project-Admin access rule

One function, one place. Both the nav entry and the page gate call it, so the rule
cannot drift between "the tab is visible" and "the page works".

**Files:**
- Create: `frontend/lib/orchestrator/access.ts`
- Create: `frontend/lib/orchestrator/__tests__/access.test.ts`

**Interfaces:**
- Consumes: `PlatformRole` from `@/lib/roles`.
- Produces: `export function canUseOrchestrator(role: PlatformRole | null | undefined): boolean`

- [ ] **Step 1: Write the failing test**

```ts
// frontend/lib/orchestrator/__tests__/access.test.ts
import { describe, expect, it } from "vitest";

import { canUseOrchestrator } from "@/lib/orchestrator/access";
import { ROLE_ORDER } from "@/lib/roles";

describe("canUseOrchestrator", () => {
  it("admits project_admin", () => {
    expect(canUseOrchestrator("project_admin")).toBe(true);
  });

  it("admits nobody else — including the governance tier", () => {
    for (const role of ROLE_ORDER) {
      if (role === "project_admin") continue;
      expect(canUseOrchestrator(role)).toBe(false);
    }
  });

  it("treats a missing role as no access", () => {
    expect(canUseOrchestrator(null)).toBe(false);
    expect(canUseOrchestrator(undefined)).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run lib/orchestrator/__tests__/access.test.ts`
Expected: FAIL — cannot resolve `@/lib/orchestrator/access`.

- [ ] **Step 3: Write the rule**

```ts
// frontend/lib/orchestrator/access.ts
import type { PlatformRole } from "@/lib/roles";

/**
 * Who may use the Orchestrator.
 *
 * Only the Project Admin, and for a specific reason: the Orchestrator reaches
 * every agent in the project, so anyone who could drive it would effectively hold
 * every agent's access. That is exactly the `use`-tier leak the one-agent-one-role
 * change removed. Project Admin is the only role that already owns all nine
 * (`AGENT_OWNERSHIP.project_admin` is ALL_OWNER), so granting it here adds nothing
 * it did not already have.
 *
 * `org_admin` and `bu_admin` are excluded despite outranking Project Admin
 * elsewhere: the governance tier holds no agent access at all. They decide who may
 * run agents; they do not run them.
 *
 * Per-project overrides (`agent_access_overrides`) deliberately do NOT open the
 * Orchestrator. They grant one extra agent to one person; the Orchestrator is all
 * nine at once, which is a different question with a different answer.
 */
export function canUseOrchestrator(
  role: PlatformRole | null | undefined,
): boolean {
  return role === "project_admin";
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run lib/orchestrator/__tests__/access.test.ts`
Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/orchestrator/access.ts frontend/lib/orchestrator/__tests__/access.test.ts
git commit -m "feat(orchestrator): one place for the Project-Admin access rule"
```

---

## Task 4: Move the Artifacts panel into the Orchestrator

Pure relocation. The panel already does what the spec asks — three tabs, artifacts
grouped agent-wise, a live code tree for Development. It moves so that deleting the
Copilot page in Phase 5 does not take it down.

**Files:**
- Move: `components/copilot/{artifacts-panel,artifact-viewer,code-tree-view,choice-card}.tsx`
  → `components/orchestrator/`
- Modify: the moved files' internal imports; `components/copilot/copilot.tsx` and
  `copilot-chat.tsx` to import from the new location.

**Interfaces:**
- Consumes: nothing new.
- Produces: `components/orchestrator/artifacts-panel.tsx` exporting the same symbols
  it exports today. No signature changes in this task.

- [ ] **Step 1: Record the baseline**

```bash
cd frontend && npm run typecheck && npx vitest run
```
Expected: PASS. Note the counts — Step 5 must match them.

- [ ] **Step 2: Move the files**

```bash
cd frontend
git mv components/copilot/artifacts-panel.tsx components/orchestrator/artifacts-panel.tsx
git mv components/copilot/artifact-viewer.tsx components/orchestrator/artifact-viewer.tsx
git mv components/copilot/code-tree-view.tsx components/orchestrator/code-tree-view.tsx
git mv components/copilot/choice-card.tsx components/orchestrator/choice-card.tsx
```

- [ ] **Step 3: Rewrite the imports**

Every reference to `@/components/copilot/<one of those four>` becomes
`@/components/orchestrator/<same>`. Find them all first — do not assume the list:

```bash
cd frontend
grep -rln "components/copilot/\(artifacts-panel\|artifact-viewer\|code-tree-view\|choice-card\)" \
  --include=*.ts --include=*.tsx . | grep -v node_modules
```

Then edit each hit. Leave `lib/copilot/*` imports alone — those modules are not
moving in this task.

- [ ] **Step 4: Verify nothing changed but the paths**

```bash
cd frontend && npm run typecheck && npm run lint && npx vitest run
```
Expected: PASS, same counts as Step 1. A behaviour change here is a mistake — this
task moves files and nothing else.

- [ ] **Step 5: Commit**

```bash
git add -A frontend/components
git commit -m "refactor(orchestrator): move the artifacts panel out of the copilot directory"
```

---

## Task 5: Strip linearity from the cockpit

Remove the PIPELINE panel, the Auto-advance switch and the Run-pipeline button, and
mount the moved panel in their place. These controls all assert a fixed order that
no longer exists.

**Files:**
- Modify: `frontend/components/orchestrator/cockpit.tsx`
- Delete: `frontend/components/orchestrator/stage-rail.tsx`
- Create: `frontend/components/orchestrator/__tests__/cockpit-no-linearity.test.tsx`

**Interfaces:**
- Consumes: `canUseOrchestrator` (Task 3); the moved `ArtifactsPanel` (Task 4).
- Produces: `OrchestratorCockpit` with `stage-rail` and auto-advance props removed.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/components/orchestrator/__tests__/cockpit-no-linearity.test.tsx
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * A source-level guard, not a render test.
 *
 * The linearity this removes is structural — a rail component, an auto-advance
 * flag, a "run the pipeline" action. A render test would only prove they are not
 * visible in one state; this proves they are gone. The Orchestrator picks any
 * agent at any time (spec D7), so any re-introduction of ordering controls should
 * fail loudly here and be a deliberate decision.
 */
const cockpit = readFileSync(
  join(process.cwd(), "components/orchestrator/cockpit.tsx"),
  "utf8",
);

describe("orchestrator cockpit", () => {
  it.each(["stage-rail", "StageRail", "autoAdvance", "Auto-advance", "Run pipeline"])(
    "no longer references %s",
    (banned) => {
      expect(cockpit).not.toContain(banned);
    },
  );

  it("mounts the artifacts panel", () => {
    expect(cockpit).toContain("components/orchestrator/artifacts-panel");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run components/orchestrator/__tests__/cockpit-no-linearity.test.tsx`
Expected: FAIL on `stage-rail`, `autoAdvance`, `Run pipeline` — all present today.

- [ ] **Step 3: Edit the cockpit**

In `components/orchestrator/cockpit.tsx`:
1. Delete the `StageRail` import and its JSX; render `<ArtifactsPanel .../>` in that
   slot, imported from `@/components/orchestrator/artifacts-panel`.
2. Delete the Auto-advance `Switch` and every `autoAdvance` state/prop/handler.
3. Delete the "Run pipeline" button and its handler. The Orchestrator starts work
   when the user says what they want, not from a button.
4. Delete the empty-state copy that describes hand-off order — replace with a line
   that describes the real behaviour, e.g. *"Ask for what you need. The right agent
   picks it up."*

Then remove the dead file:

```bash
cd frontend && git rm components/orchestrator/stage-rail.tsx
```

- [ ] **Step 4: Run test and the full suite**

```bash
cd frontend
npx vitest run components/orchestrator/__tests__/cockpit-no-linearity.test.tsx
npm run typecheck && npm run lint
```
Expected: test PASS; typecheck and lint clean. Lint fails on unused imports left
behind — that is the check doing its job, so remove them rather than disabling it.

- [ ] **Step 5: Commit**

```bash
git add -A frontend/components/orchestrator
git commit -m "feat(orchestrator): remove pipeline rail, auto-advance and run-pipeline"
```

---

## Task 6: Delete the mock engine

`script.ts` and `use-orchestrator.ts` fabricate agent output. Every line of the
Orchestrator's current "behaviour" is theatre, and leaving them risks the new UI
being wired back to them.

**Files:**
- Delete: `frontend/lib/orchestrator/script.ts`, `frontend/lib/orchestrator/use-orchestrator.ts`
- Modify: whatever imported them.
- Create: `frontend/lib/orchestrator/__tests__/no-mock-engine.test.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing. This task only removes.

- [ ] **Step 1: Write the failing test**

```ts
// frontend/lib/orchestrator/__tests__/no-mock-engine.test.ts
import { describe, expect, it } from "vitest";
import { existsSync } from "node:fs";
import { join } from "node:path";

/**
 * The Orchestrator used to fake its agents: `script.ts` held hardcoded per-agent
 * prose and `use-orchestrator.ts` revealed it on a timer, so the page looked like
 * it was streaming from a backend that did not exist. Both are gone. This test
 * exists so they cannot quietly come back as a "temporary" stand-in while the real
 * engine is being built.
 */
describe("orchestrator mock engine", () => {
  it.each(["script.ts", "use-orchestrator.ts"])("%s is gone", (file) => {
    expect(existsSync(join(process.cwd(), "lib/orchestrator", file))).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run lib/orchestrator/__tests__/no-mock-engine.test.ts`
Expected: FAIL — both files exist.

- [ ] **Step 3: Delete them and fix the fallout**

```bash
cd frontend
git rm lib/orchestrator/script.ts lib/orchestrator/use-orchestrator.ts
grep -rln "lib/orchestrator/\(script\|use-orchestrator\)" --include=*.ts --include=*.tsx . \
  | grep -v node_modules
```

Remove each importer's usage. The cockpit currently drives the fake run through
`useOrchestrator`; with no engine yet, render the thread from session state only and
leave the composer disabled with an explicit note — *"The Orchestrator engine
arrives in the next phase."* Do **not** substitute another fake.

- [ ] **Step 4: Verify**

```bash
cd frontend
npx vitest run lib/orchestrator/__tests__/no-mock-engine.test.ts
npm run typecheck && npm run lint && npx vitest run
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add -A frontend
git commit -m "refactor(orchestrator): delete the scripted mock engine"
```

---

## Task 7: Gate the route and the nav on Project Admin

**Files:**
- Modify: `frontend/lib/nav.ts:146-176`
- Modify: `frontend/app/(app)/orchestrator/page.tsx`
- Create: `frontend/lib/__tests__/nav-orchestrator.test.ts`

**Interfaces:**
- Consumes: `canUseOrchestrator` (Task 3); `useSession` from `@/hooks/use-session`.
- Produces: nav entry gated; page renders a no-access state for everyone else.

- [ ] **Step 1: Write the failing test**

```ts
// frontend/lib/__tests__/nav-orchestrator.test.ts
import { describe, expect, it } from "vitest";

import { NAV_ITEMS } from "@/lib/nav";

/**
 * The Orchestrator entry used to be gated on `artifact:view` — which every
 * delivery role holds — because the page was a mock and opening it did nothing.
 * It now reaches all nine agents, so visibility and usability must agree: a tab
 * that opens onto "no access" is a worse experience than no tab.
 */
describe("orchestrator nav entry", () => {
  const entry = NAV_ITEMS.find((i) => i.segment === "orchestrator");

  it("exists", () => {
    expect(entry).toBeDefined();
  });

  it("is restricted to project_admin, not to a permission every role holds", () => {
    expect(entry?.requirePermission).toBeUndefined();
    expect(entry?.requireRole).toEqual(["project_admin"]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run lib/__tests__/nav-orchestrator.test.ts`
Expected: FAIL — the entry still carries `requirePermission: "artifact:view"`.

- [ ] **Step 3: Implement**

If `NavItem` has no `requireRole` field, add it (optional `PlatformRole[]`) and
honour it wherever `requirePermission`/`hideForRoles` are evaluated — find that with
`grep -rn "hideForRoles" frontend/`. Then change the entry:

```ts
    label: "Orchestrator",
    href: "/orchestrator",
    icon: Workflow,
    segment: "orchestrator",
    // Reaches all nine agents, so it is Project-Admin-only (see
    // lib/orchestrator/access.ts for why). It used to be gated on `artifact:view`,
    // which every delivery role holds — correct when the page was a mock, wrong
    // now that it runs real agents.
    requireRole: ["project_admin"],
    prdSection: "§34.11",
```

Delete the now-redundant `hideForRoles` on this entry: `requireRole` already
excludes the governance tier.

In `app/(app)/orchestrator/page.tsx`, gate the render with `canUseOrchestrator`,
showing the existing no-access empty state otherwise.

- [ ] **Step 4: Verify**

```bash
cd frontend && npx vitest run lib/__tests__/nav-orchestrator.test.ts && npm run typecheck
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A frontend/lib frontend/app
git commit -m "feat(orchestrator): restrict the tab and route to Project Admin"
```

---

## Task 8: Point "Run agent" at the Orchestrator, hidden for non-admins

**Files:**
- Modify: wherever the button lives — find with
  `grep -rn "Run agent" frontend --include=*.tsx | grep -v node_modules`
- Create: a test beside that component, `__tests__/run-agent-button.test.tsx`

**Interfaces:**
- Consumes: `canUseOrchestrator` (Task 3).
- Produces: no new exports.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/components/app/__tests__/run-agent-button.test.tsx
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { RunAgentButton } from "@/components/app/run-agent-button";

/**
 * "Run agent" opens the Orchestrator, which only a Project Admin may use. Showing
 * it to a BA would put a dead end on the project page; the delivery roles start
 * their work from the agent tiles on Overview, which already enforce owner-only
 * access.
 */
describe("RunAgentButton", () => {
  it("links a project admin to that project's Orchestrator", () => {
    render(<RunAgentButton projectId="p1" role="project_admin" />);
    expect(screen.getByRole("link", { name: /run agent/i })).toHaveAttribute(
      "href",
      "/orchestrator?project=p1",
    );
  });

  it.each(["ba", "developer", "qa", "architect", "org_admin"] as const)(
    "renders nothing for %s",
    (role) => {
      const { container } = render(<RunAgentButton projectId="p1" role={role} />);
      expect(container).toBeEmptyDOMElement();
    },
  );
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run components/app/__tests__/run-agent-button.test.tsx`
Expected: FAIL — no `run-agent-button` module.

- [ ] **Step 3: Implement**

Extract the existing button into `components/app/run-agent-button.tsx`:

```tsx
"use client";

import Link from "next/link";
import { Play } from "lucide-react";

import { Button } from "@/components/ui/button";
import { canUseOrchestrator } from "@/lib/orchestrator/access";
import type { PlatformRole } from "@/lib/roles";

export function RunAgentButton({
  projectId,
  role,
}: {
  projectId: string;
  role: PlatformRole | null | undefined;
}) {
  // Hidden rather than disabled: a disabled control invites a request for access
  // that this button is not the right place to make. Delivery roles start from the
  // agent tiles on Overview, which carry their own access affordance.
  if (!canUseOrchestrator(role)) return null;

  return (
    <Button asChild size="sm">
      <Link href={`/orchestrator?project=${encodeURIComponent(projectId)}`}>
        <Play className="size-3.5" aria-hidden />
        Run agent
      </Link>
    </Button>
  );
}
```

Replace the old inline button with `<RunAgentButton …/>`, and remove any navigation
that still points at `/projects/[id]/copilot`. The Copilot route itself stays alive
and reachable by URL until Phase 5 — only the links go.

- [ ] **Step 4: Verify**

```bash
cd frontend
npx vitest run components/app/__tests__/run-agent-button.test.tsx
grep -rn "copilot" app components --include=*.tsx | grep -v node_modules | grep -v "components/copilot"
```
Expected: test PASS; the grep shows no remaining navigation into the Copilot.

- [ ] **Step 5: Commit**

```bash
git add -A frontend
git commit -m "feat(orchestrator): Run agent opens the Orchestrator, Project Admin only"
```

---

## Task 9: End-to-end check

**Files:**
- Modify: `frontend/e2e/orchestrator.spec.ts`

- [ ] **Step 1: Update the existing spec**

It currently drives the mock ("Run pipeline", stage rail). Rewrite it to assert what
now exists: a Project Admin sees the Orchestrator tab, opens it for a project, sees
the Artifacts / Activity / Context panel, and sees **no** pipeline rail, no
Auto-advance and no Run-pipeline control.

- [ ] **Step 2: Run it**

```bash
cd frontend && npx playwright test e2e/orchestrator.spec.ts
```
Expected: PASS. Requires the stack up — backend on **8004**, frontend on 3000.

- [ ] **Step 3: Full gate**

```bash
cd frontend && npm run typecheck && npm run lint && npx vitest run
```
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add -A frontend/e2e
git commit -m "test(orchestrator): e2e covers the merged surface"
```

---

## Phase 1 exit criteria

- [ ] Orchestrator tab visible only to Project Admin; page gated server-consistently.
- [ ] "Run agent" opens the Orchestrator; nothing links to the Copilot.
- [ ] Artifacts / Activity / Context panel renders, grouped agent-wise.
- [ ] No pipeline rail, no gates, no auto-advance, no Run-pipeline anywhere.
- [ ] Mock engine deleted; composer honestly says the engine is coming.
- [ ] Wire protocol pinned and tested — Phase 2's target.
- [ ] `npm run typecheck && npm run lint && npx vitest run` all clean.

**Not in Phase 1** (deliberate): server-backed sessions, the capability registry,
the Context Agent, real agent dispatch, deleting the Copilot route. Phases 2–5.

---

## Self-review

**Spec coverage.** §1.1 merge → Tasks 4–6; §1.1 no rail/gates → Task 5; §1.2
navigation → Tasks 7–8; §1.4 artifacts agent-wise → Task 4 (panel already does it);
§1.5 Project-Admin-only → Tasks 3, 7, 8; §5.5 event contract → Task 2; §6 risk
(`/api/chat` fallback) → Task 1. §1.3 Context Agent, §5.2 registry, §5.3 sessions
are Phase 2–3 by design and are listed under "Not in Phase 1".

**Gap accepted, with reason.** §1.5 says every run belongs to the project, but
sessions stay `localStorage` through Phase 1 because moving them needs a backend
endpoint that does not exist until Phase 2. Phase 1 scopes the rail to the selected
project so the UI is already correct; only persistence lags.

**Type consistency.** `canUseOrchestrator(role)` has one signature across Tasks 3, 7
and 8. `OrchestratorAgentId` is defined once in Task 2 and reused. The moved panel
keeps its exports unchanged in Task 4 — no renames.

**Known unknown.** Task 7 assumes `NavItem` may lack `requireRole` and instructs the
implementer to add and honour it; Task 8 assumes "Run agent" is inline and needs
extracting. Both are stated as find-then-act rather than asserted as fact.
