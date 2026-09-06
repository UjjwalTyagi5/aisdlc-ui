// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/**
 * The composer is open, and the Orchestrator chooses unless told otherwise.
 *
 * The cockpit shipped an earlier phase with the composer nailed shut behind "the
 * engine arrives in the next phase", and then with it disabled until the user named
 * an agent. Both are gone: routing is in the engine, so the picker defaults to "Let
 * the Orchestrator choose" and a message sent that way is routed.
 *
 * What is pinned here is that the default is a REAL, VISIBLE selection rather than an
 * empty control — the common case must not look like an unanswered question — and
 * that picking an agent still overrides the router, because the router is a model and
 * a wrong decision has to be correctable in one turn.
 */

const sendTurn = vi.fn();
let socketError: string | null = null;

vi.mock("@/lib/orchestrator/use-orchestrator-socket", () => ({
  useOrchestratorSocket: () => ({
    messages: [],
    send: sendTurn,
    connState: "connected" as const,
    activeAgent: null,
    error: socketError,
    busy: false,
    reset: () => {},
  }),
}));

vi.mock("@/hooks/use-session", () => ({
  useSession: () => ({
    user: { id: "u1", name: "Ana", email: "ana@example.com", initials: "A" },
    tenant: { id: "t1", name: "ABC Bank", plan: "pro" },
    permissions: ["artifact:view"],
  }),
}));

const accessScope = { role: "project_admin" as string | null, isLoading: false };
vi.mock("@/hooks/use-access-scope", () => ({
  useAccessScope: () => accessScope,
}));

vi.mock("@/lib/api/projects", () => ({
  getProject: vi.fn(async () => ({
    id: "p1",
    name: "Core ledger",
    track: "greenfield",
    workspaceId: "w1",
  })),
  listProjects: vi.fn(async () => ({ items: [], total: 0, page: 1, pageSize: 100 })),
}));
vi.mock("@/lib/api/models", () => ({
  getModelOptions: vi.fn(async () => ({
    options: [],
    default_offering_id: null,
    default_model_id: null,
  })),
}));
interface CreateRunBody {
  project_id: string;
  offering_id?: string | null;
  model_id?: string | null;
}
const createRun = vi.fn(async (_body: CreateRunBody) => ({ runId: "run-created-once" }));
vi.mock("@/lib/api/runs", () => ({ createRun: (body: CreateRunBody) => createRun(body) }));

// The model picker runs its own cascade of queries; none of that is under test.
vi.mock("@/components/orchestrator/model-picker", () => ({
  ModelPicker: () => <div data-testid="model-picker" />,
}));

import { OrchestratorCockpit } from "@/components/orchestrator/cockpit";

function renderCockpit() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <OrchestratorCockpit lockedProjectId="p1" variant="embedded" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  // jsdom has no layout and no pointer capture; Radix's Select and the thread's
  // scroll-to-bottom both expect them.
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = vi.fn();
  Element.prototype.releasePointerCapture = vi.fn();
  sendTurn.mockClear();
  createRun.mockClear();
  socketError = null;
  accessScope.role = "project_admin";
});

/** Pick an agent from the header's picker, the way a user does. */
async function chooseAgent(label: string) {
  const picker = screen.getByRole("combobox", { name: /agent/i });
  fireEvent.keyDown(picker, { key: "ArrowDown" });
  const option = await screen.findByRole("option", { name: label });
  fireEvent.click(option);
  await waitFor(() => expect(picker).toHaveTextContent(label));
}

/** Type into the composer and send. */
function sendMessage(text: string) {
  const composer = screen.getByRole("textbox", { name: /message the orchestrator/i });
  fireEvent.change(composer, { target: { value: text } });
  fireEvent.keyDown(composer, { key: "Enter" });
}

afterEach(cleanup);

describe("OrchestratorCockpit composer", () => {
  it("defaults to letting the Orchestrator choose, as a visible selection", () => {
    // NOT an empty picker. Phase 2 started blank because there was no router and the
    // user HAD to name an agent; now the common case is that nobody names one, and an
    // empty control would make the default path look like an unanswered question.
    renderCockpit();
    const picker = screen.getByRole("combobox", { name: /agent/i });
    expect(picker).toHaveTextContent(/let the orchestrator choose/i);
  });

  it("opens the composer with no agent named, because routing is the default", () => {
    // The inverse of the Phase 2 test this replaces, which kept the composer disabled
    // until an agent was picked. Sending with no agent is now the normal path: the
    // engine routes and announces what it chose and why.
    renderCockpit();
    const composer = screen.getByRole("textbox", { name: /message the orchestrator/i });
    expect(composer).not.toBeDisabled();
    expect(composer).toHaveAttribute(
      "placeholder",
      expect.stringMatching(/orchestrator picks the agent/i),
    );
  });

  it("still lets an agent be picked, and then addresses the composer to it", async () => {
    // The override survives. A default of "let the Orchestrator choose" would be
    // worth little if choosing one yourself no longer did anything — the router is a
    // model, and a wrong decision has to be correctable in one turn.
    renderCockpit();
    await chooseAgent("Security");
    const composer = screen.getByRole("textbox", { name: /message the orchestrator/i });
    expect(composer).not.toBeDisabled();
    expect(composer).toHaveAttribute(
      "placeholder",
      expect.stringMatching(/security agent/i),
    );
  });

  it("no longer claims the engine is coming in a later phase", () => {
    const { container } = renderCockpit();
    expect(container.textContent).not.toMatch(/next phase/i);
    expect(container.textContent).not.toMatch(/nothing runs yet/i);
  });

  it("shows an error event instead of leaving the user in silence", () => {
    socketError = "That run is not available.";
    renderCockpit();
    expect(screen.getByText("That run is not available.")).toBeInTheDocument();
  });

  it("tells a non-admin why the composer is closed to them", () => {
    accessScope.role = "ba";
    renderCockpit();
    const composer = screen.getByRole("textbox", { name: /message the orchestrator/i });
    expect(composer).toBeDisabled();
    expect(composer).toHaveAttribute("placeholder", expect.stringMatching(/read-only/i));
  });
});

/**
 * Run creation, exercised through the real `handleSend`/`ensureRun` rather than
 * read off the page.
 *
 * Laziness is the property that breaks quietly: a run created at mount litters
 * the project with empty runs that nobody started and nobody notices, and a run
 * created per turn does the same one message at a time. Neither shows up on
 * screen, so only a test that counts the calls can hold the line.
 */
describe("OrchestratorCockpit run creation", () => {
  it("creates no run just because the Orchestrator was opened", async () => {
    renderCockpit();
    await screen.findByRole("combobox", { name: /agent/i });
    expect(createRun).not.toHaveBeenCalled();
  });

  it("sends no agent at all when the Orchestrator is left to choose", async () => {
    // The picker's "let the Orchestrator choose" row needs a value Radix accepts, so
    // it carries a sentinel. That sentinel must never escape the component: reaching
    // the hook it would be sent as an agent id and refused by the engine as unknown.
    renderCockpit();
    await screen.findByRole("combobox", { name: /agent/i });

    sendMessage("I need a PRD");
    await waitFor(() => expect(sendTurn).toHaveBeenCalledTimes(1));

    const turn = sendTurn.mock.calls[0]![0] as { agent?: string | null };
    expect(turn.agent ?? null).toBeNull();
  });

  it("returns to no override when the auto option is re-selected", async () => {
    renderCockpit();
    await chooseAgent("Security");
    sendMessage("scan it");
    await waitFor(() => expect(sendTurn).toHaveBeenCalledTimes(1));
    expect((sendTurn.mock.calls[0]![0] as { agent?: string | null }).agent).toBe("security");

    await chooseAgent("Let the Orchestrator choose");
    sendMessage("and now?");
    await waitFor(() => expect(sendTurn).toHaveBeenCalledTimes(2));
    const second = sendTurn.mock.calls[1]![0] as { agent?: string | null };
    expect(second.agent ?? null).toBeNull();
  });

  it("creates one run on the first turn and reuses it on the next", async () => {
    renderCockpit();
    await chooseAgent("Project Manager");

    sendMessage("draft me a schedule");
    await waitFor(() => expect(sendTurn).toHaveBeenCalledTimes(1));

    const first = sendTurn.mock.calls[0]![0] as {
      text: string;
      agent: string;
      resolveRunId: () => Promise<string>;
    };
    // The user's pick reaches the wire, under its internal id.
    expect(first.agent).toBe("plan");
    expect(first.text).toBe("draft me a schedule");
    // Still nothing created: the run is the resolver's job, not the composer's.
    expect(createRun).not.toHaveBeenCalled();

    await act(async () => {
      await expect(first.resolveRunId()).resolves.toBe("run-created-once");
    });
    expect(createRun).toHaveBeenCalledTimes(1);
    expect(createRun.mock.calls[0]![0]).toMatchObject({ project_id: "p1" });

    sendMessage("and a risk list");
    await waitFor(() => expect(sendTurn).toHaveBeenCalledTimes(2));
    const second = sendTurn.mock.calls[1]![0] as { resolveRunId: () => Promise<string> };
    await act(async () => {
      await expect(second.resolveRunId()).resolves.toBe("run-created-once");
    });
    expect(createRun).toHaveBeenCalledTimes(1);
  });

  it("mints one run, not two, when two turns race for it", async () => {
    renderCockpit();
    await chooseAgent("Requirements");

    sendMessage("first");
    await waitFor(() => expect(sendTurn).toHaveBeenCalledTimes(1));
    sendMessage("second");
    await waitFor(() => expect(sendTurn).toHaveBeenCalledTimes(2));

    const resolvers = sendTurn.mock.calls.map(
      (c) => (c[0] as { resolveRunId: () => Promise<string> }).resolveRunId,
    );
    await act(async () => {
      const ids = await Promise.all(resolvers.map((r) => r()));
      expect(ids).toEqual(["run-created-once", "run-created-once"]);
    });
    expect(createRun).toHaveBeenCalledTimes(1);
  });
});

void React;
