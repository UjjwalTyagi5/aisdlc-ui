// @vitest-environment jsdom
import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/**
 * Adding people to an EXISTING project.
 *
 * The page used to offer a bare email-and-role form, which told a Project Admin nothing
 * about what the person would actually be able to open and made them retype an address
 * the Business Unit already knows. This dialog is the create-project flow's contributor
 * staging, brought to the settings page, so the tests here are about the two things that
 * makes possible — picking from the roster, and granting an agent beyond the role — plus
 * the one thing staging several people adds: a save that is honest when only some of
 * them land.
 */

const addProjectMember = vi.fn();
const updateProjectMemberAgents = vi.fn();
const listWorkspaceMembers = vi.fn();

vi.mock("@/lib/api/project-members", () => ({
  addProjectMember: (...a: unknown[]) => addProjectMember(...a),
  updateProjectMemberAgents: (...a: unknown[]) => updateProjectMemberAgents(...a),
}));
vi.mock("@/lib/api/workspaces", () => ({
  listWorkspaceMembers: (...a: unknown[]) => listWorkspaceMembers(...a),
}));

import { AddProjectMembersDialog } from "@/components/app/add-project-members-dialog";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const ROLES = [
  { value: "ba", label: "BA (Business Analyst)" },
  { value: "developer", label: "Developer" },
] as const;

function renderDialog(props: Partial<React.ComponentProps<typeof AddProjectMembersDialog>> = {}) {
  const onAdded = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <AddProjectMembersDialog
        open
        onOpenChange={() => {}}
        projectId={"p-1" as never}
        track="greenfield"
        workspaceId="bu-1"
        roleOptions={ROLES}
        existingEmails={["already@co.com"]}
        onAdded={onAdded}
        {...props}
      />
    </QueryClientProvider>,
  );
  return { onAdded };
}

describe("adding members to an existing project", () => {
  it("stages someone typed by email and saves them with the chosen role", async () => {
    listWorkspaceMembers.mockResolvedValue([]);
    addProjectMember.mockResolvedValue({ membershipId: "m-1" });
    const user = userEvent.setup();
    const { onAdded } = renderDialog();

    await user.type(screen.getByPlaceholderText(/invite someone new by email/i), "jane@co.com");
    await user.click(screen.getByRole("button", { name: "Add" }));

    // Staged, not yet written: a settings page has no save until you press save.
    expect(addProjectMember).not.toHaveBeenCalled();
    expect(screen.getByText("jane@co.com")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: /save 1 member/i }));

    await waitFor(() =>
      expect(addProjectMember).toHaveBeenCalledWith("p-1", {
        email: "jane@co.com",
        displayName: undefined,
        roleName: "ba",
      }),
    );
    // No extras were ticked, so nothing is granted beyond the role's own agents.
    expect(updateProjectMemberAgents).not.toHaveBeenCalled();
    expect(onAdded).toHaveBeenCalled();
  });

  it("grants an extra agent as a second write, against the new membership", async () => {
    listWorkspaceMembers.mockResolvedValue([]);
    addProjectMember.mockResolvedValue({ membershipId: "m-7" });
    const user = userEvent.setup();
    renderDialog();

    // "+ Design" is offered because a BA does not own the Design agent.
    await user.click(screen.getByRole("button", { name: /design/i }));
    await user.type(screen.getByPlaceholderText(/invite someone new by email/i), "raj@co.com");
    await user.click(screen.getByRole("button", { name: "Add" }));
    await user.click(screen.getByRole("button", { name: /save 1 member/i }));

    await waitFor(() =>
      expect(updateProjectMemberAgents).toHaveBeenCalledWith("p-1", "m-7", ["design"]),
    );
  });

  it("refuses to stage somebody already on the project", async () => {
    listWorkspaceMembers.mockResolvedValue([]);
    const user = userEvent.setup();
    renderDialog();

    await user.type(
      screen.getByPlaceholderText(/invite someone new by email/i),
      "already@co.com",
    );
    await user.click(screen.getByRole("button", { name: "Add" }));

    expect(screen.queryByRole("button", { name: /save 1 member/i })).toBeNull();
  });

  it("keeps only the failures staged, so a retry repeats exactly them", async () => {
    listWorkspaceMembers.mockResolvedValue([]);
    addProjectMember
      .mockResolvedValueOnce({ membershipId: "m-1" })
      .mockRejectedValueOnce(new Error("already a member elsewhere"));
    const user = userEvent.setup();
    renderDialog();

    for (const address of ["one@co.com", "two@co.com"]) {
      await user.type(screen.getByPlaceholderText(/invite someone new by email/i), address);
      await user.click(screen.getByRole("button", { name: "Add" }));
    }
    await user.click(screen.getByRole("button", { name: /save 2 members/i }));

    await waitFor(() => expect(addProjectMember).toHaveBeenCalledTimes(2));
    // The one that landed is gone from the list; the one that did not is still there.
    await waitFor(() => expect(screen.queryByText("one@co.com")).toBeNull());
    expect(screen.getByText("two@co.com")).toBeTruthy();
  });

  it("offers the Business Unit's roster, minus whoever is already on the project", async () => {
    listWorkspaceMembers.mockResolvedValue([
      { userId: "u-1", email: "already@co.com", displayName: "Already There" },
      { userId: "u-2", email: "free@co.com", displayName: "Free Agent" },
    ]);
    renderDialog();

    await waitFor(() => expect(listWorkspaceMembers).toHaveBeenCalledWith("bu-1"));
    const picker = screen.getByRole("combobox", { name: /existing business unit member/i });
    expect(picker.textContent).not.toMatch(/No more/i);
  });
});
