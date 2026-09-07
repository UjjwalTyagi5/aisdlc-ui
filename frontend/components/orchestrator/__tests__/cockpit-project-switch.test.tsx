// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/**
 * Switching project must not leave the next turn running against the old one.
 *
 * The global cockpit (`/orchestrator`, no `lockedProjectId`) has a project picker.
 * The reset that clears `runIdRef` and the transcript is keyed on
 * `conversationKey`, and that key was `active?.id ?? projectId` — a SESSION id when
 * one is active. `handleProjectChange` repoints the active session in place via
 * `retargetSession`, which deliberately keeps the session id, so the key did not
 * change, so the reset never fired and the next turn reused the previous project's
 * `run_id`.
 *
 * What that costs: `ws._resolve_run` reads `project_id` from the RUN row, never from
 * the frame, so the turn enforces the OLD project's offering grant, spends the OLD
 * project's monthly budget with its BYOK key, and joins the old run's LangGraph
 * thread — while the header, the model picker and the artifacts panel all name the
 * new project. No privilege is crossed (the caller administers both), but the UI and
 * the run disagree about which project is being worked on, which is exactly what the
 * backend's per-turn project check exists to make impossible.
 *
 * Found by the whole-branch review, measured on the real component rather than
 * inferred.
 */

const sendTurn = vi.fn();
const resetSocket = vi.fn();
let socketBusy = false;
let socketConnState = "connected";
let activityFromSocket: Array<{
  id: string;
  ts: string;
  kind: "tool" | "thinking" | "stage" | "turn";
  label: string;
  status?: "running" | "done";
}> = [];

const deliverablesFromSocket: Array<Record<string, unknown>> = [];

vi.mock("@/lib/orchestrator/use-orchestrator-socket", () => ({
  useOrchestratorSocket: () => ({
    messages: [],
    send: sendTurn,
    connState: socketConnState,
    activeAgent: null,
    error: null,
    busy: socketBusy,
    activity: activityFromSocket,
    deliverables: deliverablesFromSocket,
    reset: resetSocket,
  }),
}));

vi.mock("@/hooks/use-session", () => ({
  useSession: () => ({
    user: { id: "u1", name: "Ana", email: "ana@example.com", initials: "A" },
    tenant: { id: "t1", name: "ABC Bank", plan: "pro" },
    permissions: ["artifact:view"],
  }),
}));

vi.mock("@/hooks/use-access-scope", () => ({
  useAccessScope: () => ({ role: "project_admin", isLoading: false }),
}));

const PROJECTS = [
  { id: "p1", name: "Alpha", track: "greenfield", workspaceId: "w1" },
  { id: "p2", name: "Beta", track: "greenfield", workspaceId: "w1" },
];

vi.mock("@/lib/api/projects", () => ({
  getProject: vi.fn(async (id: string) => PROJECTS.find((p) => p.id === id) ?? PROJECTS[0]),
  listProjects: vi.fn(async () => ({
    items: PROJECTS,
    total: PROJECTS.length,
    page: 1,
    pageSize: 100,
  })),
}));

vi.mock("@/lib/api/models", () => ({
  // One RUNNABLE offering. An empty `/model/options` now closes the composer (the
  // project has no keyed provider connection behind any granted model), which is
  // not the state these tests are about.
  getModelOptions: vi.fn(async () => ({
    options: [
      {
        offering_id: "off-1",
        provider_id: "prov-1",
        display_name: "Anthropic",
        provider: "anthropic",
        model_id: "claude-opus-4-5",
        is_default: true,
      },
    ],
    default_offering_id: "off-1",
    default_model_id: null,
  })),
}));

interface CreateRunBody {
  project_id: string;
}
let runSeq = 0;
const createRun = vi.fn(async (body: CreateRunBody) => ({
  runId: `run-${body.project_id}-${++runSeq}`,
}));
vi.mock("@/lib/api/runs", () => ({ createRun: (b: CreateRunBody) => createRun(b) }));

vi.mock("@/components/orchestrator/model-picker", () => ({
  ModelPicker: () => <div data-testid="model-picker" />,
}));

// The picker itself is not under test; a plain button per project makes the change
// deterministic without driving Radix in jsdom.
vi.mock("@/components/orchestrator/project-picker", () => ({
  ProjectPicker: ({ onValueChange }: { onValueChange: (id: string) => void }) => (
    <div>
      {PROJECTS.map((p) => (
        <button key={p.id} onClick={() => onValueChange(p.id)}>
          pick-{p.id}
        </button>
      ))}
    </div>
  ),
}));

import { OrchestratorCockpit } from "@/components/orchestrator/cockpit";
import { useOrchestratorStore } from "@/stores/orchestrator-store";

function renderGlobalCockpit() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <OrchestratorCockpit variant="page" />
    </QueryClientProvider>,
  );
}

function sendMessage(text: string) {
  const composer = screen.getByRole("textbox", { name: /message the orchestrator/i });
  fireEvent.change(composer, { target: { value: text } });
  fireEvent.keyDown(composer, { key: "Enter" });
}

/** Resolve the run for the Nth turn, the way the hook does. */
async function runIdOfTurn(n: number): Promise<string> {
  const turn = sendTurn.mock.calls[n]![0] as { resolveRunId: () => Promise<string> };
  let id = "";
  await act(async () => {
    id = await turn.resolveRunId();
  });
  return id;
}

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
  // jsdom implements neither; the Activity tab scrolls itself to the bottom on every
  // render, and the thread scrolls into view.
  Element.prototype.scrollTo = vi.fn();
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = vi.fn();
  Element.prototype.releasePointerCapture = vi.fn();
  sendTurn.mockClear();
  resetSocket.mockClear();
  createRun.mockClear();
  runSeq = 0;
  activityFromSocket = [];
  socketBusy = false;
  socketConnState = "connected";
  // A real session on Alpha, exactly as the rail would hold one.
  useOrchestratorStore.setState({ sessions: [], activeSessionId: null });
  act(() => {
    useOrchestratorStore.getState().createSession({
      projectId: "p1",
      projectName: "Alpha",
      track: "greenfield",
      modelKey: null,
    });
  });
});

afterEach(cleanup);

describe("OrchestratorCockpit — the Activity tab is actually fed", () => {
  it("hands the hook's activity to the panel, not an empty array", async () => {
    // The defect `960e90fc` is named after lived at THIS boundary — the cockpit
    // passed no `activity` prop at all, so the panel defaulted to `[]`. That
    // commit's own mutations were all inside the hook, so restoring the exact
    // defect (`activity={[]}`) left all 638 tests green. Found by the whole-branch
    // review; this is the test that closes it.
    activityFromSocket = [
      { id: "a1", ts: new Date().toISOString(), kind: "tool", label: "read_repo", status: "running" },
    ];
    renderGlobalCockpit();
    await screen.findByRole("combobox", { name: /agent/i });

    // The panel starts collapsed.
    fireEvent.click(screen.getByRole("button", { name: /show deliverables panel/i }));
    fireEvent.click(await screen.findByRole("tab", { name: /activity/i }));
    expect(await screen.findByText("read_repo")).toBeInTheDocument();
  });

  it("feeds the status line, so 'working' is the socket's word and not a constant", async () => {
    // The tab's status line takes `working` and `connectionStatus` from the socket.
    // Both were wired but unpinned: replacing them with `false` / "idle" left the
    // whole suite green, which would have shown an idle Orchestrator throughout a
    // turn and hidden a reconnect.
    socketBusy = true;
    socketConnState = "reconnecting";
    renderGlobalCockpit();
    await screen.findByRole("combobox", { name: /agent/i });

    fireEvent.click(screen.getByRole("button", { name: /show deliverables panel/i }));
    fireEvent.click(await screen.findByRole("tab", { name: /activity/i }));

    // The composer also says "reconnecting", so scope to the panel's status line.
    expect((await screen.findAllByText(/reconnecting/i)).length).toBeGreaterThan(0);
    expect(screen.getByText(/^Working…$/)).toBeInTheDocument();
  });
});

describe("OrchestratorCockpit — switching project with a session open", () => {
  it("starts a NEW run against the new project rather than reusing the old one", async () => {
    renderGlobalCockpit();
    await screen.findByRole("combobox", { name: /agent/i });

    sendMessage("first turn on Alpha");
    await waitFor(() => expect(sendTurn).toHaveBeenCalledTimes(1));
    const firstRun = await runIdOfTurn(0);
    expect(createRun.mock.calls[0]![0].project_id).toBe("p1");

    fireEvent.click(screen.getByText("pick-p2"));

    sendMessage("second turn, after switching to Beta");
    await waitFor(() => expect(sendTurn).toHaveBeenCalledTimes(2));
    const secondRun = await runIdOfTurn(1);

    expect(secondRun).not.toBe(firstRun);
    expect(createRun).toHaveBeenCalledTimes(2);
    expect(createRun.mock.calls[1]![0].project_id).toBe("p2");
  });

  it("clears the transcript, because it described the other project", async () => {
    // `resetSocket` is what drops the messages on screen. Leaving Alpha's
    // conversation under a header that now says Beta is the visible half of the
    // same defect.
    renderGlobalCockpit();
    await screen.findByRole("combobox", { name: /agent/i });

    sendMessage("first turn on Alpha");
    await waitFor(() => expect(sendTurn).toHaveBeenCalledTimes(1));
    resetSocket.mockClear();

    fireEvent.click(screen.getByText("pick-p2"));

    await waitFor(() => expect(resetSocket).toHaveBeenCalled());
  });
});
