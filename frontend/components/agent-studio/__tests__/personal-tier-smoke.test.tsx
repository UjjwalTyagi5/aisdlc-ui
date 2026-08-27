// @vitest-environment jsdom
/**
 * Smoke test for Agent Studio sub-project 2: confirms BehaviorTab and SkillsTab
 * render without error at the personal ("user") tier and that a save round-trips
 * against the (now scope="user"-accepting) API client — catching any accidental
 * frontend assumption that the personal tier never persists. Full backend
 * authorization coverage lives in the live-DB tests (Task 7); this test only proves
 * the frontend itself has no "user" special-casing left to break.
 */
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/lib/api/agent-skills", () => ({
  listAgentSkills: vi.fn().mockResolvedValue({ skills: [] }),
  getAgentSkill: vi.fn(),
  createAgentSkill: vi.fn(),
  updateAgentSkill: vi.fn(),
  toggleAgentSkill: vi.fn(),
  deleteAgentSkill: vi.fn(),
  listAgentSkillVersions: vi.fn(),
}));
vi.mock("@/lib/api/agent-profiles", () => ({
  getAgentProfilesSummary: vi.fn().mockResolvedValue({
    agents: [{
      agent_id: "requirements", active_version: null, latest_version: null,
      draft_count: 0, updated_at: null, active: null, inherited_from: null,
    }],
  }),
  listAgentProfileVersions: vi.fn().mockResolvedValue({ versions: [] }),
  createAgentProfileDraft: vi.fn().mockResolvedValue({
    id: "draft-1", agent_id: "requirements", scope: "user", scope_id: "u1",
    version: 1, is_active: false, prompt_prepend: "", prompt_append: "",
    output_contract_extra: "", created_by: "u1", created_at: null, updated_at: null,
  }),
  publishAgentProfile: vi.fn(),
  unpublishAgentProfile: vi.fn(),
  previewAgentProfile: vi.fn().mockResolvedValue({ layers: [], warnings: [] }),
  proposeAgentProfilePublish: vi.fn(),
  getLintViolations: vi.fn().mockReturnValue(null),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

import { SkillsTab } from "../skills-tab";
import { BehaviorTab } from "../behavior-tab";
import type { ScopeContext } from "../agent-editor";
import type { AgentProfileSummaryEntry } from "@/lib/schemas/agent-profiles";

afterEach(cleanup);

function personalScopeContext(): ScopeContext {
  return {
    scope: "user",
    scopeId: "u1",
    scopeLabel: "You",
    chain: { workspaceId: "ws-1", projectId: "proj-1", userId: "u1" },
    isOwner: true,
    canPropose: false,
    ownerRoleLabel: null,
  };
}

function renderWithClient(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

describe("Agent Studio personal-tier smoke test", () => {
  it("SkillsTab renders at the personal tier without error", async () => {
    renderWithClient(
      <SkillsTab agentId="requirements" agentLabel="Requirements" scopeContext={personalScopeContext()} />,
    );
    // Exact text, not a substring match — the empty-state copy ("No personal
    // skills yet") also matches /personal skills/i and would otherwise collide.
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Your personal skills" })).toBeInTheDocument(),
    );
  });

  it("BehaviorTab renders at the personal tier and can save a draft", async () => {
    const summary: AgentProfileSummaryEntry = {
      agent_id: "requirements", active_version: null, latest_version: null,
      draft_count: 0, updated_at: null, active: null, inherited_from: null,
    };
    const { createAgentProfileDraft } = await import("@/lib/api/agent-profiles");
    const user = userEvent.setup();

    renderWithClient(
      <BehaviorTab
        agentId="requirements"
        agentLabel="Requirements"
        summary={summary}
        scopeContext={personalScopeContext()}
      />,
    );

    // "Save draft" stays disabled until a field differs from its baseline
    // (see BehaviorTab's `dirty` check) — type into the instructions field
    // first so the click actually fires the mutation.
    const instructionsField = await screen.findByLabelText(/behavior instructions/i);
    await user.type(instructionsField, "Be extra concise.");

    const saveButton = screen.getByRole("button", { name: /save draft/i });
    await user.click(saveButton);
    const mockedCreateDraft = vi.mocked(createAgentProfileDraft);
    await waitFor(() => expect(mockedCreateDraft).toHaveBeenCalled());
    const [callArg] = mockedCreateDraft.mock.calls[0]!;
    expect(callArg.scope).toBe("user");
    expect(callArg.scope_id).toBe("u1");
  });
});
