// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/**
 * The composer is open, and it says who is missing.
 *
 * The cockpit shipped its last phase with the composer nailed shut behind "the
 * engine arrives in the next phase". The engine exists now, so the disabled
 * composer and that copy must be gone — but the thing worth pinning is what
 * replaced them: an agent picker that starts EMPTY. Phase 2 has no router, so a
 * default here would be the UI choosing an agent on the user's behalf and
 * calling it their choice, which is the failure this rebuild exists to end.
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
vi.mock("@/lib/api/runs", () => ({ createRun: vi.fn() }));

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
  // jsdom has no layout, so the thread's scroll-to-bottom needs a stand-in.
  Element.prototype.scrollIntoView = vi.fn();
  sendTurn.mockClear();
  socketError = null;
  accessScope.role = "project_admin";
});

afterEach(cleanup);

describe("OrchestratorCockpit composer", () => {
  it("offers an agent picker that starts empty", () => {
    renderCockpit();
    const picker = screen.getByRole("combobox", { name: /agent/i });
    expect(picker).toHaveTextContent("Choose an agent");
  });

  it("keeps the composer closed until an agent is named — and says so", () => {
    renderCockpit();
    const composer = screen.getByRole("textbox", { name: /message the orchestrator/i });
    expect(composer).toBeDisabled();
    expect(composer).toHaveAttribute("placeholder", expect.stringMatching(/choose an agent/i));
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

void React;
