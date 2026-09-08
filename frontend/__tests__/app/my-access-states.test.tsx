// @vitest-environment jsdom
import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TooltipProvider } from "@/components/ui/tooltip";

/**
 * Three states on "My access", never two.
 *
 * `allBindings` is `[]` both when the fetch FAILED and when the person genuinely holds
 * nothing, and the page branched on the array alone. So a viewer whose session had gone
 * stale — which is what changing anybody's roles does, deliberately, by bumping their
 * token epoch — was told "You aren't assigned to anything yet". A plausible, wrong story
 * about their own access, told by the page that exists to answer that question.
 *
 * `use-access-scope` already exposed `isError`, and its docstring already warned that a
 * pending or failed request must never render as "you have access to nothing". This page
 * collapsed the states anyway.
 */

const scopeState = vi.hoisted(() => ({
  current: {} as Record<string, unknown>,
}));

vi.mock("@/hooks/use-access-scope", () => ({
  useAccessScope: () => scopeState.current,
}));

vi.mock("@/hooks/use-session", () => ({
  useSession: () => ({ user: { id: "u1", email: "bruno@abcbank.com" }, permissions: [] }),
}));

import MyAccessPage from "@/app/(app)/my-access/page";

afterEach(cleanup);

/** The page runs other queries besides the mocked scope hook; they resolve to nothing
 *  and are not what any of this is about. */
function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <MyAccessPage />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

const base = {
  role: "project_admin",
  scope: null,
  isOrgWide: false,
  allBindings: [] as unknown[],
  bindings: [] as unknown[],
  managedBusinessUnitIds: [] as string[],
  managedProjectIds: [] as string[],
  isLoading: false,
  isError: false,
  refetch: () => {},
};

describe("my access, when the scope cannot be read", () => {
  it("says the load failed rather than that you have nothing", () => {
    scopeState.current = { ...base, isError: true };
    renderPage();

    expect(screen.getByText(/Couldn't load your access/i)).toBeTruthy();
    expect(screen.queryByText(/aren't assigned to anything yet/i)).toBeNull();
  });

  it("names the stale session, because that is what usually caused it", () => {
    /** Retrying cannot help once the token epoch has moved — only signing in again
     *  can — so the copy has to say so rather than offer a button that will fail. */
    scopeState.current = { ...base, isError: true };
    renderPage();

    expect(screen.getByText(/session is out of date/i)).toBeTruthy();
    expect(screen.getByText(/Sign out and back in/i)).toBeTruthy();
  });

  it("does not claim your account is waiting for a role", () => {
    /** The other banner keyed on the same empty array, so it fired too — telling a
     *  Project Admin their account had never been given anything. */
    scopeState.current = { ...base, isError: true };
    renderPage();

    expect(screen.queryByText(/waiting for a role/i)).toBeNull();
  });
});

describe("my access, when you genuinely hold nothing", () => {
  it("still says so", () => {
    /** NON-VACUITY. The empty state is correct for a just-registered account and must
     *  survive — the fix is to tell the two apart, not to delete one. */
    scopeState.current = { ...base };
    renderPage();

    expect(screen.getByText(/aren't assigned to anything yet/i)).toBeTruthy();
    expect(screen.queryByText(/Couldn't load your access/i)).toBeNull();
  });

  it("shows neither while the request is still in flight", () => {
    /** The third state. A pending load is not an answer about access either. */
    scopeState.current = { ...base, isLoading: true };
    renderPage();

    expect(screen.queryByText(/aren't assigned to anything yet/i)).toBeNull();
    expect(screen.queryByText(/Couldn't load your access/i)).toBeNull();
  });
});
