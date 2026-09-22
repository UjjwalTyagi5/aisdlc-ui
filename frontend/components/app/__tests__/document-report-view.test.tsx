// @vitest-environment jsdom
/**
 * The centre-panel document view reads the document's page copy by its artifact id,
 * through the app's own origin.
 *
 * It used to fetch the markdown straight from the backend's `/generated/` mount — which
 * the app's Content Security Policy refuses, so every generated BRD rendered as "Failed
 * to fetch" while its download worked — and only knew that URL from the chat message,
 * so after a reload nothing could be opened.
 */
import "@testing-library/jest-dom/vitest";

import * as React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const getArtifactPreview = vi.fn();
vi.mock("@/lib/api/artifacts", () => ({ getArtifactPreview: (...a: unknown[]) => getArtifactPreview(...a) }));

import { ApiRequestError } from "@/lib/api/client";
import { DocumentReportView, hasReportView } from "@/components/app/document-report-view";

afterEach(() => { cleanup(); getArtifactPreview.mockReset(); });

function renderView(doc: Parameters<typeof DocumentReportView>[0]["doc"]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><DocumentReportView doc={doc} project="last" status="Draft" /></QueryClientProvider>);
}

describe("DocumentReportView", () => {
  it("renders the page copy fetched by the document's id", async () => {
    getArtifactPreview.mockResolvedValue({ kind: "markdown", artifactId: "row-1", filename: "last_BRD.docx", status: "draft",
      markdown: "## Executive Summary\nQuickLink shortens internal links." });
    const { container } = renderView({ id: "artifact:run:last_BRD.docx", name: "last_BRD.docx", url: "http://localhost:8004/generated/x/last_BRD.docx", documentId: "row-1" });
    expect(await screen.findByRole("article")).toBeInTheDocument();
    expect(container.textContent).toContain("QuickLink shortens internal links.");
    expect(container.textContent).toContain("Executive Summary");
    expect(getArtifactPreview).toHaveBeenCalledWith("row-1");
    expect(screen.getByRole("link", { name: /Download Word/ })).toHaveAttribute("href", expect.stringContaining("last_BRD.docx"));
  });

  it("says when a document has no page copy, and still offers the file", async () => {
    getArtifactPreview.mockRejectedValue(new ApiRequestError(404, { detail: "This document has no page view." }));
    renderView({ id: "row-2", name: "old.docx", url: "/api/artifacts/row-2/download", documentId: "row-2" });
    expect(await screen.findByText(/no page view/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Download" })).toBeInTheDocument();
  });

  it("never fetches the backend origin directly — a document is openable only by its row", () => {
    expect(hasReportView({ documentId: "row-1" })).toBe(true);
    expect(hasReportView({ documentId: undefined })).toBe(false);
    const { container } = renderView({ id: "artifact:run:x.docx", name: "x.docx", url: "http://localhost:8004/generated/x.docx" });
    expect(container).toBeEmptyDOMElement();
    expect(getArtifactPreview).not.toHaveBeenCalled();
  });
});

describe("DocumentReportView — a document with no page copy", () => {
  function renderWithFallback(over: Record<string, unknown> = {}) {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={qc}>
        <DocumentReportView
          doc={{ id: "a1", name: "QuickLink_Project_Plan.xlsx", url: "/api/artifacts/a1/download", documentId: "a1" }}
          status="Approved"
          fallback={<div data-testid="file-card">QuickLink_Project_Plan.xlsx</div>}
          {...over}
        />
      </QueryClientProvider>,
    );
  }

  it("shows the caller's own view when there is nothing to render", async () => {
    // A spreadsheet or a deck: the backend has no preview to give, so the page keeps its card.
    getArtifactPreview.mockRejectedValue(new ApiRequestError(404, { detail: "This document has no page view." }));
    renderWithFallback();
    expect(await screen.findByTestId("file-card")).toBeInTheDocument();
  });

  it("still explains a rejected document rather than showing the card", async () => {
    getArtifactPreview.mockRejectedValue(new ApiRequestError(410, { detail: "This document was rejected and its file has been deleted." }));
    const { container } = renderWithFallback();
    expect(await screen.findByText(/rejected and its file has been deleted/)).toBeInTheDocument();
    expect(container.querySelector('[data-testid="file-card"]')).toBeNull();
  });

  it("says when the text was read back from the Word file", async () => {
    // LIVE: architecture.docx had no page copy, so the backend derives one from the file.
    getArtifactPreview.mockResolvedValue({
      kind: "markdown", artifactId: "a1", filename: "architecture.docx", status: "approved", derived: true,
      markdown: "## 01 Overview\nQuickLink replaces a withdrawn public shortener.",
    });
    const { container } = renderWithFallback();
    expect(await screen.findByRole("article")).toBeInTheDocument();
    expect(container.textContent).toContain("Read from the Word file");
    expect(container.textContent).toContain("QuickLink replaces a withdrawn public shortener.");
  });

  it("does not say so when the agent's own page copy is what rendered", async () => {
    getArtifactPreview.mockResolvedValue({
      kind: "markdown", artifactId: "a1", filename: "architecture.docx", status: "approved", derived: false,
      markdown: "## 01 Overview\nBody.",
    });
    const { container } = renderWithFallback();
    expect(await screen.findByRole("article")).toBeInTheDocument();
    expect(container.textContent).not.toContain("Read from the Word file");
  });
});

describe("DocumentReportView — a spreadsheet", () => {
  it("opens a workbook as its sheets, not as a missing page", async () => {
    // LIVE: the Project Manager's estimate opened as "no page view" and a download link.
    getArtifactPreview.mockResolvedValue({
      kind: "sheets", artifactId: "x1", filename: "QuickLink_Estimate.xlsx", status: "draft",
      sheets: [
        { name: "Estimate", rows: [["Task", "Days"], ["Link creation", "5"]], rows_total: 2, cols_total: 2, truncated: false },
        { name: "Notes", rows: [["Assumes one developer."]], rows_total: 1, cols_total: 1, truncated: false },
      ],
    });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><DocumentReportView doc={{ id: "x1", name: "QuickLink_Estimate.xlsx", url: "/api/artifacts/x1/download", documentId: "x1" }} /></QueryClientProvider>);
    expect(await screen.findByText("Link creation")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Estimate" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("columnheader", { name: "B" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Download/ })).toHaveAttribute("href", "/api/artifacts/x1/download");
  });
});
