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

const getArtifactPage = vi.fn();
vi.mock("@/lib/api/artifacts", () => ({ getArtifactPage: (...a: unknown[]) => getArtifactPage(...a) }));

import { ApiRequestError } from "@/lib/api/client";
import { DocumentReportView, hasReportView } from "@/components/app/document-report-view";

afterEach(() => { cleanup(); getArtifactPage.mockReset(); });

function renderView(doc: Parameters<typeof DocumentReportView>[0]["doc"]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><DocumentReportView doc={doc} project="last" status="Draft" /></QueryClientProvider>);
}

describe("DocumentReportView", () => {
  it("renders the page copy fetched by the document's id", async () => {
    getArtifactPage.mockResolvedValue({ artifactId: "row-1", filename: "last_BRD.docx", status: "draft",
      markdown: "## Executive Summary\nQuickLink shortens internal links." });
    const { container } = renderView({ id: "artifact:run:last_BRD.docx", name: "last_BRD.docx", url: "http://localhost:8004/generated/x/last_BRD.docx", documentId: "row-1" });
    expect(await screen.findByRole("article")).toBeInTheDocument();
    expect(container.textContent).toContain("QuickLink shortens internal links.");
    expect(container.textContent).toContain("Executive Summary");
    expect(getArtifactPage).toHaveBeenCalledWith("row-1");
    expect(screen.getByRole("link", { name: /Download Word/ })).toHaveAttribute("href", expect.stringContaining("last_BRD.docx"));
  });

  it("says when a document has no page copy, and still offers the file", async () => {
    getArtifactPage.mockRejectedValue(new ApiRequestError(404, { detail: "This document has no page view." }));
    renderView({ id: "row-2", name: "old.docx", url: "/api/artifacts/row-2/download", documentId: "row-2" });
    expect(await screen.findByText(/no page view/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Download Word" })).toBeInTheDocument();
  });

  it("never fetches the backend origin directly — a document is openable only by its row", () => {
    expect(hasReportView({ documentId: "row-1" })).toBe(true);
    expect(hasReportView({ documentId: undefined })).toBe(false);
    const { container } = renderView({ id: "artifact:run:x.docx", name: "x.docx", url: "http://localhost:8004/generated/x.docx" });
    expect(container).toBeEmptyDOMElement();
    expect(getArtifactPage).not.toHaveBeenCalled();
  });
});
