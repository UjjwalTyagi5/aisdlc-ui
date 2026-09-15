// @vitest-environment jsdom
import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Artifact, ArtifactId } from "@/lib/schemas";

/**
 * The third tab: the PROJECT's approved artifacts.
 *
 * Deliverables are what THIS RUN's agents produced, with no approval concept.
 * Artifacts are the documents any agent produced on its own page (or a person
 * uploaded) that went through approval into the project's record — the same list the
 * standalone agents show under Documents, and the same list the Orchestrator is now
 * told about on every turn. The two are similar enough to confuse and different
 * enough to matter, which is why this is a separate tab and not a second group.
 */

const listArtifacts = vi.fn();
vi.mock("@/lib/api/artifacts", () => ({
  listArtifacts: (...args: unknown[]) => listArtifacts(...args),
}));
vi.mock("@/lib/api/runs", () => ({ getRun: async () => ({ id: "run-1" }) }));
vi.mock("@/components/orchestrator/artifact-viewer", () => ({
  ArtifactViewer: () => <div data-testid="viewer" />,
}));

class _ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal("ResizeObserver", _ResizeObserver);

afterEach(cleanup);
// Braces, not an expression body: `mockReset()` RETURNS the mock, and vitest runs a
// function returned from `beforeEach` as that test's teardown — so without them the
// mock itself was called after every test, and a rejecting one failed the test at
// teardown with its own rejection.
beforeEach(() => {
  listArtifacts.mockReset();
});

const base = {
  runId: "run-1",
  activeStage: "requirements",
  artifacts: [],
  openArtifactId: null,
  onSelectArtifact: () => {},
  streamingArtifactId: null,
  collapsed: false,
  onToggle: () => {},
  tabLabel: "Deliverables",
};

const UUID_A = "942db0a8-8920-4864-9274-fa6cef071039";
const UUID_B = "5d2a1c4e-2f7b-4c1a-9e3d-1b2c3d4e5f60";
const UUID_C = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d";
const UUID_D = "7f6e5d4c-3b2a-4190-8f7e-6d5c4b3a2918";

function doc(overrides: Partial<Omit<Artifact, "id">> & { id: string }): Artifact {
  return {
    projectId: "p1",
    runId: null,
    type: "document",
    scope: "agent",
    stage: "requirements",
    uploadedBy: "agent",
    uploadNote: null,
    approvedBy: "sarthak2004@example.com",
    approvedAt: "2026-09-15T06:02:34Z",
    phase: "requirements",
    title: "QuickLink_BRD_v1.docx",
    version: 1,
    contentHash: "a".repeat(64),
    status: "approved",
    body: {
      kind: "document",
      filename: "QuickLink_BRD_v1.docx",
      contentType: null,
      sizeBytes: 39065,
      stored: true,
      awaitingApproval: false,
      rejected: false,
    },
    downloadUrl: `/api/artifacts/${overrides.id}/download`,
    createdBy: "agent",
    createdAt: "2026-09-15T06:00:00Z",
    updatedAt: "2026-09-15T06:00:00Z",
    ...overrides,
    id: overrides.id as ArtifactId,
  } as Artifact;
}

async function draw(props: Record<string, unknown>) {
  const { ArtifactsPanel } = await import("@/components/orchestrator/artifacts-panel");
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ArtifactsPanel {...base} {...props} />
    </QueryClientProvider>,
  );
}

function openArtifactsTab() {
  fireEvent.click(screen.getByRole("tab", { name: /^artifacts/i }));
}

describe("the project artifacts tab", () => {
  it("is offered beside Deliverables and Activity when the run has a project", async () => {
    listArtifacts.mockResolvedValue([]);
    await draw({ projectId: "p1" });
    const tabs = screen.getAllByRole("tab").map((t) => t.textContent?.trim() ?? "");
    expect(tabs.some((t) => /^deliverables/i.test(t))).toBe(true);
    expect(tabs.some((t) => /^activity/i.test(t))).toBe(true);
    expect(tabs.some((t) => /^artifacts/i.test(t))).toBe(true);
  });

  it("is not offered when the run has no project — there is no record to list", async () => {
    await draw({ projectId: null });
    expect(screen.queryByRole("tab", { name: /^artifacts/i })).toBeNull();
    expect(listArtifacts).not.toHaveBeenCalled();
  });

  it("lists the project's APPROVED documents, grouped by the agent that produced them", async () => {
    listArtifacts.mockResolvedValue([
      doc({ id: UUID_A, stage: "requirements", title: "QuickLink_BRD_v1.docx" }),
      doc({ id: UUID_B, stage: "design", title: "HLD.docx", phase: "design" }),
    ]);
    await draw({ projectId: "p1" });
    openArtifactsTab();

    await waitFor(() => expect(screen.getByTestId("project-artifact-group-requirements")).toBeTruthy());
    expect(screen.getByTestId("project-artifact-group-design")).toBeTruthy();
    expect(screen.getByText("QuickLink_BRD_v1.docx")).toBeTruthy();
    expect(screen.getByText("HLD.docx")).toBeTruthy();
    expect(listArtifacts).toHaveBeenCalledWith("p1");
  });

  it("leaves out what is pending, rejected, or not a stored document", async () => {
    listArtifacts.mockResolvedValue([
      doc({ id: UUID_A, title: "Approved.docx" }),
      doc({
        id: UUID_B, title: "Pending.docx", status: "awaiting_approval",
        approvedBy: null, approvedAt: null, downloadUrl: null,
      }),
      doc({
        id: UUID_C, title: "Rejected.docx", status: "rejected", downloadUrl: null,
      }),
      // A board story materialised from a run payload — approved by construction,
      // but not a document anybody approved into the record.
      doc({ id: "run-1:story:42", type: "story", title: "As a user I want…", status: "approved" }),
    ]);
    await draw({ projectId: "p1" });
    openArtifactsTab();

    await waitFor(() => expect(screen.getByText("Approved.docx")).toBeTruthy());
    expect(screen.queryByText("Pending.docx")).toBeNull();
    expect(screen.queryByText("Rejected.docx")).toBeNull();
    expect(screen.queryByText("As a user I want…")).toBeNull();
  });

  it("files a project-wide document under its own heading", async () => {
    listArtifacts.mockResolvedValue([
      doc({ id: UUID_D, stage: null, scope: "project", title: "Coding-Standard.pdf" }),
    ]);
    await draw({ projectId: "p1" });
    openArtifactsTab();

    await waitFor(() => expect(screen.getByTestId("project-artifact-group-project")).toBeTruthy());
    expect(screen.getByText("Coding-Standard.pdf")).toBeTruthy();
    expect(screen.getByText(/project-wide/i)).toBeTruthy();
  });

  it("says who approved a document and offers the one authorised download", async () => {
    listArtifacts.mockResolvedValue([doc({ id: UUID_A })]);
    await draw({ projectId: "p1" });
    openArtifactsTab();

    await waitFor(() => expect(screen.getByText("QuickLink_BRD_v1.docx")).toBeTruthy());
    const row = screen.getByTestId(`project-artifact-${UUID_A}`);
    expect(within(row).getByText(/sarthak2004@example.com/)).toBeTruthy();
    const link = within(row).getByRole("link", { name: /download/i });
    expect(link.getAttribute("href")).toBe(`/api/artifacts/${UUID_A}/download`);
  });

  it("counts the approved documents on the tab", async () => {
    listArtifacts.mockResolvedValue([
      doc({ id: UUID_A }),
      doc({ id: UUID_B, title: "Two.docx" }),
      doc({ id: UUID_C, title: "Pending.docx", status: "awaiting_approval", downloadUrl: null }),
    ]);
    await draw({ projectId: "p1" });
    await waitFor(() => {
      const tab = screen.getByRole("tab", { name: /^artifacts/i });
      expect(tab.textContent).toMatch(/2/);
    });
  });

  it("says plainly when nothing has been approved yet, and where approval happens", async () => {
    listArtifacts.mockResolvedValue([]);
    await draw({ projectId: "p1" });
    openArtifactsTab();

    await waitFor(() => expect(screen.getByText(/no approved artifacts yet/i)).toBeTruthy());
    expect(screen.getByText(/once it has been approved/i)).toBeTruthy();
  });

  it("says when the list could not be read, rather than showing an empty project", async () => {
    listArtifacts.mockRejectedValue(new Error("boom"));
    await draw({ projectId: "p1" });
    openArtifactsTab();

    await waitFor(() => expect(screen.getByText(/could not be read/i)).toBeTruthy());
    expect(screen.queryByText(/no approved artifacts yet/i)).toBeNull();
  });
});
