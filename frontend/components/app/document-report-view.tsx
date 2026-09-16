"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Download, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { LoadingState } from "@/components/ui/loading-state";
import { Callout } from "@/components/app/report-primitives";
import { MarkdownReport } from "@/components/app/markdown-report";
import type { GeneratedDoc } from "@/components/app/generated-documents";

/**
 * A generated Word document, rendered in the centre panel.
 *
 * THE WORD FILE IS NOT WHAT IS RENDERED. The agent writes the document's markdown
 * beside its `.docx` (`<name>.md`, see `requirements_document.write_markdown_sibling`)
 * under the same `/generated/` static mount the download link uses, so the page can
 * show the same document the file holds — as a report, not a download link.
 *
 * Only a `.docx` at a `/generated/` URL has a sibling. Anything else (an approved
 * artifact served through `/api/artifacts/…/download`, a spreadsheet) has no
 * report view and this renders nothing, so the page falls back to what it showed.
 */

export function markdownSiblingUrl(doc: Pick<GeneratedDoc, "url" | "name">): string | null {
  const url = doc.url ?? "";
  if (!/\.docx$/i.test(url) || !url.includes("/generated/")) return null;
  return url.replace(/\.docx$/i, ".md");
}

export function hasReportView(doc: Pick<GeneratedDoc, "url" | "name">): boolean {
  return markdownSiblingUrl(doc) !== null;
}

async function fetchMarkdown(url: string): Promise<string | null> {
  const res = await fetch(url, { cache: "no-store" });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`Couldn't read the document (${res.status}).`);
  return res.text();
}

export function DocumentReportView({ doc, project, status, onClose }: {
  doc: GeneratedDoc;
  project?: string;
  status?: string;
  onClose?: () => void;
}) {
  const mdUrl = markdownSiblingUrl(doc);
  const q = useQuery({
    queryKey: ["generated-document", "markdown", mdUrl],
    queryFn: () => fetchMarkdown(mdUrl!),
    enabled: !!mdUrl,
    staleTime: Infinity,
    // The .md is written a beat before the file_generated event; one retry covers it.
    retry: 2,
  });
  const name = doc.name ?? "document";

  if (!mdUrl) return null;
  if (q.isLoading) return <div className="p-6"><LoadingState variant="card" /></div>;
  if (q.isError || q.data === null || q.data === undefined) {
    return (
      <div className="mx-auto max-w-3xl p-6">
        <Callout tone="neutral" title={name}>
          This document has no report view — open the Word file instead.
          {doc.url && (
            <a href={doc.url} className="text-primary ml-2 underline underline-offset-2" download>Download</a>
          )}
        </Callout>
      </div>
    );
  }

  return (
    <MarkdownReport
      markdown={q.data}
      filename={name}
      project={project}
      status={status}
      generatedAt={new Date().toISOString()}
      actions={
        <>
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
