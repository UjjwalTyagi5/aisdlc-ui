// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/**
 * Task 2 of sub-project C (approval-workflow / new request entry points): the
 * project cost page's "over its total cap" copy already promised "Request
 * headroom to continue" but had no button to act on it. This covers just the
 * one behavioral contract that matters for a page with no prior test file —
 * the button appears for the Project Admin of an over-cap project, and is
 * absent for a non-admin builder on that same project — rather than
 * inventing a broader suite for a page that has none today.
 */

let mockRole: string | null = "project_admin";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "project-1" }),
}));

vi.mock("@/hooks/use-session", () => ({
  useSession: () => ({ userId: "user-1", permissions: [] }),
}));

vi.mock("@/hooks/use-can-see-project-cost", () => ({
  useCanSeeProjectCost: () => true,
}));

vi.mock("@/lib/auth/effective-role", () => ({
  effectivePlatformRole: () => mockRole,
}));

vi.mock("@/hooks/use-workspaces", () => ({
  useActiveWorkspace: () => ({
    active: null,
    workspaces: [],
    isLoading: false,
    isError: false,
    refetch: () => {},
    setActive: () => {},
  }),
}));

const { requestProjectBudgetIncreaseSpy } = vi.hoisted(() => ({
  requestProjectBudgetIncreaseSpy: vi.fn().mockResolvedValue({}),
}));

vi.mock("@/lib/api/projects", () => ({
  getProject: vi.fn().mockResolvedValue({
    id: "project-1",
    tenantId: "tenant-1",
    name: "Over-cap Project",
    slug: "over-cap-project",
    description: null,
    workspaceId: "ws-1",
    approvalStatus: "active",
    deliveryStatus: "not_started",
    template: "custom",
    track: "greenfield",
    archived: false,
    owners: [],
    pipeline: [],
    // Over its cap: spend exceeds budget, so ratio >= 1 and the cap-state
    // block renders its "over its total cap" branch.
    monthlyBudgetUsd: 100,
    monthlySpendUsd: 150,
    lastActivityAt: "2026-08-01T00:00:00Z",
    createdAt: "2026-01-01T00:00:00Z",
  }),
  listProjects: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  requestProjectBudgetIncrease: requestProjectBudgetIncreaseSpy,
  updateProject: vi.fn(),
}));

vi.mock("@/lib/api/runs", () => ({
  listRuns: vi.fn().mockResolvedValue({ items: [] }),
}));

import ProjectCostPage from "@/app/(app)/projects/[id]/cost/page";

afterEach(() => {
  cleanup();
  requestProjectBudgetIncreaseSpy.mockClear();
});

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ProjectCostPage />
    </QueryClientProvider>,
  );
}

describe("project cost page — 'Request more budget'", () => {
  it("appears for the Project Admin of an over-cap project", async () => {
    mockRole = "project_admin";
    renderPage();

    expect(
      await screen.findByRole("button", { name: /request more budget/i }),
    ).toBeInTheDocument();
  });

  it("is absent for a Developer on the same over-cap project", async () => {
    mockRole = "developer";
    renderPage();

    // Wait for the over-cap copy to render, then confirm no request button.
    await screen.findByText(/over its total cap/i);
    expect(
      screen.queryByRole("button", { name: /request more budget/i }),
    ).not.toBeInTheDocument();
  });
});
