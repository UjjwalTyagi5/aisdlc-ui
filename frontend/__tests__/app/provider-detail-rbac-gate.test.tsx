// @vitest-environment jsdom
/**
 * Regression test for the Task 11 review finding: the page's top-level role
 * dispatcher once had `role === "org_admin"` fall through past every `if` and
 * reach a full org-admin render (a relocated copy of the pre-Task-9.1 screen,
 * "Add model" button and all) instead of `RestrictedAccess`. That silently
 * re-opened the exact loophole Task 9.1 was created to close — an Org Admin
 * adding an org-wide credential from this page, which violates this plan's
 * hardest constraint ("Org Admin never adds a key, period").
 *
 * This test renders the real page component (not a copy of its logic) with
 * `useAccessScope` mocked to each resolvable role, and asserts purely on what
 * actually reaches the DOM — so a future edit that reintroduces a dedicated
 * `org_admin` branch, or otherwise lets it fall through to real content,
 * fails this test rather than needing another manual review pass to catch.
 */
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("next/navigation", () => ({
  useParams: () => ({ provider: "openai" }),
}));

vi.mock("@/hooks/use-access-scope", () => ({
  useAccessScope: vi.fn(),
}));

// BuAdminProviderDetail (mounted only for role === "bu_admin" below) fires a
// handful of its own queries unconditionally (spend, catalog, projects) even
// with zero business units — stub them so the test never makes a real network
// call, matching the mocking pattern in components/app/__tests__/add-model-dialog.test.tsx.
vi.mock("@/lib/api/models", () => ({
  assignProviderToProject: vi.fn(),
  deleteModelProvider: vi.fn(),
  getModelCatalog: vi.fn().mockResolvedValue([]),
  listModelProviders: vi.fn().mockResolvedValue([]),
}));
vi.mock("@/lib/api/projects", () => ({
  listProjects: vi.fn().mockResolvedValue({
    items: [],
    pagination: { page: 1, pageSize: 100, total: 0 },
  }),
}));
vi.mock("@/lib/api/cost", () => ({
  getSpendSeries: vi.fn().mockResolvedValue({ months: [], series: [] }),
}));

import { useAccessScope } from "@/hooks/use-access-scope";
import ProviderDetailPage from "@/app/(app)/admin/models/[provider]/page";

const mockedUseAccessScope = vi.mocked(useAccessScope);

afterEach(cleanup);

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ProviderDetailPage />
    </QueryClientProvider>,
  );
}

describe("provider detail page — role gate", () => {
  it("shows a loading state while the role is still resolving (role === null)", () => {
    mockedUseAccessScope.mockReturnValue({ role: null } as ReturnType<typeof useAccessScope>);
    renderPage();
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("denies org_admin — RestrictedAccess, and specifically no 'Add model' credential button", () => {
    mockedUseAccessScope.mockReturnValue({
      role: "org_admin",
    } as ReturnType<typeof useAccessScope>);
    renderPage();

    const alert = screen.getByRole("alert");
    // RestrictedAccess now forces ApiErrorState's `forbidden` branch (a sub-
    // project-C fix: it previously passed only title/description with no
    // `error`, so `forbidden` was permanently false there, silently skipping
    // the documented Lock-icon/no-retry treatment and — the reason it had to
    // be fixed now — making the new `action` prop, gated on `forbidden`,
    // permanently dead). That branch's heading is a fixed "You don't have
    // access" (ApiErrorState ignores `title` once forbidden), so this page's
    // own identifying copy is asserted via its `description` text instead.
    expect(alert).toHaveTextContent(/you don't have access/i);
    expect(alert).toHaveTextContent(/provider detail is being rebuilt/i);

    // The exact regression: Org Admin must never see the credential-adding
    // control that used to live on this page's org-wide view.
    expect(screen.queryByRole("button", { name: /add model/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/models\s*&\s*access/i)).not.toBeInTheDocument();
  });

  it("denies every other resolved role too (e.g. project_admin) — RestrictedAccess", () => {
    mockedUseAccessScope.mockReturnValue({
      role: "project_admin",
    } as ReturnType<typeof useAccessScope>);
    renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent(/provider detail is being rebuilt/i);
  });

  it("gives bu_admin real content, not RestrictedAccess", () => {
    // useScopedBusinessUnits (called inside BuAdminProviderDetail) reads
    // bindings/businessUnitIds/managedBusinessUnitIds off this same mocked
    // hook, so a bu_admin fixture needs those present, not just `role`.
    mockedUseAccessScope.mockReturnValue({
      role: "bu_admin",
      bindings: [],
      businessUnitIds: [],
      managedBusinessUnitIds: [],
      isOrgWide: false,
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useAccessScope>);
    renderPage();

    // No business units bound in this fixture -> the branch's own empty
    // state, which is real bu_admin content and specifically not the denied
    // gate — proves the dispatcher actually reached BuAdminProviderDetail.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(
      screen.getByText(/aren.t bound to any/i),
    ).toBeInTheDocument();
  });
});
