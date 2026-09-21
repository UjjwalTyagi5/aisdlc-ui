// @vitest-environment jsdom
/**
 * The Code Review page's report, and its Files tab.
 *
 * IT USED TO CARRY A SECURITY REPORT TOO — a Security tab, an SBOM tab, scanner numbers in
 * the band — which the SECURITY agent produces (PRD 21.5 owns the scanning stack, the SBOM
 * and the sign-off). Both pages showed the same SBOM. What a reader must be able to trust
 * here (PRD 21.4): the verdict and the findings are the reviewer's, the report says what the
 * change was checked against, a whole-branch review says how much of the branch was read,
 * and security appears only as a finding with a file and a line.
 */
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import {
  BranchFilesView,
  CodeReviewReport,
  approvalState,
  reportDocumentFor,
  reviewChecklist,
} from "@/components/app/code-review-report";
import { CodeReviewArtifact, PrepareResult } from "@/lib/schemas/code-review";

afterEach(cleanup);

function artifact(over: Record<string, unknown> = {}) {
  return CodeReviewArtifact.parse({
    id: "r1",
    created_at: "2026-09-16T10:05:00Z",
    context: { repo_name: "QuickLink", ado_project: "QuickLink", mode: "repo", source_branch: "feature/116-117-link-management", base_branch: "", head_sha: "082f91e49bf0ffb68b063d28db7c89096b5eb247", base_sha: "" },
    summary: "## Overview\nA small Express + SQLite service.",
    merge_recommendation: "request_changes",
    findings: [
      { id: "F-002", severity: "medium", category: "logic_error", file: "src/services/LinkService.js", line: 22, description: "Slug uniqueness is not enforced by the database.", recommendation: "Add a UNIQUE constraint." },
      { id: "F-001", severity: "high", category: "security", file: "src/controllers/linkController.js", line: 14, description: "Open redirect via javascript: URLs.", recommendation: "Allow http and https only." },
    ],
    requirements_coverage: [{ ac_id: "FR-01 Create short link", status: "satisfied", note: "" }],
    design_conformance: [],
    metrics: { files_changed: 0, added: 0, removed: 0 },
    scope: { mode: "repo", files_total: 20, reviewable_files: 14, lines_total: 325, reviewable_files_read: 11, files_read: ["src/index.js"], not_read: ["tests/link.test.js"], languages: { JavaScript: 8 } },
    document: { filename: "QuickLink_Code_Review.docx", url: "http://localhost:8004/generated/u/code_review/s/output/QuickLink_Code_Review.docx" },
    ...over,
  });
}

describe("CodeReviewReport", () => {
  it("leads with the verdict, the review's own numbers and the report download", () => {
    render(<CodeReviewReport artifact={artifact()} onOpenTab={() => {}} />);
    expect(screen.getByRole("heading", { level: 2, name: "QuickLink — Request changes" })).toBeInTheDocument();
    expect(screen.getByText(/Whole branch · feature\/116-117-link-management/)).toBeInTheDocument();
    expect(screen.getByText("the whole branch feature/116-117-link-management")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Download report/ })).toHaveAttribute("href", expect.stringContaining("QuickLink_Code_Review.docx"));
    expect(screen.getByText("11 / 14")).toBeInTheDocument();
    expect(screen.getByText("1 critical/high")).toBeInTheDocument();
    // The band is the REVIEW's: no scanner numbers, no SBOM.
    expect(screen.getByText("acceptance criteria met")).toBeInTheDocument();
    expect(screen.queryByText("SBOM")).not.toBeInTheDocument();
    expect(screen.queryByText("Vulnerabilities")).not.toBeInTheDocument();
    expect(screen.queryByText("Secrets")).not.toBeInTheDocument();
  });

  it("leaves scanning, the SBOM and the sign-off to the Security agent", () => {
    render(<CodeReviewReport artifact={artifact()} onOpenTab={() => {}} />);
    expect(screen.queryByRole("heading", { name: /Security checks/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /bill of materials/i })).not.toBeInTheDocument();
    expect(screen.getByText(/are the\s+Security agent's report, not this one/)).toBeInTheDocument();
  });

  it("shows why there is no report when it could not be written", () => {
    render(<CodeReviewReport artifact={artifact({ document: { error: "The report document could not be written (OSError: disk full)" } })} onOpenTab={() => {}} />);
    expect(screen.getByText(/disk full/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Download report/ })).not.toBeInTheDocument();
  });

  it("names the approved documents the code was checked against, and one that could not be read", () => {
    const base = artifact().scope!;
    render(<CodeReviewReport artifact={artifact({ scope: { ...base, documents: [
      { title: "TEST_Project_BRD.docx", stage: "requirements", outcome: "ok" },
      { title: "architecture.docx", stage: "design", outcome: "that document has no stored file" },
    ] } })} onOpenTab={() => {}} />);
    expect(screen.getByText("TEST_Project_BRD.docx")).toBeInTheDocument();
    expect(screen.getByText(/could not be read: that document has no stored file/)).toBeInTheDocument();
    expect(screen.getByText(/with the project's approved requirements and design documents/)).toBeInTheDocument();
  });

  it("claims nothing about documents on a review saved before they were recorded", () => {
    render(<CodeReviewReport artifact={artifact()} onOpenTab={() => {}} />);
    expect(screen.queryByText(/Checked against/)).not.toBeInTheDocument();
    expect(screen.queryByText(/approved requirements and design documents/)).not.toBeInTheDocument();
    expect(screen.queryByText(/had no approved requirements/)).not.toBeInTheDocument();
  });

  it("orders findings by severity and links to the full tabs", () => {
    const onOpenTab = vi.fn();
    render(<CodeReviewReport artifact={artifact()} onOpenTab={onOpenTab} />);
    // The findings table's rows start with the finding id (the checklist's mention ids in their detail).
    const rows = screen.getAllByRole("row").map((r) => r.textContent ?? "");
    expect(rows.findIndex((r) => r.startsWith("F-001"))).toBeLessThan(rows.findIndex((r) => r.startsWith("F-002")));
    fireEvent.click(screen.getByRole("button", { name: "All findings" }));
    expect(onOpenTab).toHaveBeenCalledWith("findings");
  });
});

describe("BranchFilesView", () => {
  it("lists a prepared whole branch's files", () => {
    const prepared = PrepareResult.parse({
      status: "ready", mode: "repo", repo_name: "QuickLink", ado_project: "QuickLink", source_branch: "feature/x", base_branch: "",
      head_sha: "abc", base_sha: "", truncated: false, diff: "",
      files: [
        { path: "src/index.js", status: "T", added: 40, removed: 0, language: "JavaScript", reviewable: true },
        { path: "src/.gitkeep", status: "T", added: 0, removed: 0, language: "Other", reviewable: false },
      ],
    });
    render(<BranchFilesView prepared={prepared} artifact={null} />);
    expect(screen.getByText("src/index.js")).toBeInTheDocument();
    expect(screen.getByText(/1 reviewable/)).toBeInTheDocument();
  });

  it("marks what a saved review read and did not read", () => {
    render(<BranchFilesView prepared={null} artifact={artifact()} />);
    expect(screen.getByText("read")).toBeInTheDocument();
    expect(screen.getByText("not read")).toBeInTheDocument();
  });
});

function documentRow(over: Record<string, unknown> = {}) {
  return {
    id: "doc-1", projectId: "p1", runId: "r1", type: "document", scope: "agent", stage: "code_review",
    title: "QuickLink_Code_Review.docx", status: "draft", version: 1, contentHash: "h".repeat(64),
    body: { kind: "document", filename: "QuickLink_Code_Review.docx", stored: true },
    phase: "review", createdBy: "agent", createdAt: "2026-09-16T10:04:58Z", updatedAt: "2026-09-16T10:04:58Z",
    approvedBy: null, approvedAt: null,
    ...over,
  } as never;
}

describe("The report's approval", () => {
  it("links a review to its own document by id, even among identically named reports", () => {
    const review = artifact({ document: { filename: "QuickLink_Code_Review.docx", url: "u", artifact_id: "doc-2" } });
    const docs = [documentRow({ id: "doc-1" }), documentRow({ id: "doc-2", status: "approved" })];
    expect((reportDocumentFor(review, docs) as { id: string }).id).toBe("doc-2");
  });

  it("links an older review (no id) to the same-named document saved closest to it, within ten minutes", () => {
    const review = artifact({ document: { filename: "QuickLink_Code_Review.docx", url: "u" } });
    const docs = [
      documentRow({ id: "far", createdAt: "2026-09-16T09:00:00Z" }),
      documentRow({ id: "near", createdAt: "2026-09-16T10:04:58Z" }),
      documentRow({ id: "other-name", title: "Other.docx", createdAt: "2026-09-16T10:05:00Z" }),
    ];
    expect((reportDocumentFor(review, docs) as { id: string }).id).toBe("near");
    expect(reportDocumentFor(review, [documentRow({ id: "far", createdAt: "2026-09-16T09:00:00Z" })])).toBeNull();
  });

  it("says Draft, Raised for approval, Approved or Rejected", () => {
    expect(approvalState(documentRow()).label).toBe("Draft · not yet raised for approval");
    expect(approvalState(documentRow({ status: "awaiting_approval" })).label).toBe("Raised for approval · waiting on the approver");
    expect(approvalState(documentRow({ status: "approved", approvedBy: "sarthakk2004@gmail.com" })).label).toBe("Approved by sarthakk2004@gmail.com");
    expect(approvalState(documentRow({ status: "rejected" })).label).toBe("Rejected");
  });

  it("shows the status in the report header and offers Raise for approval on a draft", () => {
    const onRaise = vi.fn();
    render(<CodeReviewReport artifact={artifact()} onOpenTab={() => {}}
      approval={{ document: documentRow(), mayRaise: true, raising: false, onRaise }} />);
    expect(screen.getByText("Draft · not yet raised for approval")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Raise for approval" }));
    expect(onRaise).toHaveBeenCalledTimes(1);
  });

  it("says it has been raised, and offers no second raise", () => {
    render(<CodeReviewReport artifact={artifact()} onOpenTab={() => {}}
      approval={{ document: documentRow({ status: "awaiting_approval" }), mayRaise: true, raising: false, onRaise: vi.fn() }} />);
    expect(screen.getByText("Raised for approval · waiting on the approver")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Raise for approval" })).not.toBeInTheDocument();
  });

  it("says Approved once it is", () => {
    render(<CodeReviewReport artifact={artifact()} onOpenTab={() => {}}
      approval={{ document: documentRow({ status: "approved", approvedBy: "sarthakk2004@gmail.com" }), mayRaise: true, raising: false, onRaise: vi.fn() }} />);
    expect(screen.getByText("Approved by sarthakk2004@gmail.com")).toBeInTheDocument();
  });

  it("offers no raise to someone who may not send it", () => {
    render(<CodeReviewReport artifact={artifact()} onOpenTab={() => {}}
      approval={{ document: documentRow(), mayRaise: false, raising: false, onRaise: vi.fn() }} />);
    expect(screen.queryByRole("button", { name: "Raise for approval" })).not.toBeInTheDocument();
  });
});

describe("CodeReviewReport as a code review report", () => {
  it("is titled a code review report and answers the standard review checklist", () => {
    render(<CodeReviewReport artifact={artifact()} onOpenTab={() => {}} />);
    expect(screen.getByText("Code review report", { exact: false })).toBeInTheDocument();
    expect(screen.queryByText(/security report/i)).not.toBeInTheDocument();
    for (const check of ["Whole change read", "Security issues in the code", "Meets the approved requirements",
      "Follows the approved architecture", "Logic and correctness", "Merge recommendation"]) {
      expect(screen.getByText(check)).toBeInTheDocument();
    }
  });

  it("never marks a check done that nothing established", () => {
    const rows = reviewChecklist(artifact({ requirements_coverage: [], design_conformance: [] }));
    const byCheck = Object.fromEntries(rows.map((r) => [r.check, r.result]));
    expect(byCheck["Meets the approved requirements"]).toBe("Not checked");
    expect(byCheck["Follows the approved architecture"]).toBe("Not checked");
    // The security row is the reviewer's own finding — never a claim about a scan.
    expect(byCheck["Security issues in the code"]).toBe("Issues found");
    expect(Object.keys(byCheck)).not.toContain("Security checks run");
  });
});
