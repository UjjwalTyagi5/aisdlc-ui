// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/**
 * The left rail is real history: server-stored, openable, continuable.
 *
 * It was `zustand` + localStorage and said so on screen — "Sessions are stored in this
 * browser only." Clearing site data lost every chat, another device never had them, and
 * none of it was auditable. Spec §5.3 asked for server-backed sessions in Phase 1 and
 * Phases 1-4 never built them.
 *
 * The load-bearing test here is `continues an opened chat against its own run`. Session
 * id IS run id, so reopening a chat and sending a turn must rejoin THAT run — its
 * LangGraph thread, its Deliverables, its project scope — rather than minting a new
 * one. If that breaks, the rail still looks right and every conversation silently
 * starts over.
 */

const sendTurn = vi.fn();
const resetSocket = vi.fn();
const hydrate = vi.fn();
const createRun = vi.fn<(body: unknown) => Promise<{ runId: string }>>(
  async () => ({ runId: "run-NEW" }),
);
const listConversations =
  vi.fn<(projectId: string, agentId: string) => Promise<unknown[]>>();
const getConversationMessages =
  vi.fn<(id: string) => Promise<unknown[]>>();
const renameConversation =
  vi.fn<(id: string, title: string) => Promise<unknown>>(async () => ({ id: "x", title: "t" }));
const deleteConversation = vi.fn<(id: string) => Promise<unknown>>(async () => ({}));

vi.mock("@/components/orchestrator/artifacts-panel", () => ({
  ArtifactsPanel: () => <div data-testid="panel" />,
}));

vi.mock("@/lib/orchestrator/use-orchestrator-socket", () => ({
  useOrchestratorSocket: () => ({
    messages: [],
    send: sendTurn,
    connState: "connected" as const,
    activeAgent: null,
    error: null,
    busy: false,
    activity: [],
    deliverables: [],
    hydrate,
    reset: resetSocket,
  }),
}));

vi.mock("@/lib/api/conversations", () => ({
  listConversations: (p: string, a: string) => listConversations(p, a),
  getConversationMessages: (id: string) => getConversationMessages(id),
  renameConversation: (id: string, t: string) => renameConversation(id, t),
  deleteConversation: (id: string) => deleteConversation(id),
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
  listProjects: vi.fn(async () => ({ items: PROJECTS, total: 1, page: 1, pageSize: 100 })),
}));

vi.mock("@/lib/api/models", () => ({
  getModelOptions: vi.fn(async () => ({
    options: [], default_offering_id: null, default_model_id: null,
  })),
}));

vi.mock("@/lib/api/runs", () => ({ createRun: (b: unknown) => createRun(b) }));

vi.mock("@/components/orchestrator/model-picker", () => ({
  ModelPicker: () => <div data-testid="model-picker" />,
}));
vi.mock("@/components/orchestrator/project-picker", () => ({
  ProjectPicker: () => <div data-testid="project-picker" />,
}));

import { OrchestratorCockpit } from "@/components/orchestrator/cockpit";
import { useOrchestratorStore } from "@/stores/orchestrator-store";

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
  sendTurn.mockClear();
  resetSocket.mockClear();
  hydrate.mockClear();
  createRun.mockClear();
  listConversations.mockReset().mockResolvedValue([]);
  getConversationMessages.mockReset().mockResolvedValue([]);
  useOrchestratorStore.setState({ sessions: [], activeSessionId: null });
  // A project has to be selected for the rail to have a history to list — the picker
  // is mocked out, so seed the same way the sibling cockpit tests do.
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

describe("the Orchestrator rail is server history", () => {
  it("lists the project's saved chats, not this browser's", async () => {
    listConversations.mockResolvedValue([
      { id: "run-1", title: "Billing PRD", agent_id: "orchestrator" },
      { id: "run-2", title: "Auth review", agent_id: "orchestrator" },
    ]);
    renderCockpit();
    expect(await screen.findByText("Billing PRD")).toBeTruthy();
    expect(await screen.findByText("Auth review")).toBeTruthy();
    await waitFor(() =>
      expect(listConversations).toHaveBeenCalledWith("p1", "orchestrator"));
  });

  it("opens a chat and replays its transcript", async () => {
    listConversations.mockResolvedValue([{ id: "run-1", title: "Billing PRD" }]);
    getConversationMessages.mockResolvedValue([
      { id: "m1", seq: 1, role: "user", content: "I need a PRD", content_type: "text" },
      { id: "m2", seq: 2, role: "agent", content: "Here it is", content_type: "text" },
    ]);
    renderCockpit();
    fireEvent.click(await screen.findByText("Billing PRD"));

    await waitFor(() => expect(getConversationMessages).toHaveBeenCalledWith("run-1"));
    await waitFor(() => expect(hydrate).toHaveBeenCalled());
    const restored = hydrate.mock.calls.at(-1)![0] as Array<Record<string, unknown>>;
    expect(restored.map((m) => m.content)).toEqual(["I need a PRD", "Here it is"]);
    expect(restored.map((m) => m.role)).toEqual(["user", "agent"]);
  });

  it("continues an opened chat against its own run", async () => {
    // The load-bearing one. Session id IS run id, so the next turn must rejoin THAT
    // run — its LangGraph thread, its Deliverables, its project scope — and mint
    // nothing. If this breaks, the rail still looks right and every conversation
    // silently starts over.
    listConversations.mockResolvedValue([{ id: "run-1", title: "Billing PRD" }]);
    renderCockpit();
    fireEvent.click(await screen.findByText("Billing PRD"));
    await waitFor(() => expect(hydrate).toHaveBeenCalled());

    sendMessage("and now the design");
    await waitFor(() => expect(sendTurn).toHaveBeenCalled());
    const turn = sendTurn.mock.calls[0]![0] as { resolveRunId: () => Promise<string> };
    let id = "";
    await act(async () => { id = await turn.resolveRunId(); });
    expect(id).toBe("run-1");
    expect(createRun).not.toHaveBeenCalled();
  });

  it("does not create a run just for clicking New", async () => {
    // A new chat is a draft until its first turn, or every stray click mints a run
    // nobody used and litters the rail with empty conversations.
    renderCockpit();
    await waitFor(() => expect(listConversations).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: /new/i }));
    expect(createRun).not.toHaveBeenCalled();
  });

  it("no longer claims sessions live only in this browser", async () => {
    renderCockpit();
    await waitFor(() => expect(listConversations).toHaveBeenCalled());
    expect(screen.queryByText(/stored in this browser only/i)).toBeNull();
  });
});
