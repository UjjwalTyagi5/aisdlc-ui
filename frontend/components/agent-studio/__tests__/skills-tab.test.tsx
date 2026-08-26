// @vitest-environment jsdom
/**
 * SkillsTab cascade awareness (spec: agent-studio-1-skills-cascade-inheritance,
 * Task 9). SkillsTab used to be hardcoded to the workspace (Business Unit)
 * tier — see the "Skills tab is a separate, BU-scoped system" comment this
 * task removes from agent-editor.tsx. These tests pin down the three
 * observable behaviors that changed:
 *
 *   - it now requests skills at whichever tier scopeContext names (was always
 *     "workspace" before, regardless of what tier the viewer had drilled into)
 *   - "read-only" is now driven by scopeContext.isOwner, not the old flat
 *     session-capability check
 *   - an inherited custom skill (origin_scope !== the requested scope) now
 *     shows an origin badge and an "Override" action instead of Edit/Delete
 *
 * Follows the mocking convention from
 * components/app/__tests__/add-model-dialog.test.tsx: this repo has no wired
 * MSW server for component tests (frontend/mocks/node.ts exists but nothing
 * calls server.listen() — vitest.config.ts has no setupFiles), so API modules
 * are mocked directly with vi.mock, and (per
 * __tests__/app/provider-detail-rbac-gate.test.tsx, needed here because
 * SkillsTab uses useQuery/useMutation) the component is wrapped in a fresh
 * QueryClientProvider.
 */
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/lib/api/agent-skills", () => ({
  listAgentSkills: vi.fn(),
  getAgentSkill: vi.fn(),
  createAgentSkill: vi.fn(),
  updateAgentSkill: vi.fn(),
  toggleAgentSkill: vi.fn(),
  deleteAgentSkill: vi.fn(),
  listAgentSkillVersions: vi.fn(),
  proposeAgentSkill: vi.fn(),
  evaluateAgentSkill: vi.fn(),
}));
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

// Signed-in viewer's own id — mocked per-test, matching behavior-tab.test.tsx's
// convention (session-provider isn't wired in component tests).
let mockSession: { user: { id: string } } | null = null;
vi.mock("@/components/auth/session-provider", () => ({
  useRawSession: () => mockSession,
}));

import {
  evaluateAgentSkill,
  getAgentSkill,
  listAgentSkills,
  proposeAgentSkill,
} from "@/lib/api/agent-skills";
import type { SkillDetail, SkillList } from "@/lib/schemas/agent-skills";

import { SkillsTab } from "../skills-tab";
import type { ScopeContext } from "../agent-editor";

afterEach(() => {
  cleanup();
  mockSession = null;
  vi.clearAllMocks();
});

const mockedListAgentSkills = vi.mocked(listAgentSkills);

function orgScopeContext(isOwner = true): ScopeContext {
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

function workspaceScopeContext(isOwner: boolean): ScopeContext {
  return {
    scope: "workspace",
    scopeId: "ws-1",
    scopeLabel: "Acme BU",
    chain: { workspaceId: "ws-1", projectId: null, userId: null },
    isOwner,
    canPropose: !isOwner,
    ownerRoleLabel: "Business Unit Admin",
    workspaceId: "ws-1",
    workspaceName: "Acme BU",
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

function renderSkillsTab(scopeContext: ScopeContext) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <SkillsTab agentId="requirements" agentLabel="Requirements" scopeContext={scopeContext} />
    </QueryClientProvider>,
  );
}

describe("SkillsTab cascade awareness", () => {
  it("requests skills at the org tier when scopeContext.scope is org (was hardcoded to workspace before)", async () => {
    mockedListAgentSkills.mockResolvedValue({ skills: [] } satisfies SkillList);

    renderSkillsTab(orgScopeContext());

    await waitFor(() => expect(mockedListAgentSkills).toHaveBeenCalled());
    expect(mockedListAgentSkills).toHaveBeenCalledWith("requirements", "org", null, {
      workspaceId: null,
      projectId: null,
    });
  });

  it("read-only when scopeContext.isOwner is false, not the old flat permission check", async () => {
    mockedListAgentSkills.mockResolvedValue({
      skills: [
        {
          origin: "custom",
          skill_key: "k",
          agent_id: "requirements",
          display_name: "A Skill",
          description: null,
          when_to_use: null,
          runtime: "llm",
          enabled: true,
          editable: true,
          deletable: true,
          version: 1,
          active_version: 1,
          origin_scope: "workspace",
        },
      ],
    } satisfies SkillList);

    renderSkillsTab(workspaceScopeContext(false));

    await screen.findByText("A Skill");
    expect(screen.getByText(/read-only access/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /new skill/i })).not.toBeInTheDocument();
  });

  it("shows an origin badge and Override action for an inherited skill", async () => {
    mockedListAgentSkills.mockResolvedValue({
      skills: [
        {
          origin: "custom",
          skill_key: "shared-key",
          agent_id: "requirements",
          display_name: "Org Skill",
          description: null,
          when_to_use: null,
          runtime: "llm",
          enabled: true,
          editable: true,
          deletable: true,
          version: 1,
          active_version: 1,
          origin_scope: "org",
        },
      ],
    } satisfies SkillList);

    renderSkillsTab(workspaceScopeContext(true));

    await screen.findByText("Org Skill");
    expect(screen.getByText(/From Organization/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /override/i })).toBeInTheDocument();
  });

  it("keeps the skill_key locked to the inherited value when overriding, even after editing the display name", async () => {
    mockedListAgentSkills.mockResolvedValue({
      skills: [
        {
          origin: "custom",
          skill_key: "shared-key",
          agent_id: "requirements",
          display_name: "Org Skill",
          description: null,
          when_to_use: null,
          runtime: "llm",
          enabled: true,
          editable: false,
          deletable: false,
          version: 1,
          active_version: 1,
          origin_scope: "org",
        },
      ],
    } satisfies SkillList);

    const user = userEvent.setup();
    renderSkillsTab(workspaceScopeContext(true));

    await screen.findByText("Org Skill");
    await user.click(screen.getByRole("button", { name: /override/i }));

    const keyField = await screen.findByLabelText("Skill key");
    expect(keyField).toHaveValue("shared-key");

    const nameField = screen.getByLabelText("Display name");
    await user.clear(nameField);
    await user.type(nameField, "Our BU's Version");

    expect(keyField).toHaveValue("shared-key");
  });

  it("shows a Propose action for a non-owner viewing their own (non-inherited) custom skill, gated on a passing evaluation, and calls the API on click", async () => {
    mockedListAgentSkills.mockResolvedValue({
      skills: [{
        origin: "custom", skill_key: "team-skill", agent_id: "requirements",
        display_name: "Team Skill", description: null, when_to_use: null,
        runtime: "llm", enabled: true, editable: false, deletable: false,
        version: 1, active_version: null, origin_scope: "project",
      }],
    } satisfies SkillList);
    const mockedPropose = vi.mocked(proposeAgentSkill);
    mockedPropose.mockResolvedValue({ id: "req-1" } as any);
    const mockedEvaluate = vi.mocked(evaluateAgentSkill);
    mockedEvaluate.mockResolvedValue({
      id: "eval-1", target_type: "skill", target_id: "team-skill", agent_id: "requirements",
      scope: "project", result: "pass", score: 0.9, signals: {},
      evaluator_id: "user-1", evaluator_role: "developer", created_at: null,
    });

    const user = userEvent.setup();
    renderSkillsTab(projectScopeContext(false));

    await screen.findByText("Team Skill");
    expect(screen.getByRole("button", { name: /propose/i })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: /evaluate/i }));
    await waitFor(() => expect(mockedEvaluate).toHaveBeenCalled());

    await user.click(screen.getByRole("button", { name: /propose/i }));
    await waitFor(() => expect(mockedPropose).toHaveBeenCalled());
  });

  it("keeps Propose disabled when Evaluate returns fail", async () => {
    const mockedEvaluate = vi.mocked(evaluateAgentSkill);
    mockedEvaluate.mockResolvedValue({
      id: "eval-2", target_type: "skill", target_id: "team-skill", agent_id: "requirements",
      scope: "project", result: "fail", score: 0.1, signals: {},
      evaluator_id: "user-1", evaluator_role: "developer", created_at: null,
    });
    mockedListAgentSkills.mockResolvedValue({
      skills: [{
        origin: "custom", skill_key: "team-skill", agent_id: "requirements",
        display_name: "Team Skill", description: null, when_to_use: null,
        runtime: "llm", enabled: true, editable: false, deletable: false,
        version: 1, active_version: null, origin_scope: "project",
      }],
    } satisfies SkillList);

    const user = userEvent.setup();
    renderSkillsTab(projectScopeContext(false));

    await user.click(await screen.findByRole("button", { name: /evaluate/i }));
    await waitFor(() => expect(mockedEvaluate).toHaveBeenCalled());
    expect(await screen.findByText(/fail/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /propose/i })).toBeDisabled();
  });

  it("R3: disables Evaluate with a tooltip when the org-scope skill's author is the signed-in viewer", async () => {
    mockSession = { user: { id: "author-1" } };
    mockedListAgentSkills.mockResolvedValue({
      skills: [{
        origin: "custom", skill_key: "org-skill", agent_id: "requirements",
        display_name: "Org Skill", description: null, when_to_use: null,
        runtime: "llm", enabled: true, editable: false, deletable: false,
        version: 1, active_version: null, origin_scope: "org",
      }],
    } satisfies SkillList);
    const mockedGetSkill = vi.mocked(getAgentSkill);
    mockedGetSkill.mockResolvedValue({
      origin: "custom", skill_key: "org-skill", agent_id: "requirements",
      display_name: "Org Skill", description: null, when_to_use: null,
      runtime: "llm", enabled: true, editable: false, deletable: false,
      version: 1, active_version: null, origin_scope: "org",
      body: "body", created_by: "author-1", created_at: null, updated_at: null,
    } satisfies SkillDetail);

    renderSkillsTab(orgScopeContext(false));

    await screen.findByText("Org Skill");
    const evaluateButton = await screen.findByRole("button", { name: /evaluate/i });
    await waitFor(() => expect(evaluateButton).toBeDisabled());
    expect(evaluateButton).toHaveAttribute(
      "title",
      "An organization-wide default must be evaluated by someone other than its author.",
    );
    expect(vi.mocked(evaluateAgentSkill)).not.toHaveBeenCalled();
  });

  it("R3: does not block Evaluate for a different viewer than the skill's author at org scope", async () => {
    mockSession = { user: { id: "reviewer-2" } };
    mockedListAgentSkills.mockResolvedValue({
      skills: [{
        origin: "custom", skill_key: "org-skill", agent_id: "requirements",
        display_name: "Org Skill", description: null, when_to_use: null,
        runtime: "llm", enabled: true, editable: false, deletable: false,
        version: 1, active_version: null, origin_scope: "org",
      }],
    } satisfies SkillList);
    const mockedGetSkill = vi.mocked(getAgentSkill);
    mockedGetSkill.mockResolvedValue({
      origin: "custom", skill_key: "org-skill", agent_id: "requirements",
      display_name: "Org Skill", description: null, when_to_use: null,
      runtime: "llm", enabled: true, editable: false, deletable: false,
      version: 1, active_version: null, origin_scope: "org",
      body: "body", created_by: "author-1", created_at: null, updated_at: null,
    } satisfies SkillDetail);

    renderSkillsTab(orgScopeContext(false));

    await screen.findByText("Org Skill");
    await waitFor(() => expect(mockedGetSkill).toHaveBeenCalled());
    expect(await screen.findByRole("button", { name: /evaluate/i })).toBeEnabled();
  });
});
