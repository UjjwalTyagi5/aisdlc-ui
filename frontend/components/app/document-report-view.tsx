"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Download, FileImage, FileSpreadsheet, FileText, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { LoadingState } from "@/components/ui/loading-state";
import { Callout } from "@/components/app/report-primitives";
import { MarkdownReport } from "@/components/app/markdown-report";
import { FileEmbed, HtmlView, SpreadsheetView } from "@/components/app/document-preview";
import type { GeneratedDoc } from "@/components/app/generated-documents";
import { ApiRequestError } from "@/lib/api/client";
import { getArtifactPreview } from "@/lib/api/artifacts";
import { qk } from "@/lib/api/query-keys";
import type { ArtifactId } from "@/lib/schemas";

/**
 * A generated document, rendered in the centre panel — a Word document as a report, a
 * workbook as its sheets.
 *
 * THE WORD FILE IS NOT WHAT IS RENDERED. The agent writes the document's markdown beside
 * its `.docx`; registration keeps that copy with the document in the artifact store, and
 * `GET /artifacts/{id}/page` serves it — through the app's own origin.
 *
 * WHY NOT THE `/generated/` URL ANY MORE. The first version fetched the markdown straight
 * from the backend's static mount by the URL in the chat message. The app's Content
 * Security Policy (`connect-src 'self'`) refuses a fetch to that origin, so every
 * document rendered as "Failed to fetch" while its download — a navigation, which
 * `connect-src` does not govern — worked. And the URL lived only in the chat: after a
 * reload the Documents panel had no way to open anything.
 *
 * ONE ENDPOINT FOR BOTH KINDS. It read `/page`, which only knows markdown, so a spreadsheet
 * an agent filed (the Project Manager's estimate, the Testing agent's workbooks) opened as
 * "no page view" and a download link. It reads `/preview` now, which answers a workbook with
 * its sheets — the same answer `DocumentPreview` renders on the other agent pages.
 *
 * It answers every other kind the same way `DocumentPreview` does — a deck as its slides, an
 * HTML report in a sandboxed frame, a PDF or an image drawn by the browser. A file with no
 * view at all renders the caller's fallback, or says so and offers the file.
 */

/** True when the document can be opened as a report: it has an artifact row to read. */
export function hasReportView(doc: Pick<GeneratedDoc, "documentId">): boolean {
  return !!doc.documentId;
}

export function DocumentReportView({ doc, project, status, actions, fallback, onClose }: {
  doc: GeneratedDoc;
  project?: string;
  status?: string;
  /** The page's own controls for this document (e.g. "Raise for approval"), shown first. */
  actions?: React.ReactNode;
  /** What to show instead when the document has no page view at all — a spreadsheet or a
   *  deck, say. Without one the reader is told, and offered the file. */
  fallback?: React.ReactNode;
  onClose?: () => void;
}) {
  const documentId = doc.documentId ?? null;
  const q = useQuery({
    queryKey: qk.artifacts.preview(documentId ?? ""),
    queryFn: () => getArtifactPreview(documentId as ArtifactId),
    enabled: !!documentId,
    // The copy is written once, with the document; a change of status is on the card.
    staleTime: 5 * 60_000,
    // A 404 is a real answer ("no page view"), not a flake — and the copy is stored
    // before the file is announced, so a miss stays a miss.
    retry: false,
  });
  const name = doc.name ?? "document";

  if (!documentId) return fallback ?? null;
  if (q.isLoading) return <div className="p-6"><LoadingState variant="card" /></div>;
  if (q.isError) {
    const missing = q.error instanceof ApiRequestError && (q.error.status === 404 || q.error.status === 410);
    // A caller with something of its own to show (the file card) shows it rather than a
    // message about a view the reader never asked for. A rejection is still explained.
    if (missing && fallback && !(q.error instanceof ApiRequestError && q.error.status === 410)) return <>{fallback}</>;
    return (
      <div className="mx-auto max-w-3xl p-6">
        <Callout tone={missing ? "warning" : "danger"} title={name}>
          {q.error instanceof ApiRequestError && q.error.status === 410
            ? "This document was rejected and its file has been deleted."
            : missing
              ? "This file has no page view. Download it to open it; the file itself is unaffected."
              : q.error instanceof Error
                ? q.error.message
                : "The document could not be loaded."}
          {missing && doc.url && (
            <a href={doc.url} className="text-primary ml-2 underline underline-offset-2" download>Download</a>
          )}
        </Callout>
      </div>
    );
  }
  if (!q.data) return null;

  // Labelled, at the size of the buttons beside it — a bare × was too small to find.
  const closeButton = onClose && (
    <Button size="sm" variant="outline" className="h-8 gap-1.5 text-xs" onClick={onClose} aria-label="Close document">
      <X className="size-3.5" aria-hidden />Close
    </Button>
  );

  if (q.data.kind !== "markdown") {
    const HeaderIcon = q.data.kind === "sheets" ? FileSpreadsheet
      : q.data.kind === "file" && q.data.media === "image" ? FileImage : FileText;
    return (
      <div className="mx-auto max-w-6xl space-y-4 p-4 md:p-6">
        <header className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b pb-3">
          <HeaderIcon className="text-brand-bright size-5 shrink-0" aria-hidden />
          <h2 className="min-w-0 flex-1 truncate text-base font-semibold" title={q.data.filename || name}>{q.data.filename || name}</h2>
          <div className="flex items-center gap-1.5">
            {actions}
            {doc.url && (
              <Button asChild size="sm" variant="outline" className="h-8 gap-1.5 text-xs">
                <a href={doc.url} download><Download className="size-3.5" aria-hidden />Download</a>
              </Button>
            )}
            {closeButton}
          </div>
        </header>
        {q.data.kind === "sheets" ? (
          <SpreadsheetView sheets={q.data.sheets} filename={q.data.filename} />
        ) : q.data.kind === "html" ? (
          <HtmlView html={q.data.html} truncated={q.data.truncated} title={q.data.filename || name} />
        ) : (
          <div className="overflow-hidden rounded-lg border">
            <FileEmbed artifactId={documentId} media={q.data.media} title={q.data.filename || name} />
          </div>
        )}
      </div>
    );
  }

  return (
    <MarkdownReport
      markdown={q.data.markdown}
      filename={q.data.filename || name}
      project={project}
      status={status}
      facts={q.data.derived
        ? [{ label: "View", value: q.data.derivedFrom === "slides" ? "Read from the slides" : "Read from the Word file" }]
        : []}
      generatedAt={new Date().toISOString()}
      actions={
        <>
          {actions}
          {doc.url && (
            <Button asChild size="sm" variant="outline" className="h-8 gap-1.5 text-xs">
              <a href={doc.url} download>
                <Download className="size-3.5" aria-hidden />Download Word
              </a>
            </Button>
          )}
          {closeButton}
        </>
      }
    />
  );
}
