// @vitest-environment jsdom
import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ProjectAccessList } from "@/components/app/project-access-list";

/**
 * The third rung, on screen.
 *
 * The assertions worth having are about the two things a level alone cannot tell
 * you: whether it was chosen here or is the most the organisation allows, and
 * whether a wider one is even offered.
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
  unitAccess: "read_write",
  projectAccess: null,
  effectiveAccess: "read_write",
  effectiveLabel: "read and write",
  inherited: true,
  ...over,
});

describe("what a project may do", () => {
  it("says when a level is inherited rather than chosen here", async () => {
    renderList([row()]);
    expect(await screen.findByText("inherited")).toBeTruthy();
  });

  it("names the unit's level alongside the project's", async () => {
    // "Read only" alone cannot say whether it is a narrowing you can undo here or
    // the most the organisation allows.
    renderList([row({ unitAccess: "read_write", effectiveAccess: "read", inherited: false })]);
    // The BADGE, not the radio of the same name — the badge is what states the
    // ceiling, and a bare text match finds both.
    expect(await screen.findByText(/Business Unit: Read and write/)).toBeTruthy();
  });

  it("will not offer a level above the unit's grant", async () => {
    renderList([row({ unitAccess: "read", effectiveAccess: "read" })]);
    const wider = (await screen.findByRole("radio", {
      name: "Read and write",
    })) as HTMLButtonElement;
    // Disabled, not absent — an admin who cannot find it learns nothing.
    expect(wider.disabled).toBe(true);
  });

  it("narrows on selection", async () => {
    vi.mocked(setProjectIntegrationAccess).mockResolvedValue({
      ok: true,
      unitAccess: "read_write",
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

  it("offers a reset only when there is a narrowing to undo", async () => {
    renderList([row({ inherited: false, projectAccess: "read", effectiveAccess: "read" })]);
    expect(await screen.findByRole("button", { name: /Follow the/ })).toBeTruthy();

    cleanup();
    renderList([row()]); // inherited
    await screen.findByText("inherited");
    // A reset on an inherited row is a no-op offered as an action, which reads
    // as broken.
    expect(screen.queryByRole("button", { name: /Follow the/ })).toBeNull();
  });

  it("resets back to inheriting", async () => {
    vi.mocked(clearProjectIntegrationAccess).mockResolvedValue({
      ok: true,
      cleared: true,
      effectiveAccess: "read_write",
    } as never);
    renderList([row({ inherited: false, projectAccess: "read", effectiveAccess: "read" })]);

    await userEvent.click(await screen.findByRole("button", { name: /Follow the/ }));
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
