// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/**
 * The Orchestrator chooses the agent. The user does not.
 *
 * ASKED FOR DIRECTLY, 2026-09-07: "remove the top 'Let the Orchestrator choose' button:
 * we don't want to give that because that conflicts with our similar agent. The
 * orchestrator will directly choose which agent in conversation, and the user will not
 * have the option to choose."
 *
 * The dropdown offered the same nine agents the project's own agent tiles offer, one
 * control away from them, under different rules — the Orchestrator reaches all nine for
 * a Project Admin; the tiles are owner-scoped per agent. Two pickers over one roster
 * with two access models is the confusion this removes.
 *
 * WHAT IS NOT REMOVED: naming an agent IN THE CONVERSATION. `router.prefilter` still
 * answers "run the security agent" without spending a model call, and the routing design
 * rests on it — a model's decision needs a way to be overridden by the person talking to
 * it, and the conversation is where this engine puts every other decision. What is gone
 * is the out-of-band control that skipped routing before a word was read.
 *
 * The socket still ACCEPTS an `agent` field (`ws.py`'s `override_agent`); nothing sends
 * one now. That is deliberate: the field is the documented way a future surface forces
 * an agent, and removing a server-side capability is not what was asked for.
 */

vi.mock("@/lib/orchestrator/use-orchestrator-socket", () => ({
  useOrchestratorSocket: () => ({
    messages: [],
    send: vi.fn(),
    connState: "connected" as const,
    activeAgent: null,
    error: null,
    busy: false,
    activity: [],
    deliverables: [],
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

vi.mock("@/hooks/use-access-scope", () => ({
  useAccessScope: () => ({ role: "project_admin" as string | null, isLoading: false }),
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

vi.mock("@/lib/api/runs", () => ({
  createRun: vi.fn(async () => ({ runId: "run-1" })),
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

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = vi.fn();
  Element.prototype.releasePointerCapture = vi.fn();
});

// Each test renders the cockpit; without this they stack in one document and every
// `getByRole` finds several.
afterEach(cleanup);

describe("the agent override control", () => {
  it("is not rendered", async () => {
    renderCockpit();
    await waitFor(() => {
      expect(screen.getByRole("textbox", { name: /message the orchestrator/i }))
        .toBeInTheDocument();
    });
    expect(screen.queryByRole("combobox", { name: /agent/i })).toBeNull();
  });

  it("does not offer to let the Orchestrator choose, because there is no alternative", async () => {
    renderCockpit();
    await waitFor(() => {
      expect(screen.getByRole("textbox", { name: /message the orchestrator/i }))
        .toBeInTheDocument();
    });
    expect(screen.queryByText(/let the orchestrator choose/i)).toBeNull();
  });

  it("leaves the composer open and routing", async () => {
    renderCockpit();
    // Removing the control must not disable the thing it used to sit beside: a turn
    // sent with no agent named is the ONLY kind of turn now, so if this closes, the
    // Orchestrator does nothing at all.
    await waitFor(() => {
      expect(screen.getByRole("textbox", { name: /message the orchestrator/i }))
        .not.toBeDisabled();
    });
  });

  it("still tells the user the Orchestrator picks the agent", async () => {
    renderCockpit();
    // The placeholder carried this when the picker was there to explain. With the
    // control gone it is the only thing that says so, which makes it load-bearing.
    await waitFor(() => {
      expect(
        screen.getByRole("textbox", { name: /message the orchestrator/i }),
      ).toHaveAttribute("placeholder", expect.stringMatching(/orchestrator picks/i));
    });
  });
});
