// @vitest-environment jsdom
/** The tech stack the Design agent follows, shown beside it and linked to where it is chosen. */
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const getProjectTechStack = vi.fn();
vi.mock("@/lib/api/tech-stacks", () => ({ getProjectTechStack: (...a: unknown[]) => getProjectTechStack(...a) }));

import { TechStackChip } from "@/components/app/tech-stack-chip";

afterEach(() => { cleanup(); getProjectTechStack.mockReset(); });

const node = {
  id: "s1", scope: "workspace", workspace_id: "w1", project_id: null, name: "Node + Next.js",
  description: "", categories: {}, notes: "", is_default: true,
};

function view(stack: unknown, source: string, warning: string | null = null) {
  return {
    project_id: "p1", workspace_id: "w1", options: { business_unit: [], project: [] },
    selection: { tech_stack_id: null }, effective: { stack, source, warning },
    can_manage: false, can_manage_business_unit: false,
  };
}

function show() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><TechStackChip projectId="p1" /></QueryClientProvider>);
}

describe("TechStackChip", () => {
  it("names the stack the Design agent follows and links to where it is chosen", async () => {
    getProjectTechStack.mockResolvedValue(view(node, "bu_default"));
    show();
    const link = await screen.findByRole("link", { name: /Tech stack: Node \+ Next\.js/ });
    expect(link).toHaveAttribute("href", "/agent-studio?project=p1&tab=skills");
    expect(link).toHaveAttribute("title", "Business Unit default");
  });

  it("says when there is none, and carries a warning", async () => {
    getProjectTechStack.mockResolvedValue(view(null, "none", "The tech stack chosen for this project was deleted."));
    show();
    const link = await screen.findByRole("link", { name: /No tech stack set/ });
    expect(link).toHaveAttribute("title", "The tech stack chosen for this project was deleted.");
  });

  it("says so when it cannot be read", async () => {
    getProjectTechStack.mockRejectedValue(new Error("boom"));
    show();
    expect(await screen.findByText("Tech stack unavailable")).toBeInTheDocument();
  });
});
