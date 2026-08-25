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
}));
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

import { listAgentSkills } from "@/lib/api/agent-skills";
import type { SkillList } from "@/lib/schemas/agent-skills";

import { SkillsTab } from "../skills-tab";
import type { ScopeContext } from "../agent-editor";

afterEach(cleanup);

const mockedListAgentSkills = vi.mocked(listAgentSkills);

function orgScopeContext(): ScopeContext {
  return {
    scope: "org",
    scopeId: null,
    scopeLabel: "Organization",
    chain: { workspaceId: null, projectId: null, userId: null },
    isOwner: true,
    canPropose: false,
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
});
