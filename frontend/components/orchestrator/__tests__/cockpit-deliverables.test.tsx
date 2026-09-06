// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/**
 * What the cockpit actually HANDS the panel.
 *
 * Before Phase 4 the cockpit passed `runId=""`, `activeStage=""` and
 * `artifacts={[]}` — three literals that make the panel render an empty tab, a
 * permanently empty code tree, and no expanded group. On screen that is
 * indistinguishable from an agent that produced nothing and a repo the agent failed
 * to pull.
 *
 * Those three literals are why this file exists. Reverting each of them, one at a
 * time, left the entire 83-test orchestrator suite green — the same failure the
 * Activity tab had, where `activity={[]}` survived an entire 638-test run. A test
 * that renders the panel proves the PANEL works; only capturing the props proves the
 * cockpit is feeding it.
 *
 * So the panel is mocked here and its props recorded. This is the one place that
 * asserts the wiring rather than the rendering.
 */

const sendTurn = vi.fn();
const resetSocket = vi.fn();
let deliverablesFromSocket: Array<Record<string, unknown>> = [];
let activeAgentFromSocket: string | null = null;

/** Every props object the panel has been rendered with, newest last. */
const panelProps: Array<Record<string, unknown>> = [];

vi.mock("@/components/orchestrator/artifacts-panel", () => ({
  ArtifactsPanel: (props: Record<string, unknown>) => {
    panelProps.push(props);
    return <div data-testid="panel" />;
  },
}));

vi.mock("@/lib/orchestrator/use-orchestrator-socket", () => ({
  useOrchestratorSocket: () => ({
    messages: [],
    send: sendTurn,
    connState: "connected" as const,
    activeAgent: activeAgentFromSocket,
    error: null,
    busy: false,
    activity: [],
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

const PROJECTS = [{ id: "p1", name: "Alpha", track: "greenfield", workspaceId: "w1" }];

vi.mock("@/lib/api/projects", () => ({
  getProject: vi.fn(async () => PROJECTS[0]),
  listProjects: vi.fn(async () => ({
    items: PROJECTS, total: 1, page: 1, pageSize: 100,
  })),
}));

vi.mock("@/lib/api/models", () => ({
  getModelOptions: vi.fn(async () => ({
    options: [], default_offering_id: null, default_model_id: null,
  })),
}));

vi.mock("@/lib/api/runs", () => ({
  createRun: vi.fn(async () => ({ runId: "run-abc" })),
}));

vi.mock("@/components/orchestrator/model-picker", () => ({
  ModelPicker: () => <div data-testid="model-picker" />,
}));
vi.mock("@/components/orchestrator/project-picker", () => ({
  ProjectPicker: () => <div data-testid="project-picker" />,
}));

import { OrchestratorCockpit } from "@/components/orchestrator/cockpit";
import { useOrchestratorStore } from "@/stores/orchestrator-store";

/** The props of the panel's most recent render. */
function latest(): Record<string, unknown> {
  return panelProps[panelProps.length - 1] ?? {};
}

function renderCockpit() {
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

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.scrollTo = vi.fn();
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = vi.fn();
  Element.prototype.releasePointerCapture = vi.fn();
  vi.stubGlobal("fetch", vi.fn(async () => new Response(
    JSON.stringify({ deliverables: [] }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  )));
  panelProps.length = 0;
  sendTurn.mockClear();
  resetSocket.mockClear();
  deliverablesFromSocket = [];
  activeAgentFromSocket = null;
  useOrchestratorStore.setState({ sessions: [], activeSessionId: null });
  act(() => {
    useOrchestratorStore.getState().createSession({
      projectId: "p1", projectName: "Alpha", track: "greenfield", modelKey: null,
    });
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the cockpit feeds the Deliverables panel", () => {
  it("names the tab Deliverables, not Artifacts", async () => {
    renderCockpit();
    await waitFor(() => expect(panelProps.length).toBeGreaterThan(0));
    expect(latest().tabLabel).toBe("Deliverables");
  });

  it("hands over the socket's deliverables, not an empty array", async () => {
    // The literal `artifacts={[]}` this replaces. An empty panel is what an agent
    // that produced nothing looks like, so the difference is invisible on screen.
    deliverablesFromSocket = [{
      id: "d1", agent: "security", kind: "markdown", title: "Security Report",
      content: "body", url: null, language: null, source: null,
      created_at: "2026-09-06T10:00:00Z",
    }];
    renderCockpit();
    await waitFor(() => expect(panelProps.length).toBeGreaterThan(0));
    const passed = latest().artifacts as Array<Record<string, unknown>>;
    expect(passed).toHaveLength(1);
    expect(passed[0]?.title).toBe("Security Report");
  });

  it("maps the wire's `agent` onto the panel's `stage`", async () => {
    // The panel groups by `stage`; the wire says `agent`. If this mapping is dropped
    // every deliverable lands under an undefined heading.
    deliverablesFromSocket = [{
      id: "d1", agent: "plan", kind: "markdown", title: "Sprint plan",
      content: "body", url: null, language: null, source: null, created_at: null,
    }];
    renderCockpit();
    await waitFor(() => expect(panelProps.length).toBeGreaterThan(0));
    const passed = latest().artifacts as Array<Record<string, unknown>>;
    expect(passed[0]?.stage).toBe("plan");
  });

  it("hands over the active agent, so the Development code tree can appear", async () => {
    // `activeStage=""` is why that tree never rendered: the panel synthesises it only
    // while Development is the active agent. This is the one per-agent quirk the
    // requirement names explicitly.
    activeAgentFromSocket = "development";
    renderCockpit();
    await waitFor(() => expect(panelProps.length).toBeGreaterThan(0));
    expect(latest().activeStage).toBe("development");
  });

  it("hands over the real run id once a turn has created one", async () => {
    // `runId=""` left the panel's code-tree and file-tree reads pointed at nothing,
    // which renders as an empty repo rather than as "no run yet".
    renderCockpit();
    await waitFor(() => expect(panelProps.length).toBeGreaterThan(0));
    expect(latest().runId).toBe("");

    sendMessage("I need a PRD");
    const turn = sendTurn.mock.calls[0]![0] as { resolveRunId: () => Promise<string> };
    await act(async () => {
      await turn.resolveRunId();
    });
    await waitFor(() => expect(latest().runId).toBe("run-abc"));
  });
});
