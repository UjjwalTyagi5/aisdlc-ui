// @vitest-environment jsdom
import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@testing-library/jest-dom/vitest";

/**
 * The Checklist tab reads back the page copy `create_review_checklist` writes
 * (code_review_agent/checklist.py) — the same fixed shape; the two change together.
 */

const PAGE = `# QuickLink Code Review Checklist

Why we check.

3 checks · 1 pass · 1 fail · 0 not applicable · 1 to check

## Security

| # | Check | How to verify | Status | Note |
|---|---|---|---|---|
| 1 | No hardcoded secrets | Gitleaks passed | Pass | 0 secrets |
| 2 | Inputs validated / encoded | Read the controllers | Fail | F-001 |

## Testing

| # | Check | How to verify | Status | Note |
|---|---|---|---|---|
| 3 | New code has tests | — | To check | — |
`;

const pages = vi.hoisted(() => ({ calls: [] as string[] }));
vi.mock("@/lib/api/artifacts", () => ({
  getArtifactPage: async (id: string) => {
    pages.calls.push(id);
    return { artifactId: id, filename: "QuickLink_Code_Review_Checklist.docx", status: "draft", markdown: PAGE };
  },
}));
vi.mock("@/components/app/markdown-report", () => ({ MarkdownReport: () => <div>as a document</div> }));

import { checklistDocuments, parseChecklist, ReviewChecklistView } from "@/components/app/review-checklist-view";

function doc(id: string, title: string, createdAt: string, extra: Record<string, unknown> = {}) {
  return {
    id, projectId: "p", runId: "r", type: "document", scope: "agent", stage: "code_review", title,
    status: "draft", version: 1, contentHash: "h".repeat(64), body: { kind: "document", filename: title, stored: true },
    phase: "review", createdBy: "agent", createdAt, updatedAt: createdAt, approvedBy: null, approvedAt: null,
    downloadUrl: `/api/artifacts/${id}/download`, ...extra,
  } as never;
}

const approvals = { mayRaise: () => true, raise: vi.fn(), raisingId: null };

function renderView(documents: unknown[], onAsk = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ReviewChecklistView documents={documents as never} selectedId={null} onSelect={() => {}}
        approvals={approvals} onAsk={onAsk} busy={false} />
    </QueryClientProvider>,
  );
}

afterEach(() => { cleanup(); pages.calls = []; approvals.raise.mockClear(); });

describe("parseChecklist", () => {
  it("reads the sections and checks the agent wrote", () => {
    const parsed = parseChecklist(PAGE);
    expect(parsed.title).toBe("QuickLink Code Review Checklist");
    expect(parsed.sections.map((s) => s.section)).toEqual(["Security", "Testing"]);
    expect(parsed.sections[0]!.items[1]).toEqual({ n: "2", check: "Inputs validated / encoded", how: "Read the controllers", status: "Fail", note: "F-001" });
    expect(parsed.sections[1]!.items[0]).toMatchObject({ how: "", status: "To check", note: "" });
  });

  it("finds nothing in a checklist written as prose", () => {
    expect(parseChecklist("# A checklist\n\n- [ ] Security\n- [ ] Tests").sections).toEqual([]);
  });
});

describe("Checklist tab", () => {
  it("lists only this project's Code Review checklists, newest first", () => {
    const got = checklistDocuments([
      doc("old", "code_review_checklist.docx", "2026-09-17T10:00:00Z"),
      doc("report", "QuickLink_Code_Review_feature_x.docx", "2026-09-17T12:00:00Z"),
      doc("new", "QuickLink_Code_Review_Checklist.docx", "2026-09-17T11:00:00Z"),
      doc("design", "design_checklist.docx", "2026-09-17T13:00:00Z", { stage: "design" }),
    ] as never);
    expect(got.map((d: { id: string }) => d.id)).toEqual(["new", "old"]);
  });

  it("fills the tab with the newest checklist's checks, statuses and approval", async () => {
    renderView([doc("new", "QuickLink_Code_Review_Checklist.docx", "2026-09-17T11:00:00Z")]);
    expect(await screen.findByText("No hardcoded secrets")).toBeInTheDocument();
    expect(screen.getByText("Security")).toBeInTheDocument();
    expect(screen.getByText("F-001")).toBeInTheDocument();
    expect(screen.getByText("3 checks")).toBeInTheDocument();
    expect(screen.getByText("Draft · not yet raised for approval")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Download Word/ })).toHaveAttribute("href", "/api/artifacts/new/download");
    fireEvent.click(screen.getByRole("button", { name: "Raise for approval" }));
    expect(approvals.raise).toHaveBeenCalled();
    expect(pages.calls).toEqual(["new"]);
  });

  it("offers to create one when there is none", async () => {
    const onAsk = vi.fn();
    renderView([doc("report", "QuickLink_Code_Review_feature_x.docx", "2026-09-17T12:00:00Z")], onAsk);
    fireEvent.click(screen.getByRole("button", { name: /Create a checklist/ }));
    await waitFor(() => expect(onAsk).toHaveBeenCalled());
  });
});
