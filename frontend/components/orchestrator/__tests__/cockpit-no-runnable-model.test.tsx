// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/**
 * A project with no runnable model must not be offered a Send.
 *
 * `/model/options` is the runnable set — offerings on a keyed, verified provider
 * connection, already intersected with the grant cascade. When it comes back empty
 * there is nothing the Orchestrator can answer with: the turn would reach the engine,
 * fail on model resolution, and come back as an error the user could do nothing about
 * from this screen. A composer that accepts a message it is certain cannot be answered
 * is the same failure the cockpit was rebuilt to remove — it just fails one turn later.
 *
 * The gate lives on the cockpit's own `/model/options` query rather than on a prop the
 * model picker reports, so that closing the composer does not depend on a child
 * rendering (the picker is mocked away here, exactly as the other cockpit suites do).
 * The two share a react-query key, so this is one request, not two.
 */

const sendTurn = vi.fn();

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
    reset: () => {},
    hydrate: () => {},
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

vi.mock("@/lib/api/projects", () => ({
  getProject: vi.fn(async () => ({
    id: "p1",
    name: "reall",
    track: "greenfield",
    workspaceId: "w1",
  })),
  listProjects: vi.fn(async () => ({ items: [], total: 0, page: 1, pageSize: 100 })),
}));

const RUNNABLE = [
  {
    offering_id: "off-anthropic",
    provider_id: "prov-anthropic",
    display_name: "Anthropicnewkwy",
    provider: "anthropic",
    model_id: "claude-opus-4-5",
    is_default: false,
  },
];

/** Resolved by the test, so "still loading" is a state this suite can hold. */
let optionsResponse: Promise<{
  options: typeof RUNNABLE;
  default_offering_id: string | null;
  default_model_id: string | null;
}>;

vi.mock("@/lib/api/models", () => ({
  getModelOptions: vi.fn(() => optionsResponse),
}));

vi.mock("@/lib/api/runs", () => ({
  createRun: vi.fn(async () => ({ runId: "run-1" })),
}));

vi.mock("@/lib/api/conversations", () => ({
  listConversations: vi.fn(async () => []),
  getConversationMessages: vi.fn(async () => []),
  renameConversation: vi.fn(async () => {}),
  deleteConversation: vi.fn(async () => {}),
}));

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

const composer = () => screen.getByRole("textbox", { name: /message the orchestrator/i });

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.scrollTo = vi.fn();
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = vi.fn();
  Element.prototype.releasePointerCapture = vi.fn();
  sendTurn.mockClear();
  optionsResponse = Promise.resolve({
    options: RUNNABLE,
    default_offering_id: null,
    default_model_id: null,
  });
});

afterEach(cleanup);

describe("OrchestratorCockpit — a project with nothing runnable", () => {
  it("closes the composer and says why", async () => {
    optionsResponse = Promise.resolve({
      options: [],
      default_offering_id: null,
      default_model_id: null,
    });
    renderCockpit();

    await waitFor(() => expect(composer()).toBeDisabled());
    expect(composer()).toHaveAttribute(
      "placeholder",
      expect.stringMatching(/no model this project can run/i),
    );
  });

  it("refuses the turn rather than letting it fail on model resolution", async () => {
    optionsResponse = Promise.resolve({
      options: [],
      default_offering_id: null,
      default_model_id: null,
    });
    renderCockpit();
    await waitFor(() => expect(composer()).toBeDisabled());

    fireEvent.change(composer(), { target: { value: "write me a PRD" } });
    fireEvent.keyDown(composer(), { key: "Enter" });
    expect(sendTurn).not.toHaveBeenCalled();
  });

  it("leaves the composer open when the project has a runnable model", async () => {
    renderCockpit();
    await screen.findByRole("textbox", { name: /message the orchestrator/i });

    await waitFor(() => expect(composer()).not.toBeDisabled());
    expect(composer()).toHaveAttribute(
      "placeholder",
      expect.stringMatching(/orchestrator picks the agent/i),
    );
  });

  it("does not read a request still in flight as 'nothing can run'", async () => {
    // An unresolved query is not evidence of a missing key. Closing the composer
    // while the list loads would flash a claim the app has not verified — and
    // `data?.options ?? []` is exactly how that gets written by accident.
    let release: (v: {
      options: typeof RUNNABLE;
      default_offering_id: string | null;
      default_model_id: string | null;
    }) => void = () => {};
    optionsResponse = new Promise((resolve) => {
      release = resolve;
    });

    renderCockpit();
    await screen.findByRole("textbox", { name: /message the orchestrator/i });
    expect(composer()).not.toBeDisabled();

    release({ options: RUNNABLE, default_offering_id: null, default_model_id: null });
    await waitFor(() => expect(composer()).not.toBeDisabled());
  });
});

void React;
