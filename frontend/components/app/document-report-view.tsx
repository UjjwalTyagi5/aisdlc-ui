"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Download, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { LoadingState } from "@/components/ui/loading-state";
import { Callout } from "@/components/app/report-primitives";
import { MarkdownReport } from "@/components/app/markdown-report";
import type { GeneratedDoc } from "@/components/app/generated-documents";
import { ApiRequestError } from "@/lib/api/client";
import { getArtifactPage } from "@/lib/api/artifacts";
import { qk } from "@/lib/api/query-keys";
import type { ArtifactId } from "@/lib/schemas";

/**
 * A generated Word document, rendered in the centre panel.
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
 * A document with no page copy (an upload, or one generated before page copies were
 * kept) renders nothing here; the caller falls back to what it showed, and the panel
 * still offers the file.
 */

/** True when the document can be opened as a report: it has an artifact row to read. */
export function hasReportView(doc: Pick<GeneratedDoc, "documentId">): boolean {
  return !!doc.documentId;
}

export function DocumentReportView({ doc, project, status, actions, onClose }: {
  doc: GeneratedDoc;
  project?: string;
  status?: string;
  /** The page's own controls for this document (e.g. "Raise for approval"), shown first. */
  actions?: React.ReactNode;
  onClose?: () => void;
}) {
  const documentId = doc.documentId ?? null;
  const q = useQuery({
    queryKey: qk.artifacts.page(documentId ?? ""),
    queryFn: () => getArtifactPage(documentId as ArtifactId),
    enabled: !!documentId,
    // The copy is written once, with the document; a change of status is on the card.
    staleTime: 5 * 60_000,
    // A 404 is a real answer ("no page view"), not a flake — and the copy is stored
    // before the file is announced, so a miss stays a miss.
    retry: false,
  });
  const name = doc.name ?? "document";

  if (!documentId) return null;
  if (q.isLoading) return <div className="p-6"><LoadingState variant="card" /></div>;
  if (q.isError) {
    const missing = q.error instanceof ApiRequestError && (q.error.status === 404 || q.error.status === 410);
    return (
      <div className="mx-auto max-w-3xl p-6">
        <Callout tone={missing ? "warning" : "danger"} title={name}>
          {q.error instanceof ApiRequestError && q.error.status === 410
            ? "This document was rejected and its file has been deleted."
            : missing
              ? "This document has no page view: it was uploaded, or generated before page copies were kept. The Word file itself is unaffected."
              : q.error instanceof Error
                ? q.error.message
                : "The document could not be loaded."}
          {missing && doc.url && (
            <a href={doc.url} className="text-primary ml-2 underline underline-offset-2" download>Download Word</a>
          )}
        </Callout>
      </div>
    );
  }
  if (!q.data) return null;

  return (
    <MarkdownReport
      markdown={q.data.markdown}
      filename={q.data.filename || name}
      project={project}
      status={status}
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
          {onClose && (
            <Button size="sm" variant="ghost" className="h-8 gap-1 text-xs" onClick={onClose} aria-label="Close document">
              <X className="size-3.5" aria-hidden />
            </Button>
          )}
        </>
      }
    />
  );
}
