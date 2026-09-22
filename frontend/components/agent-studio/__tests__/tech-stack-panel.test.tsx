// @vitest-environment jsdom
/**
 * Tech stack · all agents — a Business Unit offers stacks and marks one its default; a
 * project admin picks exactly one; everyone else reads. (Spec:
 * docs/superpowers/specs/2026-09-22-agent-studio-tech-stacks-design.md)
 */
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const api = vi.hoisted(() => ({
  getTechStackCatalog: vi.fn(),
  listBusinessUnitTechStacks: vi.fn(),
  getProjectTechStack: vi.fn(),
  selectProjectTechStack: vi.fn(),
  setDefaultTechStack: vi.fn(),
  deleteTechStack: vi.fn(),
  createTechStack: vi.fn(),
  updateTechStack: vi.fn(),
}));
vi.mock("@/lib/api/tech-stacks", () => api);
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { TechStackPanel } from "@/components/agent-studio/tech-stack-panel";
import { ApiRequestError } from "@/lib/api/client";

const CATALOG = {
  categories: [
    { id: "languages", label: "Languages", suggestions: ["Java", "TypeScript"] },
    { id: "backend_frameworks", label: "Backend frameworks", suggestions: [] },
  ],
};
const node = {
  id: "s-node", scope: "workspace", workspace_id: "w1", project_id: null, name: "Node + Next.js",
  description: "Web apps", categories: { languages: ["TypeScript"] }, notes: "", is_default: true,
};
const java = { ...node, id: "s-java", name: "Java + Spring Boot", is_default: false, categories: { languages: ["Java"] } };

function ctx(scope: string, scopeId: string | null, over: Record<string, unknown> = {}) {
  return {
    scope, scopeId, scopeLabel: "Payments",
    chain: { workspaceId: "w1", projectId: scope === "project" ? "p1" : null, userId: null },
    isOwner: false, canPropose: false, ownerRoleLabel: null, ...over,
  } as never;
}

function renderPanel(context: never) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><TechStackPanel scopeContext={context} /></QueryClientProvider>);
}

function projectView(over: Record<string, unknown> = {}) {
  return {
    project_id: "p1", workspace_id: "w1",
    options: { business_unit: [node, java], project: [] }, selection: { tech_stack_id: null },
    effective: { stack: node, source: "bu_default", warning: null },
    can_manage: true, can_manage_business_unit: false, ...over,
  };
}

beforeEach(() => { api.getTechStackCatalog.mockResolvedValue(CATALOG); });
afterEach(() => { cleanup(); Object.values(api).forEach((f) => f.mockReset()); });

describe("TechStackPanel", () => {
  it("at the organisation tier, says where stacks are set", () => {
    renderPanel(ctx("org", null));
    expect(screen.getByText(/offered by each Business Unit and chosen per project/i)).toBeInTheDocument();
  });

  it("shows a Business Unit's stacks with the default, and lets its admin manage them", async () => {
    api.listBusinessUnitTechStacks.mockResolvedValue({ items: [node, java], can_manage: true });
    api.setDefaultTechStack.mockResolvedValue({ ...java, is_default: true });
    renderPanel(ctx("workspace", "w1"));
    expect(await screen.findByText("Node + Next.js")).toBeInTheDocument();
    expect(screen.getByText("Default")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New tech stack" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Make Java + Spring Boot the default" }));
    await waitFor(() => expect(api.setDefaultTechStack).toHaveBeenCalledWith("s-java", true));
  });

  it("is read-only for someone who does not own the Business Unit", async () => {
    api.listBusinessUnitTechStacks.mockResolvedValue({ items: [node], can_manage: false });
    renderPanel(ctx("workspace", "w1"));
    expect(await screen.findByText("Node + Next.js")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New tech stack" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete Node + Next.js" })).not.toBeInTheDocument();
    expect(screen.getByText(/read-only/i)).toBeInTheDocument();
  });

  it("lets a project admin pick exactly one stack, and says what agents follow", async () => {
    api.getProjectTechStack.mockResolvedValue(projectView());
    api.selectProjectTechStack.mockResolvedValue(projectView({
      selection: { tech_stack_id: "s-java" }, effective: { stack: java, source: "project_selection", warning: null },
    }));
    renderPanel(ctx("project", "p1"));
    expect(await screen.findByText(/Agents follow/)).toHaveTextContent("Node + Next.js");
    expect(screen.getByRole("radio", { name: /Follow the Business Unit default/ })).toBeChecked();
    fireEvent.click(screen.getByRole("radio", { name: /Java \+ Spring Boot/ }));
    await waitFor(() => expect(api.selectProjectTechStack).toHaveBeenCalledWith("p1", "s-java"));
    expect(await screen.findByText(/Agents follow/)).toHaveTextContent("Java + Spring Boot");
  });

  it("warns when the chosen stack no longer stands, and a non-admin cannot change it", async () => {
    api.getProjectTechStack.mockResolvedValue(projectView({
      selection: { tech_stack_id: "gone" }, can_manage: false,
      effective: { stack: node, source: "bu_default", warning: "The tech stack chosen for this project was deleted." },
    }));
    renderPanel(ctx("project", "p1"));
    expect(await screen.findByRole("alert")).toHaveTextContent("was deleted");
    expect(screen.getByRole("radio", { name: /Follow the Business Unit default/ })).toBeDisabled();
  });

  it("opens a stack to read everything in it", async () => {
    api.listBusinessUnitTechStacks.mockResolvedValue({ items: [node], can_manage: false });
    renderPanel(ctx("workspace", "w1"));
    fireEvent.click(await screen.findByRole("button", { name: "View Node + Next.js" }));
    expect(await screen.findByRole("dialog")).toHaveTextContent("TypeScript");
  });

  it("creates a stack from the editor and shows the server's reasons on their fields", async () => {
    api.listBusinessUnitTechStacks.mockResolvedValue({ items: [], can_manage: true });
    api.createTechStack.mockRejectedValueOnce(new ApiRequestError(422, {
      detail: { violations: [{ field: "name", code: "name_length", message: "A name is 3–80 characters." }] },
    }));
    renderPanel(ctx("workspace", "w1"));
    fireEvent.click(await screen.findByRole("button", { name: "New tech stack" }));
    fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "x" } });
    const languages = screen.getByLabelText("Languages");
    fireEvent.change(languages, { target: { value: "Java" } });
    fireEvent.keyDown(languages, { key: "Enter" });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText("A name is 3–80 characters.")).toBeInTheDocument();
    expect(api.createTechStack).toHaveBeenCalledWith({
      name: "x", description: "", notes: "", categories: { languages: ["Java"] }, scope: "workspace", scope_id: "w1",
    });
  });
});
