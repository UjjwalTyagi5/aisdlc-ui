"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Download, FileImage, FileSpreadsheet, FileText, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { LoadingState } from "@/components/ui/loading-state";
import { Callout, Pill } from "@/components/app/report-primitives";
import { MarkdownReport, type DocumentKind } from "@/components/app/markdown-report";
import type { UseRaiseForApprovalResult } from "@/hooks/use-raise-for-approval";
import { ApiRequestError } from "@/lib/api/client";
import { artifactPreviewFileUrl, getArtifactPreview, type PreviewSheet } from "@/lib/api/artifacts";
import { qk } from "@/lib/api/query-keys";
import { approvalState } from "@/lib/documents/report-document";
import type { Artifact, ArtifactId } from "@/lib/schemas";
import { cn } from "@/lib/utils";

/**
 * ANY DOCUMENT AN AGENT FILED, OPENED IN THE PAGE'S CENTRE — the one viewer every agent page
 * uses, so a row in the Documents panel opens the same way on all of them.
 *
 * IT MUST NOT READ AS PART OF THE PAGE. It opens over the page's own centre (which stays
 * mounted underneath), and the first version looked exactly like it: a code review report
 * opened on the Code Review page was indistinguishable from the page's own Summary tab, and
 * the only way back was a small ×. So the viewer is its own surface: a bar across the top
 * that says "Viewing document" with a large "Back to <page>" button, and the document on a
 * framed sheet over a shaded background.
 *
 * The backend answers by kind (`GET /artifacts/{id}/preview`): a WORKBOOK or CSV as its
 * sheets, shown as a grid; a DOCUMENT as its page copy, or its text read back from the Word
 * file or the slides; an HTML report in a sandboxed frame; a PDF or an IMAGE drawn by the
 * browser. Anything else says it has no view. A draft can be raised for approval from the bar.
 */

/** What an agent page's documents are — the report's eyebrow, instead of a guess from the
 *  headings that named a code review report a "Requirements document". Requirements is left
 *  to the headings, which tell its BRD, PRD, PDD and risk register apart. */
const STAGE_KIND: Record<string, Pick<DocumentKind, "eyebrow" | "label">> = {
  design: { eyebrow: "Design document", label: "Design document" },
  plan: { eyebrow: "Project plan", label: "Project plan" },
  development: { eyebrow: "Development document", label: "Development document" },
  code_review: { eyebrow: "Code review document", label: "Code review document" },
  security: { eyebrow: "Security document", label: "Security document" },
  testing: { eyebrow: "Testing document", label: "Testing document" },
  deployment: { eyebrow: "Deployment document", label: "Deployment document" },
  documentation: { eyebrow: "Documentation", label: "Documentation" },
};

const SHEET_FILE = /\.(xlsx|xlsm|csv|tsv)$/i;
const IMAGE_FILE = /\.(png|jpe?g|gif|webp|svg)$/i;

export function DocumentPreview({ artifact, project, approvals, onClose, pageName, className }: {
  artifact: Artifact;
  /** The project's name, for the report band. */
  project?: string;
  /** When given, a draft offers "Raise for approval" in the bar. */
  approvals?: UseRaiseForApprovalResult;
  onClose: () => void;
  /** The page underneath, for the way back: "Back to Code Review". */
  pageName?: string;
  className?: string;
}) {
  const q = useQuery({
    queryKey: qk.artifacts.preview(artifact.id),
    queryFn: () => getArtifactPreview(artifact.id as ArtifactId),
    // A document's content does not change after it is filed; its STATUS does, and that
    // comes from the Documents list the caller passes in, not from this answer.
    staleTime: 5 * 60_000,
    // 404 and 410 are answers ("no view", "rejected"), not flakes.
    retry: false,
  });

  const state = approvalState(artifact);
  const statusWord = artifact.status === "approved" ? "Approved"
    : artifact.status === "draft" ? "Draft"
      : artifact.status === "rejected" ? "Rejected" : "Awaiting approval";
  const canRaise = !!approvals && artifact.status === "draft" && approvals.mayRaise(artifact.stage);
  const Icon = SHEET_FILE.test(artifact.title) ? FileSpreadsheet : IMAGE_FILE.test(artifact.title) ? FileImage : FileText;

  let body: React.ReactNode;
  if (q.isLoading) {
    body = <div className="p-6" aria-busy="true"><LoadingState variant="card" /></div>;
  } else if (q.isError) {
    const status = q.error instanceof ApiRequestError ? q.error.status : 0;
    body = (
      <div className="p-4 md:p-6">
        <Callout tone={status === 404 ? "warning" : "danger"}
          title={status === 410 ? "Rejected" : status === 404 ? "This file cannot be shown here" : "Could not open"}>
          {status === 410
            ? "This document was rejected and its file has been deleted."
            : q.error instanceof Error ? q.error.message : "The document could not be loaded."}
        </Callout>
      </div>
    );
  } else {
    const preview = q.data!;
    body = preview.kind === "sheets" ? (
      <div className="p-3 md:p-5"><SpreadsheetView sheets={preview.sheets} filename={preview.filename} /></div>
    ) : preview.kind === "html" ? (
      <HtmlView html={preview.html} truncated={preview.truncated} title={artifact.title} />
    ) : preview.kind === "file" ? (
      <FileEmbed artifactId={artifact.id} media={preview.media} title={artifact.title} />
    ) : (
      <MarkdownReport
        markdown={preview.markdown}
        filename={preview.filename || artifact.title}
        project={project}
        status={statusWord}
        generatedAt={artifact.createdAt}
        kind={STAGE_KIND[artifact.stage ?? ""]}
        facts={preview.derived
          ? [{ label: "View", value: preview.derivedFrom === "slides" ? "Read from the slides" : "Read from the Word file" }]
          : []}
      />
    );
  }

  return (
    <section aria-label={`Document: ${artifact.title}`} className={cn("bg-muted/60 flex flex-col", className)}>
      {/* THE VIEWER'S BAR — what makes this read as a document opened over the page, not a
          section of it: its own tinted band, a named way back, the file and its standing. */}
      <div className="bg-background/95 supports-[backdrop-filter]:bg-background/80 sticky top-0 z-20 border-b border-l-4 border-l-primary shadow-sm backdrop-blur">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-2.5 md:px-4">
          <Button variant="outline" size="sm" className="h-9 gap-1.5 px-3" onClick={onClose}>
            <ArrowLeft className="size-4" aria-hidden />
            {pageName ? `Back to ${pageName}` : "Close document"}
          </Button>
          <span aria-hidden className="bg-border hidden h-7 w-px sm:block" />
          <Icon className="text-primary size-5 shrink-0" aria-hidden />
          <div className="min-w-0 flex-1">
            <p className="text-muted-foreground text-[10.5px] font-semibold tracking-wide uppercase">Viewing document</p>
            <h2 className="truncate text-sm font-semibold" title={artifact.title}>{artifact.title}</h2>
          </div>
          <Pill tone={state.tone}>{state.label}</Pill>
          <div className="flex items-center gap-1.5">
            {canRaise && (
              <Button size="sm" className="h-9" disabled={approvals!.raisingId === artifact.id} onClick={() => approvals!.raise(artifact)}>
                {approvals!.raisingId === artifact.id && <Loader2 className="size-3.5 animate-spin" aria-hidden />}
                Raise for approval
              </Button>
            )}
            {artifact.downloadUrl && (
              <Button asChild size="sm" variant="outline" className="h-9 gap-1.5">
                <a href={artifact.downloadUrl} download><Download className="size-4" aria-hidden />Download</a>
              </Button>
            )}
          </div>
        </div>
      </div>

      {/* The document itself, on its own sheet. */}
      <div className="flex-1 p-3 md:p-6">
        <div className="bg-background mx-auto max-w-6xl overflow-hidden rounded-xl border shadow-sm">{body}</div>
      </div>
    </section>
  );
}

/* ── an HTML report ─────────────────────────────────────────────────────────── */

/** Laid over whatever the report itself asks for: nothing may load from anywhere, so a
 *  report cannot call out or track who opened it. Inline styles and embedded images stay. */
const FRAME_POLICY =
  `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:">`;

/**
 * An HTML report (the Testing agent's coverage report) in a frame that can run nothing:
 * `sandbox` with no permissions gives it no scripts, no forms, no navigation and an origin
 * of its own, so a report cannot reach the app it is shown in.
 */
export function HtmlView({ html, truncated, title }: { html: string; truncated?: boolean; title: string }) {
  return (
    <div className="space-y-2 p-3">
      <iframe
        title={title}
        sandbox=""
        referrerPolicy="no-referrer"
        srcDoc={FRAME_POLICY + html}
        className="h-[75vh] w-full rounded-lg border bg-white"
      />
      {truncated && (
        <p className="text-muted-foreground text-xs">Only the first part of this report is shown. Download the file for the rest.</p>
      )}
    </div>
  );
}

/* ── a PDF or an image ──────────────────────────────────────────────────────── */

async function fetchPreviewFile(artifactId: string): Promise<Blob> {
  const res = await fetch(artifactPreviewFileUrl(artifactId), { credentials: "same-origin" });
  if (!res.ok) {
    let body: unknown;
    try { body = await res.json(); } catch { body = undefined; }
    throw new ApiRequestError(res.status, body);
  }
  return res.blob();
}

/**
 * A PDF or an image, drawn by the browser from the file's own bytes. They are fetched
 * same-origin and shown from a local object URL — the app's policy allows frames and images
 * from `blob:` — and an image goes in an <img>, which never runs what an SVG may carry.
 */
export function FileEmbed({ artifactId, media, title }: { artifactId: string; media: "pdf" | "image"; title: string }) {
  const q = useQuery({
    queryKey: [...qk.artifacts.preview(artifactId), "file"],
    queryFn: () => fetchPreviewFile(artifactId),
    staleTime: 5 * 60_000,
    retry: false,
  });
  const [url, setUrl] = React.useState<string | null>(null);
  React.useEffect(() => {
    if (!q.data) return;
    const next = URL.createObjectURL(q.data);
    setUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [q.data]);

  if (q.isError) {
    return (
      <div className="p-4 md:p-6">
        <Callout tone="danger" title="Could not open">
          {q.error instanceof Error ? q.error.message : "The file could not be loaded."}
        </Callout>
      </div>
    );
  }
  if (!url) return <div className="p-6" aria-busy="true"><LoadingState variant="card" /></div>;
  return media === "pdf" ? (
    <iframe title={title} src={url} className="h-[80vh] w-full bg-white" />
  ) : (
    <div className="flex justify-center p-4">
      {/* eslint-disable-next-line @next/next/no-img-element -- a local object URL of the filed image; next/image cannot load blob: */}
      <img src={url} alt={title} className="max-h-[75vh] max-w-full rounded border bg-white object-contain" />
    </div>
  );
}

/* ── a workbook ─────────────────────────────────────────────────────────────── */

/** Excel's column names: A … Z, AA, AB … */
export function columnLetter(index: number): string {
  let n = index + 1;
  let out = "";
  while (n > 0) {
    const r = (n - 1) % 26;
    out = String.fromCharCode(65 + r) + out;
    n = Math.floor((n - 1) / 26);
  }
  return out;
}

/**
 * A workbook as a grid: one tab per sheet, column letters and row numbers the way Excel shows
 * them, sticky while scrolling, and a line saying so when a large sheet was cut.
 */
export function SpreadsheetView({ sheets, filename }: { sheets: PreviewSheet[]; filename?: string }) {
  const [active, setActive] = React.useState(0);
  React.useEffect(() => setActive(0), [filename]);
  const sheet = sheets[active] ?? sheets[0];

  if (!sheets.length || !sheet) {
    return <Callout tone="warning" title="No sheets">This workbook has no sheets to show.</Callout>;
  }
  const width = Math.max(0, ...sheet.rows.map((r) => r.length));

  return (
    <div className="space-y-2">
      {sheets.length > 1 && (
        <div role="tablist" aria-label="Sheets" className="flex flex-wrap gap-1">
          {sheets.map((s, i) => (
            <button
              key={`${s.name}-${i}`}
              type="button"
              role="tab"
              aria-selected={i === active}
              onClick={() => setActive(i)}
              className={cn(
                "rounded-md border px-2.5 py-1 text-xs font-medium transition-colors",
                i === active ? "border-brand-bright/50 bg-brand-bright/10 text-brand-bright" : "text-muted-foreground hover:bg-accent/50",
              )}
            >
              {s.name}
            </button>
          ))}
        </div>
      )}

      {sheet.rows.length === 0 ? (
        <Callout tone="neutral" title={sheet.name}>This sheet is empty.</Callout>
      ) : (
        <div className="max-h-[70vh] overflow-auto rounded-lg border">
          <table className="border-separate border-spacing-0 text-xs">
            <caption className="sr-only">{`Sheet ${sheet.name}`}</caption>
            <thead>
              <tr>
                <th scope="col" className="bg-muted text-muted-foreground sticky left-0 top-0 z-20 w-10 border-b border-r px-2 py-1 text-right font-normal">
                  <span className="sr-only">Row</span>
                </th>
                {Array.from({ length: width }, (_, c) => (
                  <th key={c} scope="col" className="bg-muted text-muted-foreground sticky top-0 z-10 border-b border-r px-2 py-1 text-center font-normal">
                    {columnLetter(c)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sheet.rows.map((row, r) => (
                <tr key={r} className={r === 0 ? "font-semibold" : undefined}>
                  <th scope="row" className="bg-muted text-muted-foreground sticky left-0 z-10 border-b border-r px-2 py-1 text-right font-normal tabular-nums">
                    {r + 1}
                  </th>
                  {row.map((cell, c) => (
                    <td key={c} className="bg-background max-w-[28rem] border-b border-r px-2 py-1 align-top whitespace-pre-wrap">
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {sheet.truncated && (
        <p className="text-muted-foreground text-xs">
          Showing the first {sheet.rows.length.toLocaleString()} of {sheet.rows_total.toLocaleString()} rows
          {sheet.cols_total > width ? ` and ${width} of ${sheet.cols_total} columns` : ""}. Download the file for the rest.
        </p>
      )}
    </div>
  );
}
