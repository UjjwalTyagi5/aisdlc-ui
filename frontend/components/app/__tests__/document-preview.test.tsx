// @vitest-environment jsdom
/**
 * The one viewer every agent page opens its documents in.
 *
 * LIVE (2026-09-21): a Documents-panel row opened a document on four agent pages and did
 * nothing on five, and no page could show a spreadsheet — the Project Manager's estimate and
 * the Testing agent's workbooks were download-only. Whatever the file, a row now opens here.
 */
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const getArtifactPreview = vi.fn();
vi.mock("@/lib/api/artifacts", () => ({
  getArtifactPreview: (...a: unknown[]) => getArtifactPreview(...a),
  artifactPreviewFileUrl: (id: string) => `/api/artifacts/${id}/preview/file`,
}));

import { ApiRequestError } from "@/lib/api/client";
import { DocumentPreview, columnLetter } from "@/components/app/document-preview";

afterEach(() => { cleanup(); getArtifactPreview.mockReset(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function artifact(over: Record<string, unknown> = {}) {
  return {
    id: "a1", projectId: "p", runId: "r", type: "document", scope: "agent", stage: "plan",
    title: "QuickLink_Estimate.xlsx", status: "draft", version: 1, contentHash: "h".repeat(64),
    body: { kind: "document", filename: "QuickLink_Estimate.xlsx", stored: true }, phase: "plan",
    createdBy: "agent", createdAt: "2026-09-21T10:00:00Z", updatedAt: "2026-09-21T10:00:00Z",
    approvedBy: null, approvedAt: null, downloadUrl: "/api/artifacts/a1/download", ...over,
  } as never;
}

const approvals = { mayRaise: () => true, raise: vi.fn(), raisingId: null };

function open(over: Record<string, unknown> = {}, props: Record<string, unknown> = {}) {
  const onClose = vi.fn();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <DocumentPreview artifact={artifact(over)} project="QuickLink" approvals={approvals as never} onClose={onClose} {...props} />
    </QueryClientProvider>,
  );
  return { onClose };
}

const SHEETS = {
  kind: "sheets", artifactId: "a1", filename: "QuickLink_Estimate.xlsx", status: "draft",
  sheets: [
    { name: "Estimate", rows: [["Task", "Owner", "Days"], ["Link creation", "Dev", "5"], ["Reporting", "Dev", "2.5"]], rows_total: 3, cols_total: 3, truncated: false },
    { name: "Notes", rows: [["Assumes one developer."]], rows_total: 1, cols_total: 1, truncated: false },
  ],
};

describe("DocumentPreview — a spreadsheet", () => {
  it("lays the workbook out as a grid, with Excel's column letters and row numbers", async () => {
    getArtifactPreview.mockResolvedValue(SHEETS);
    open();
    expect(await screen.findByText("Link creation")).toBeInTheDocument();
    for (const letter of ["A", "B", "C"]) expect(screen.getByRole("columnheader", { name: letter })).toBeInTheDocument();
    expect(screen.getByRole("rowheader", { name: "3" })).toBeInTheDocument();
    expect(getArtifactPreview).toHaveBeenCalledWith("a1");
  });

  it("switches sheets with its tabs", async () => {
    getArtifactPreview.mockResolvedValue(SHEETS);
    open();
    await screen.findByText("Link creation");
    fireEvent.click(screen.getByRole("tab", { name: "Notes" }));
    expect(screen.getByRole("tab", { name: "Notes" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("Assumes one developer.")).toBeInTheDocument();
    expect(screen.queryByText("Link creation")).not.toBeInTheDocument();
  });

  it("says when a large sheet was cut", async () => {
    getArtifactPreview.mockResolvedValue({ ...SHEETS, sheets: [{ ...SHEETS.sheets[0], rows_total: 5000, truncated: true }] });
    open();
    expect(await screen.findByText(/Showing the first 3 of 5,000 rows/)).toBeInTheDocument();
  });
});

describe("DocumentPreview — the header every document gets", () => {
  it("shows the approval standing, the download, and raising a draft", async () => {
    getArtifactPreview.mockResolvedValue(SHEETS);
    open();
    await screen.findByText("Link creation");
    expect(screen.getByText("Draft · not yet raised for approval")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Download/ })).toHaveAttribute("href", "/api/artifacts/a1/download");
    fireEvent.click(screen.getByRole("button", { name: "Raise for approval" }));
    expect(approvals.raise).toHaveBeenCalledTimes(1);
  });

  it("offers no raise on a document already put forward", async () => {
    getArtifactPreview.mockResolvedValue(SHEETS);
    open({ status: "approved", approvedBy: "sarthakk2004@gmail.com" });
    await screen.findByText("Link creation");
    expect(screen.queryByRole("button", { name: "Raise for approval" })).not.toBeInTheDocument();
    expect(screen.getByText("Approved by sarthakk2004@gmail.com")).toBeInTheDocument();
  });

  it("closes back to the page", async () => {
    getArtifactPreview.mockResolvedValue(SHEETS);
    const { onClose } = open();
    await screen.findByText("Link creation");
    fireEvent.click(screen.getByRole("button", { name: "Close document" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe("DocumentPreview — it reads as a document opened over the page, not part of it", () => {
  // LIVE (2026-09-21): a code review report opened on the Code Review page looked exactly like
  // the page's own Summary tab, and the only way back was a small ×.
  it("names the page it goes back to, in a bar that says a document is being viewed", async () => {
    getArtifactPreview.mockResolvedValue(SHEETS);
    const { onClose } = open({}, { pageName: "Code Review" });
    await screen.findByText("Link creation");
    expect(screen.getByRole("region", { name: "Document: QuickLink_Estimate.xlsx" })).toBeInTheDocument();
    expect(screen.getByText("Viewing document")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Back to Code Review" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("calls an agent's report by its stage, not a guess from its headings", async () => {
    getArtifactPreview.mockResolvedValue({ kind: "markdown", artifactId: "a1", filename: "Review.docx", status: "draft",
      markdown: "## Summary\nReady to merge.", derived: false });
    open({ title: "Review.docx", stage: "code_review" });
    expect(await screen.findByText("Code review document")).toBeInTheDocument();
    expect(screen.queryByText("Requirements document")).not.toBeInTheDocument();
  });
});

describe("DocumentPreview — a document", () => {
  it("renders its text as the report", async () => {
    getArtifactPreview.mockResolvedValue({ kind: "markdown", artifactId: "a1", filename: "BRD.docx", status: "draft",
      markdown: "## Scope\nShort internal links.", derived: false });
    open({ title: "BRD.docx" });
    expect(await screen.findByRole("article")).toBeInTheDocument();
    expect(screen.getAllByText("Short internal links.").length).toBeGreaterThan(0);
    expect(screen.queryByText("Read from the Word file")).not.toBeInTheDocument();
  });

  it("marks an outline read from a deck", async () => {
    getArtifactPreview.mockResolvedValue({ kind: "markdown", artifactId: "a1", filename: "Architecture.pptx", status: "draft",
      markdown: "## Slide 1 · Architecture\nThree services.", derived: true, derivedFrom: "slides" });
    open({ title: "Architecture.pptx", stage: "design" });
    expect(await screen.findByText("Read from the slides")).toBeInTheDocument();
  });

  it("marks text read back from the Word file as a preview", async () => {
    getArtifactPreview.mockResolvedValue({ kind: "markdown", artifactId: "a1", filename: "architecture.docx", status: "approved",
      markdown: "## 01 Overview\nBody.", derived: true });
    open({ title: "architecture.docx", status: "approved" });
    expect(await screen.findByText("Read from the Word file")).toBeInTheDocument();
  });
});

describe("DocumentPreview — an HTML report, a PDF, an image", () => {
  // jsdom has no object URLs; the browser's are what the viewer draws from.
  const blobApi = URL as unknown as { createObjectURL?: unknown; revokeObjectURL?: unknown };
  function stubBlobUrls(url: string) {
    blobApi.createObjectURL = vi.fn(() => url);
    blobApi.revokeObjectURL = vi.fn();
  }
  // Unmount first: the viewer revokes its URL as it goes.
  afterEach(() => { cleanup(); delete blobApi.createObjectURL; delete blobApi.revokeObjectURL; });

  /** The frame itself — the viewer's bar names the file too, as a tooltip on its heading. */
  function frameTitled(title: string) {
    return waitFor(() => {
      const frame = screen.queryAllByTitle(title).find((el) => el.tagName === "IFRAME");
      if (!frame) throw new Error(`no frame titled ${title}`);
      return frame;
    });
  }

  it("shows an HTML report in a frame that can run nothing and load nothing", async () => {
    getArtifactPreview.mockResolvedValue({ kind: "html", artifactId: "a1", filename: "coverage.html", status: "draft",
      html: "<h1>Coverage 91%</h1>", truncated: false });
    open({ title: "coverage.html", stage: "testing" });
    const frame = await frameTitled("coverage.html");
    expect(frame).toHaveAttribute("sandbox", "");
    const doc = frame.getAttribute("srcdoc") ?? "";
    expect(doc).toContain("default-src 'none'");
    expect(doc).toContain("<h1>Coverage 91%</h1>");
  });

  it("draws a PDF from its own bytes, fetched same-origin", async () => {
    getArtifactPreview.mockResolvedValue({ kind: "file", artifactId: "a1", filename: "BRD.pdf", status: "draft",
      media: "pdf", contentType: "application/pdf" });
    const fetchMock = vi.fn().mockResolvedValue(new Response(new Blob(["%PDF-1.7"], { type: "application/pdf" })));
    vi.stubGlobal("fetch", fetchMock);
    stubBlobUrls("blob:pdf-1");
    open({ title: "BRD.pdf" });
    const frame = await frameTitled("BRD.pdf");
    expect(frame).toHaveAttribute("src", "blob:pdf-1");
    expect(fetchMock).toHaveBeenCalledWith("/api/artifacts/a1/preview/file", { credentials: "same-origin" });
  });

  it("shows an image in an <img>, which never runs what an SVG carries", async () => {
    getArtifactPreview.mockResolvedValue({ kind: "file", artifactId: "a1", filename: "c4.svg", status: "draft",
      media: "image", contentType: "image/svg+xml" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(new Blob(["<svg/>"], { type: "image/svg+xml" }))));
    stubBlobUrls("blob:img-1");
    open({ title: "c4.svg" });
    expect(await screen.findByRole("img", { name: "c4.svg" })).toHaveAttribute("src", "blob:img-1");
  });

  it("says why a PDF could not be drawn", async () => {
    getArtifactPreview.mockResolvedValue({ kind: "file", artifactId: "a1", filename: "BRD.pdf", status: "draft",
      media: "pdf", contentType: "application/pdf" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: "This document's file could not be found in storage, so it cannot be shown." }), { status: 404 })));
    open({ title: "BRD.pdf" });
    expect(await screen.findByText(/could not be found in storage/)).toBeInTheDocument();
  });
});

describe("DocumentPreview — when there is nothing to show", () => {
  it("says a kind with no view cannot be shown, in the backend's words", async () => {
    getArtifactPreview.mockRejectedValue(new ApiRequestError(404, { detail: "This file has no page view: open it with the download." }));
    open({ title: "Archive.zip", status: "approved" });
    expect(await screen.findByText("This file cannot be shown here")).toBeInTheDocument();
    expect(screen.getByText(/no page view/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Download/ })).toHaveAttribute("href", "/api/artifacts/a1/download");
  });

  it("explains a rejected document", async () => {
    getArtifactPreview.mockRejectedValue(new ApiRequestError(410, { detail: "rejected" }));
    open({ status: "rejected" });
    expect(await screen.findByText("This document was rejected and its file has been deleted.")).toBeInTheDocument();
  });
});

describe("columnLetter", () => {
  it("names columns the way Excel does", () => {
    expect([0, 1, 25, 26, 27, 51, 52, 701, 702].map(columnLetter)).toEqual(["A", "B", "Z", "AA", "AB", "AZ", "BA", "ZZ", "AAA"]);
  });
});
