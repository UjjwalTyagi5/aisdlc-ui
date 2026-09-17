/**
 * A saved agent result (a code review, a security scan) and the report document the
 * platform filed for it — shared by the pages that show the report's approval state.
 */
import type { Artifact } from "@/lib/schemas";
import type { PillTone } from "@/components/app/report-primitives";

/** The slice of a saved result these helpers read. */
export interface FiledReport {
  created_at?: string;
  document: { filename?: string; url?: string; error?: string; artifact_id?: string | null };
}

/**
 * The report's own document row in the project's Documents.
 *
 * BY ID when the result recorded it (`document.artifact_id`, since 16 Sep 2026). Every
 * report for one commit has the same file name, so for an older result the row is the one
 * with that name, in `stage`, saved closest to the result — the report is written seconds
 * before the result is saved — and none further than ten minutes away, rather than a guess.
 */
export function reportDocumentFor(
  report: FiledReport,
  documents: readonly Artifact[] | null | undefined,
  stage: string,
): Artifact | null {
  const rows = (documents ?? []).filter((d) => d.type !== "story");
  const id = report.document.artifact_id;
  if (id) return rows.find((d) => d.id === id) ?? null;
  const name = report.document.filename;
  const savedAt = report.created_at ? Date.parse(report.created_at) : NaN;
  if (!name || Number.isNaN(savedAt)) return null;
  let best: Artifact | null = null;
  let bestGap = 10 * 60 * 1000;
  for (const d of rows) {
    if (d.stage !== stage || d.title !== name) continue;
    const gap = Math.abs(Date.parse(d.createdAt) - savedAt);
    if (gap <= bestGap) {
      best = d;
      bestGap = gap;
    }
  }
  return best;
}

/** What the report says about its approval — the same four states the Documents panel shows. */
export function approvalState(doc: Artifact): { label: string; tone: PillTone } {
  if (doc.status === "draft") return { label: "Draft · not yet raised for approval", tone: "neutral" };
  if (doc.status === "approved") {
    return { label: doc.approvedBy ? `Approved by ${doc.approvedBy}` : "Approved", tone: "success" };
  }
  if (doc.status === "rejected") return { label: "Rejected", tone: "danger" };
  return { label: "Raised for approval · waiting on the approver", tone: "warning" };
}

/** The report's approval, as a page resolved it. */
export interface ReportApproval {
  document: Artifact | null;
  mayRaise: boolean;
  raising: boolean;
  onRaise: () => void;
}
