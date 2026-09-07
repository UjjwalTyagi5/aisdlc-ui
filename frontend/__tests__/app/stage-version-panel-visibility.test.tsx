// @vitest-environment jsdom
import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/**
 * Whether the freeze/publish panel appears at all.
 *
 * THE RULE: versions decide what a consuming agent may read ONLY when a project has
 * `enforceArtifactPublication` on. With it off — the default, and true of every project
 * in the live database when this was written — agents read the stage's live working
 * payload, and freezing then publishing a version changes nothing about what anyone
 * can see.
 *
 * WHY THAT MATTERS ENOUGH TO TEST. The panel sits directly above the Documents list,
 * where approval DOES decide something. A freeze-then-sign-off ceremony next to it that
 * decides nothing teaches people that the approvals on this screen are decorative,
 * which is precisely the wrong lesson to learn about the control beside it.
 */

const getProject = vi.fn();
const listStageVersions = vi.fn();

vi.mock("@/lib/api/projects", () => ({
  getProject: (...a: unknown[]) => getProject(...a),
}));

vi.mock("@/lib/api/artifact-versions", async (orig) => {
  const actual = await (orig() as Promise<Record<string, unknown>>);
  return {
    ...actual,
    listStageVersions: (...a: unknown[]) => listStageVersions(...a),
    publishStageVersion: vi.fn(),
    rejectStageVersion: vi.fn(),
    snapshotStageVersion: vi.fn(),
    getVersionConsumers: vi.fn(),
  };
});

vi.mock("@/hooks/use-session", () => ({
  useSession: () => ({
    user: { id: "u1", email: "bruno@abcbank.com" },
    permissions: ["run:create", "artifact:approve_requirements"],
  }),
}));

import { StageVersionPanel } from "@/components/app/stage-version-panel";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderPanel() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <StageVersionPanel projectId={"p1" as never} phase="requirements" />
    </QueryClientProvider>,
  );
}

describe("the stage version panel", () => {
  it("renders nothing when the project does not enforce publication", async () => {
    getProject.mockResolvedValue({ id: "p1", enforceArtifactPublication: false });
    listStageVersions.mockResolvedValue([]);

    const { container } = renderPanel();

    await waitFor(() => expect(getProject).toHaveBeenCalled());
    await waitFor(() => expect(container.textContent).toBe(""));
    expect(screen.queryByRole("button", { name: /freeze/i })).toBeNull();
  });

  it("shows the panel when the project does enforce publication", async () => {
    getProject.mockResolvedValue({ id: "p1", enforceArtifactPublication: true });
    listStageVersions.mockResolvedValue([]);

    renderPanel();

    expect(await screen.findByText(/Published version/i)).toBeTruthy();
  });

  it("offers Freeze only where the panel is shown at all", async () => {
    /** Non-vacuity for the first test: proves "no Freeze button" there is the flag
     *  doing its job, not the button being absent for some unrelated reason. */
    getProject.mockResolvedValue({ id: "p1", enforceArtifactPublication: true });
    listStageVersions.mockResolvedValue([]);

    renderPanel();

    expect(await screen.findByRole("button", { name: /freeze/i })).toBeTruthy();
  });
});
