// @vitest-environment jsdom
import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ProjectAccessList } from "@/components/app/project-access-list";

/**
 * The project-wide default, on screen.
 *
 * These used to assert a CEILING: the unit's granted level was shown as a badge and
 * capped the picker. Backend migration 0024 removed the level from the grant — read
 * vs write is a per-stage decision now — so the assertions worth having changed with
 * it. What matters here is that the screen no longer claims a unit level it cannot
 * know, and no longer disables a level nothing bounds.
 */
vi.mock("@/lib/api/integration-access", () => ({
  listProjectIntegrationAccess: vi.fn(),
  setProjectIntegrationAccess: vi.fn(),
  clearProjectIntegrationAccess: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

import {
  clearProjectIntegrationAccess,
  listProjectIntegrationAccess,
  setProjectIntegrationAccess,
} from "@/lib/api/integration-access";

afterEach(cleanup);

function renderList(rows: unknown[], canManage = true) {
  vi.mocked(listProjectIntegrationAccess).mockResolvedValue(rows as never);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ProjectAccessList projectId="p1" canManage={canManage} />
    </QueryClientProvider>,
  );
}

const row = (over: Record<string, unknown> = {}) => ({
  kind: "connector",
  targetId: "jira",
  projectAccess: null,
  effectiveAccess: "read_write",
  effectiveLabel: "read and write",
  inherited: true,
  ...over,
});

describe("what a project may do", () => {
  it("says when no project default is set rather than leaving a blank", async () => {
    renderList([row()]);
    expect(await screen.findByText("no default")).toBeTruthy();
  });

  it("never states a level for the business unit", async () => {
    // The grant carries no level to state. A badge here claiming one would be the
    // screen inventing a ceiling that no longer exists anywhere behind it.
    renderList([row({ effectiveAccess: "read", inherited: false })]);
    await screen.findByRole("radio", { name: "Read only" });
    expect(screen.queryByText(/Business Unit: /)).toBeNull();
  });

  it("offers every level, because nothing above the project bounds it", async () => {
    renderList([row({ effectiveAccess: "read" })]);
    for (const name of ["Read only", "Write only", "Read and write"]) {
      const opt = (await screen.findByRole("radio", { name })) as HTMLButtonElement;
      expect(opt.disabled).toBe(false);
    }
  });

  it("narrows on selection", async () => {
    vi.mocked(setProjectIntegrationAccess).mockResolvedValue({
      ok: true,
      projectAccess: "read",
      effectiveAccess: "read",
      warnings: [],
    } as never);
    renderList([row()]);

    await userEvent.click(await screen.findByRole("radio", { name: "Read only" }));
    // react-query hands the mutationFn a second argument of its own, so assert on
    // the variables rather than the whole call.
    await waitFor(() => expect(setProjectIntegrationAccess).toHaveBeenCalled());
    expect(vi.mocked(setProjectIntegrationAccess).mock.calls[0]![0]).toEqual({
      projectId: "p1",
      kind: "connector",
      targetId: "jira",
      access: "read",
    });
  });

  it("offers a clear only when there is a default to clear", async () => {
    renderList([row({ inherited: false, projectAccess: "read", effectiveAccess: "read" })]);
    expect(await screen.findByRole("button", { name: /Clear the default/ })).toBeTruthy();

    cleanup();
    renderList([row()]); // no default set
    await screen.findByText("no default");
    // A clear on a row with no default is a no-op offered as an action.
    expect(screen.queryByRole("button", { name: /Clear the default/ })).toBeNull();
  });

  it("clears back to letting each stage decide", async () => {
    vi.mocked(clearProjectIntegrationAccess).mockResolvedValue({
      ok: true,
      cleared: true,
      effectiveAccess: "read_write",
    } as never);
    renderList([row({ inherited: false, projectAccess: "read", effectiveAccess: "read" })]);

    await userEvent.click(await screen.findByRole("button", { name: /Clear the default/ }));
    await waitFor(() => expect(clearProjectIntegrationAccess).toHaveBeenCalled());
    expect(vi.mocked(clearProjectIntegrationAccess).mock.calls[0]![0]).toEqual({
      projectId: "p1",
      kind: "connector",
      targetId: "jira",
    });
  });

  it("shows the level but no controls to somebody who cannot administer", async () => {
    renderList([row()], false);
    expect(await screen.findByText("read and write")).toBeTruthy();
    expect(screen.queryByRole("radio")).toBeNull();
  });

  it("explains an empty list rather than showing nothing", async () => {
    renderList([]);
    expect(await screen.findByText(/has not been granted any/)).toBeTruthy();
  });
});
