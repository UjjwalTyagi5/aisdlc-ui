"use client";

/**
 * A stored file — what it is, and one way to get it.
 *
 * WHAT THIS REPLACED. A document artifact arrived as `{kind: "raw", markdown:
 * "[Download artifact](url)"}` and was rendered by `AdrViewer`. Three things were wrong
 * at once: the link duplicated the download icon the list row already shows, the card
 * was captioned "ADR · Architecture Decision Record" for a PDF, and the URL was the raw
 * Azure blob URL on an account with public access disabled — so following it never
 * returned the file.
 *
 * The download here goes through `/api/artifacts/{id}/download`, which resolves the id
 * against `Run.tenant_id` and streams the bytes. That is the only authorised path.
 */

import * as React from "react";
import { Download, FileSpreadsheet, FileText, Image as ImageIcon, Presentation } from "lucide-react";

import { cn } from "@/lib/utils";

/** Icon by what the file actually is, falling back to the extension. */
function iconFor(filename: string, contentType?: string | null) {
  const ext = filename.toLowerCase().split(".").pop() ?? "";
  const ct = (contentType ?? "").toLowerCase();
  if (ct.startsWith("image/") || ["png", "jpg", "jpeg", "svg", "gif", "webp"].includes(ext)) {
    return ImageIcon;
  }
  if (ct.includes("spreadsheet") || ["xlsx", "xls", "csv"].includes(ext)) return FileSpreadsheet;
  if (ct.includes("presentation") || ["pptx", "ppt"].includes(ext)) return Presentation;
  return FileText;
}

/** "2.4 MB" — decimal units, matching what an OS file listing shows. */
export function formatBytes(bytes: number | null | undefined): string | null {
  if (bytes === null || bytes === undefined || bytes < 0) return null;
  if (bytes < 1000) return `${bytes} B`;
  const units = ["kB", "MB", "GB", "TB"];
  let value = bytes / 1000;
  let unit = 0;
  while (value >= 1000 && unit < units.length - 1) {
    value /= 1000;
    unit += 1;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

/** "PDF", "Word", … — the label a person uses, not the MIME type. */
export function formatKind(filename: string, contentType?: string | null): string {
  const ext = filename.toLowerCase().split(".").pop() ?? "";
  const byExt: Record<string, string> = {
    pdf: "PDF",
    docx: "Word document",
    doc: "Word document",
    xlsx: "Excel workbook",
    xls: "Excel workbook",
    csv: "CSV",
    pptx: "PowerPoint",
    ppt: "PowerPoint",
    md: "Markdown",
    png: "PNG image",
    jpg: "JPEG image",
    jpeg: "JPEG image",
    svg: "SVG image",
    json: "JSON",
    txt: "Text",
  };
  return byExt[ext] ?? contentType ?? "File";
}

export interface DocumentCardProps {
  artifactId: string;
  filename: string;
  contentType?: string | null;
  sizeBytes?: number | null;
  /** False when there is nothing to fetch: the upload failed, OR it is still pending. */
  stored?: boolean;
  /** Waiting on a project admin. Distinct from `!stored`, which would read as a fault. */
  awaitingApproval?: boolean;
  /** Declined by an admin; the file was deleted and the row kept as the record. */
  rejected?: boolean;
  /** Provided only for someone who may decide. Omit and no controls render — the
   *  caller gates on permission, not this component. */
  onApprove?: () => void;
  onReject?: () => void;
  deciding?: boolean;
  className?: string;
}

export function DocumentCard({
  artifactId,
  filename,
  contentType,
  sizeBytes,
  stored = true,
  awaitingApproval = false,
  rejected = false,
  onApprove,
  onReject,
  deciding = false,
  className,
}: DocumentCardProps) {
  const Icon = iconFor(filename, contentType);
  const size = formatBytes(sizeBytes);
  const meta = [formatKind(filename, contentType), size].filter(Boolean).join(" · ");

  return (
    <div className={cn("rounded-lg border p-4", className)}>
      <div className="flex items-center gap-3">
        <span
          className="bg-muted/60 flex size-10 shrink-0 items-center justify-center rounded-lg"
          aria-hidden
        >
          <Icon className="text-muted-foreground size-5" />
        </span>

        <div className="flex min-w-0 flex-1 flex-col">
          <span className="truncate text-sm font-medium" title={filename}>
            {filename}
          </span>
          <span className="text-muted-foreground font-mono text-[11px]">{meta}</span>
        </div>

        {stored ? (
          <a
            href={`/api/artifacts/${encodeURIComponent(artifactId)}/download`}
            download={filename}
            className={cn(
              "inline-flex shrink-0 items-center gap-1.5 rounded-md border px-3 py-1.5",
              "text-xs font-medium transition-colors hover:bg-surface-1",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            )}
          >
            <Download className="size-3.5" aria-hidden />
            Download
          </a>
        ) : (
          // THREE REASONS THERE IS NOTHING TO DOWNLOAD, and they need different words.
          // "Not stored" on a pending artifact would read as a fault when the system is
          // working exactly as intended.
          //
          // Not a disabled button either: a control that looks clickable and is not
          // reads as a bug.
          <span className="text-muted-foreground shrink-0 text-xs">
            {awaitingApproval ? "Awaiting approval" : rejected ? "Rejected" : "Not stored"}
          </span>
        )}
      </div>

      {awaitingApproval && (
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
          <p className="text-muted-foreground text-xs">
            Submitted to the project&apos;s artifacts. A project admin decides whether it
            joins the shared record — the chat download link works either way.
          </p>
          {onApprove && onReject && (
            <div className="flex shrink-0 gap-2">
              <button
                type="button"
                onClick={onReject}
                disabled={deciding}
                className={cn(
                  "rounded-md border px-3 py-1.5 text-xs font-medium transition-colors",
                  "hover:bg-surface-1 disabled:pointer-events-none disabled:opacity-50",
                )}
              >
                Reject
              </button>
              <button
                type="button"
                onClick={onApprove}
                disabled={deciding}
                className={cn(
                  "bg-primary text-primary-foreground rounded-md px-3 py-1.5",
                  "text-xs font-medium transition-opacity hover:opacity-90",
                  "disabled:pointer-events-none disabled:opacity-50",
                )}
              >
                {deciding ? "Working\u2026" : "Approve"}
              </button>
            </div>
          )}
        </div>
      )}

      {rejected && (
        <p className="text-muted-foreground mt-3 text-xs">
          A project admin declined this document, so its file was deleted. The record is
          kept so the decision is visible.
        </p>
      )}

      {!stored && !awaitingApproval && !rejected && (
        <p className="text-muted-foreground mt-3 text-xs">
          This artifact was recorded but its file never reached storage, so there is
          nothing to download. An administrator needs to check the storage configuration.
        </p>
      )}
    </div>
  );
}
