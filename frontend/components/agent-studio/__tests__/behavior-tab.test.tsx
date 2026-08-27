// @vitest-environment jsdom
/**
 * BehaviorTab evaluation-gated Propose (spec: agent-studio-4-evaluation-gated-
 * promotion, Task 7). The backend now 422s EVALUATION_REQUIRED from propose()
 * without a passing evaluation for a draft's EXACT version (Tasks 1-5); Task 6
 * built `evaluateAgentProfile(id)` (see lib/api/agent-profiles.ts). This file
 * wires an "Evaluate" action in so a non-owner can run it and see the result
 * before Propose becomes clickable.
 *
 * Three things pinned down here:
 *   - Propose stays disabled until a passing evaluation exists for the
 *     CURRENT top draft, and Evaluate returning "pass" enables it.
 *   - The owner's Publish path is completely unaffected by all of this — no
 *     evaluation, no gating, same as before this task.
 *   - R3 self-block: at org scope, a draft's own author can't run its
 *     Evaluate button (disabled with an explanatory title).
 *
 * Mocking convention follows skills-tab.test.tsx / personal-tier-smoke.test.tsx:
 * this repo has no wired MSW server for component tests, so `@/lib/api/agent-
 * profiles` is mocked directly with vi.mock, and the component is wrapped in a
 * fresh QueryClientProvider.
 */
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/lib/api/agent-profiles", () => ({
  getAgentProfilesSummary: vi.fn(),
  listAgentProfileVersions: vi.fn(),
  createAgentProfileDraft: vi.fn(),
  publishAgentProfile: vi.fn(),
  unpublishAgentProfile: vi.fn(),
  previewAgentProfile: vi.fn().mockResolvedValue({ layers: [], warnings: [] }),
  proposeAgentProfilePublish: vi.fn(),
  evaluateAgentProfile: vi.fn(),
  getLintViolations: vi.fn().mockReturnValue(null),
}));
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

let mockSession: { user: { id: string } } | null = null;
vi.mock("@/components/auth/session-provider", () => ({
  useRawSession: () => mockSession,
}));

import {
  evaluateAgentProfile,
  listAgentProfileVersions,
  proposeAgentProfilePublish,
} from "@/lib/api/agent-profiles";
import type { AgentProfileSummaryEntry, AgentProfileVersion } from "@/lib/schemas/agent-profiles";

import { BehaviorTab } from "../behavior-tab";
import type { ScopeContext } from "../agent-editor";

afterEach(() => {
  cleanup();
  mockSession = null;
  vi.clearAllMocks();
});

const mockedListVersions = vi.mocked(listAgentProfileVersions);
const mockedEvaluate = vi.mocked(evaluateAgentProfile);
const mockedPropose = vi.mocked(proposeAgentProfilePublish);

const SUMMARY: AgentProfileSummaryEntry = {
  agent_id: "requirements",
  active_version: 1,
  latest_version: 2,
  draft_count: 1,
  updated_at: null,
  active: { prompt_prepend: "base", prompt_append: "", output_contract_extra: "" },
  inherited_from: null,
};

function draftVersion(overrides: Partial<AgentProfileVersion> = {}): AgentProfileVersion {
  return {
    id: "draft-1",
    version: 2,
    is_active: false,
    prompt_prepend: "base",
    prompt_append: "",
    output_contract_extra: "",
    created_by: "author-1",
    created_at: null,
    updated_at: null,
    published_by: null,
    published_at: null,
    ...overrides,
  };
}

function projectScopeContext(isOwner: boolean): ScopeContext {
  return {
    scope: "project",
    scopeId: "proj-1",
    scopeLabel: "Payments",
    chain: { workspaceId: "ws-1", projectId: "proj-1", userId: null },
    isOwner,
    canPropose: !isOwner,
    ownerRoleLabel: "Project Admin",
    workspaceId: "ws-1",
    workspaceName: "Acme BU",
    projectId: "proj-1",
    projectName: "Payments",
  };
}

function orgScopeContext(isOwner: boolean): ScopeContext {
  return {
    scope: "org",
    scopeId: null,
    scopeLabel: "Organization",
    chain: { workspaceId: null, projectId: null, userId: null },
    isOwner,
    canPropose: !isOwner,
    ownerRoleLabel: "Organization Admin",
  };
}

function renderBehaviorTab(scopeContext: ScopeContext, summary: AgentProfileSummaryEntry = SUMMARY) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <BehaviorTab
        agentId="requirements"
        agentLabel="Requirements"
        summary={summary}
        scopeContext={scopeContext}
      />
    </QueryClientProvider>,
  );
}

describe("BehaviorTab evaluation-gated Propose", () => {
  it("disables Propose until a passing evaluation exists for the current draft, and enables it after Evaluate returns pass", async () => {
    mockedListVersions.mockResolvedValue({ versions: [draftVersion()] });
    mockedEvaluate.mockResolvedValue({
      id: "eval-1",
      target_type: "profile",
      target_id: "draft-1",
      agent_id: "requirements",
      scope: "project",
      result: "pass",
      score: 0.75,
      signals: {},
      evaluator_id: "user-1",
      evaluator_role: "developer",
      created_at: null,
    });

    const user = userEvent.setup();
    renderBehaviorTab(projectScopeContext(false));

    expect(await screen.findByRole("button", { name: /propose/i })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: /evaluate/i }));
    await waitFor(() => expect(mockedEvaluate).toHaveBeenCalledWith("draft-1"));
    expect(await screen.findByText(/pass/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /propose/i })).toBeEnabled();
  });

  it("keeps Propose disabled when Evaluate returns fail", async () => {
    mockedListVersions.mockResolvedValue({ versions: [draftVersion()] });
    mockedEvaluate.mockResolvedValue({
      id: "eval-2",
      target_type: "profile",
      target_id: "draft-1",
      agent_id: "requirements",
      scope: "project",
      result: "fail",
      score: 0.2,
      signals: {},
      evaluator_id: "user-1",
      evaluator_role: "developer",
      created_at: null,
    });

    const user = userEvent.setup();
    renderBehaviorTab(projectScopeContext(false));

    await user.click(await screen.findByRole("button", { name: /evaluate/i }));
    await waitFor(() => expect(mockedEvaluate).toHaveBeenCalled());
    expect(await screen.findByText(/fail/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /propose/i })).toBeDisabled();
  });

  it("has no non-owner path to open the confirm dialog before a passing evaluation exists", async () => {
    // The outer Propose button is the ONLY way a non-owner reaches the
    // confirm dialog (VersionHistory's own onRequestPublish trigger is
    // canManage-gated, i.e. owner-only) — so keeping it disabled here is
    // sufficient to guarantee proposeMut is unreachable pre-evaluation. The
    // dialog's confirm button additionally re-checks the same evaluation
    // state as defense in depth (see behavior-tab.tsx).
    mockedListVersions.mockResolvedValue({ versions: [draftVersion()] });

    renderBehaviorTab(projectScopeContext(false));

    expect(await screen.findByRole("button", { name: /propose/i })).toBeDisabled();
    expect(mockedPropose).not.toHaveBeenCalled();
  });

  it("does not gate the owner's Publish path with any evaluation requirement", async () => {
    mockedListVersions.mockResolvedValue({ versions: [draftVersion({ created_by: "owner-1" })] });

    renderBehaviorTab(projectScopeContext(true));

    // Wait for the version-history query to resolve (topDraft loads
    // asynchronously) before asserting the button's enabled state — the
    // button itself is present, just disabled, on the very first render.
    await screen.findByText(/draft v2 is ready/i);

    // The owner also gets a per-row "Publish" action inside VersionHistory
    // (canManage=isOwner) — the main action row's button is the first one.
    const [publishButton] = screen.getAllByRole("button", { name: /^publish$/i });
    expect(publishButton).toBeEnabled();
    // No Evaluate action at all on the owner's direct-publish path.
    expect(screen.queryByRole("button", { name: /evaluate/i })).not.toBeInTheDocument();
    expect(mockedEvaluate).not.toHaveBeenCalled();
  });

  it("R3: disables Evaluate with a tooltip when the org-scope draft's author is the signed-in viewer", async () => {
    mockSession = { user: { id: "author-1" } };
    mockedListVersions.mockResolvedValue({
      versions: [draftVersion({ created_by: "author-1" })],
    });

    renderBehaviorTab(orgScopeContext(false));

    // Wait for topDraft to load — the self-block check depends on
    // topDraft.created_by, which is undefined (and the button is disabled
    // for the unrelated "!topDraft" reason) before the query resolves.
    await screen.findByText(/needs a passing evaluation/i);

    const evaluateButton = screen.getByRole("button", { name: /evaluate/i });
    expect(evaluateButton).toBeDisabled();
    expect(evaluateButton).toHaveAttribute(
      "title",
      "An organization-wide default must be evaluated by someone other than its author.",
    );
    expect(mockedEvaluate).not.toHaveBeenCalled();
  });

  it("R3: does not block Evaluate for a different viewer than the draft's author at org scope", async () => {
    mockSession = { user: { id: "reviewer-2" } };
    mockedListVersions.mockResolvedValue({
      versions: [draftVersion({ created_by: "author-1" })],
    });

    renderBehaviorTab(orgScopeContext(false));

    await screen.findByText(/needs a passing evaluation/i);

    expect(screen.getByRole("button", { name: /evaluate/i })).toBeEnabled();
  });
});
