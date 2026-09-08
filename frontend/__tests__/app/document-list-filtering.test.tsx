// @vitest-environment jsdom
import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

/**
 * Searching and filtering the Documents panel.
 *
 * THE INTERESTING PART IS THE TWO EMPTY STATES, not the filtering itself. "No documents
 * yet" and "nothing matches your filter" look identical if you write them carelessly,
 * and confusing them is actively harmful: telling somebody the project has no documents
 * when a status filter is hiding four sends them looking for a failed upload.
 */

vi.mock("@/hooks/use-session", () => ({
  useSession: () => ({
    user: { id: "u1", email: "bruno@abcbank.com" },
    permissions: ["run:create", "artifact:approve_requirements"],
  }),
}));

vi.mock("@/hooks/use-delete-artifact", () => ({
  useDeleteArtifact: () => ({ onDelete: undefined, deletingId: null, dialog: null }),
}));

vi.mock("@/lib/api/artifacts", () => ({
  listArtifacts: vi.fn(),
  approveArtifact: vi.fn(),
  rejectArtifact: vi.fn(),
  uploadArtifact: vi.fn(),
}));

import { DocumentList } from "@/components/app/document-list";

afterEach(cleanup);

type Row = Record<string, unknown>;

const doc = (title: string, status = "approved"): Row => ({
  id: `id-${title}`,
  projectId: "p1",
  runId: null,
  type: "document",
  scope: "agent",
  stage: "requirements",
  title,
  status,
  version: 1,
  contentHash: "h",
  body: { kind: "document", filename: title, stored: true },
  phase: "requirements",
  createdBy: "agent",
  createdAt: "2026-09-07T00:00:00Z",
  updatedAt: "2026-09-07T00:00:00Z",
});

function renderList(items: Row[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <DocumentList
        projectId={"p1" as never}
        stage="requirements"
        items={items as never}
      />
    </QueryClientProvider>,
  );
}

const FIVE = ["alpha.pdf", "beta.docx", "gamma.png", "delta.pdf", "epsilon.txt"].map(
  (t) => doc(t),
);

describe("the documents panel", () => {
  it("hides the toolbar when there is little to sift", () => {
    /** A search box above two rows is furniture. Hidden, not disabled — a disabled
     *  control reads as broken rather than as unnecessary. */
    renderList([doc("alpha.pdf"), doc("beta.docx")]);
    expect(screen.queryByLabelText("Search documents")).toBeNull();
  });

  it("offers search and a status filter once the list is worth sifting", () => {
    renderList(FIVE);
    expect(screen.getByLabelText("Search documents")).toBeTruthy();
    expect(screen.getByLabelText("Filter by status")).toBeTruthy();
  });

  it("narrows the list to what was typed", async () => {
    const user = userEvent.setup();
    renderList(FIVE);

    await user.type(screen.getByLabelText("Search documents"), "delta");

    expect(screen.getByText("delta.pdf")).toBeTruthy();
    expect(screen.queryByText("alpha.pdf")).toBeNull();
  });

  it("says NO MATCH rather than no documents when a filter empties the list", async () => {
    /** THE ONE THAT MATTERS. Reusing "No documents yet" here would tell somebody their
     *  uploads are missing when they are merely filtered out. */
    const user = userEvent.setup();
    renderList(FIVE);

    await user.type(screen.getByLabelText("Search documents"), "nothing-matches-this");

    expect(screen.getByText(/No documents match/i)).toBeTruthy();
    expect(screen.queryByText(/No documents yet/i)).toBeNull();
  });

  it("still says NO DOCUMENTS YET when the project genuinely has none", () => {
    /** Non-vacuity for the test above: proves the two states are actually distinct and
     *  the first is not simply unreachable. */
    renderList([]);
    expect(screen.getByText(/No documents yet/i)).toBeTruthy();
    expect(screen.queryByText(/No documents match/i)).toBeNull();
  });

  it("clears the filters from the no-match message", async () => {
    const user = userEvent.setup();
    renderList(FIVE);

    await user.type(screen.getByLabelText("Search documents"), "zzz");
    await user.click(screen.getByRole("button", { name: /clear the filters/i }));

    expect(screen.getByText("alpha.pdf")).toBeTruthy();
  });

  it("keeps the list a fixed height however many documents arrive", () => {
    /** The reported bug: every upload made the panel taller and pushed Stories off the
     *  page. Asserted on the class because jsdom has no layout to measure. */
    const { container } = renderList(FIVE);
    const list = container.querySelector("ul");
    expect(list?.className).toContain("max-h-80");
    expect(list?.className).toContain("overflow-y-auto");
  });
});

/**
 * Who is offered Approve / Reject.
 *
 * THE UI GATE MUST MIRROR `_artifact_for_decision`, which accepts the stage's own owner
 * OR project administration. Having only the first is what left a Project Admin looking
 * at their own upload stuck on "Pending" with no button — the server would have taken
 * the approval, the screen never offered it. A gate that is stricter than the backend is
 * not "safe"; it is a feature that silently does not exist.
 */
describe("who may decide a document", () => {
  const pendingDoc = { ...doc("scope.pdf", "pending") };

  it("offers Approve to a project admin who lacks the stage permission", async () => {
    vi.resetModules();
    vi.doMock("@/hooks/use-session", () => ({
      // `approve` is project administration. NO `artifact:approve_requirements` —
      // which is exactly bruno's real binding.
      useSession: () => ({ user: { id: "u1" }, permissions: ["approve", "run:create"] }),
    }));
    const { DocumentList: Fresh } = await import("@/components/app/document-list");
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <Fresh projectId={"p1" as never} stage="requirements" items={[pendingDoc] as never} />
      </QueryClientProvider>,
    );

    expect(screen.getByRole("button", { name: /^Approve$/ })).toBeTruthy();
  });

  it("offers nothing to someone holding neither", async () => {
    /** Non-vacuity: proves the button above is the permission talking, not the row
     *  simply always rendering one. */
    vi.resetModules();
    vi.doMock("@/hooks/use-session", () => ({
      useSession: () => ({ user: { id: "u2" }, permissions: ["run:create"] }),
    }));
    const { DocumentList: Fresh } = await import("@/components/app/document-list");
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <Fresh projectId={"p1" as never} stage="requirements" items={[pendingDoc] as never} />
      </QueryClientProvider>,
    );

    expect(screen.queryByRole("button", { name: /^Approve$/ })).toBeNull();
  });
});
