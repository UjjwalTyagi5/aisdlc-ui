"use client";

import * as React from "react";
import {
  Check,
  ClipboardCheck,
  Download,
  FileSearch,
  ListChecks,
  Loader2,
  ShieldCheck,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { MarkdownBody } from "@/components/app/markdown-report";
import {
  Callout,
  type Fact,
  FactStrip,
  MonoChip,
  PctBar,
  Pill,
  type PillTone,
  ReportHero,
  ReportSection,
  ReportTable,
  td,
  th,
} from "@/components/app/report-primitives";
import {
  approvalState,
  type FiledReport,
  type ReportApproval,
  reportDocumentFor as reportDocumentForStage,
} from "@/lib/documents/report-document";
import type { Artifact } from "@/lib/schemas";
import type {
  CodeReviewArtifact,
  PrepareResult,
} from "@/lib/schemas/code-review";

/**
 * The Code Review agent's review, as a report — and its Findings, Checklist and Files tabs.
 *
 * WHAT THIS REPORT IS, AND WHAT IT IS NOT. It is the review of a change: the verdict, the
 * findings, whether the code meets the APPROVED requirements and follows the APPROVED
 * design (PRD 21.4). It carried the scanners' output too — vulnerabilities, secrets, an
 * SBOM — so this page and the Security page showed the same SBOM and neither was the
 * answer. Scanning, the SBOM and the security sign-off are the Security agent's (PRD 21.5);
 * security appears here only as a reviewer's finding, with a file and a line.
 *
 * The same document is written as Word (backend review_document.py); the band's
 * download link opens it.
 */

export type ReviewTab = "summary" | "findings" | "checklist" | "files" | "diff" | "documents";

export { approvalState, type ReportApproval };

/** The Code Review report's document row — see lib/documents/report-document.ts. */
export function reportDocumentFor(
  review: FiledReport,
  documents: readonly Artifact[] | null | undefined,
): Artifact | null {
  return reportDocumentForStage(review, documents, "code_review");
}

const VERDICT: Record<string, { label: string; sentence: string; tone: PillTone }> = {
  approve: { label: "Approve", sentence: "No critical or high findings: the reviewed code is ready to merge.", tone: "success" },
  request_changes: { label: "Request changes", sentence: "Critical or high findings must be fixed before this code is merged.", tone: "danger" },
  needs_discussion: { label: "Needs discussion", sentence: "Material trade-offs need a team decision before this code is merged.", tone: "warning" },
};

const SEV_RANK: Record<string, number> = { critical: 0, high: 1, error: 1, medium: 2, warning: 2, low: 3, info: 4, unknown: 5 };

export function severityTone(severity: string): PillTone {
  const s = (severity || "").toLowerCase();
  if (s === "critical" || s === "high" || s === "error") return "danger";
  if (s === "medium" || s === "warning") return "warning";
  if (s === "low") return "info";
  return "neutral";
}

const title = (s: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

export function targetLine(ctx: CodeReviewArtifact["context"]): string {
  if (ctx.mode === "repo") return `Whole branch · ${ctx.source_branch}`;
  if (ctx.mode === "pr") return `PR #${ctx.pr_id} · ${ctx.source_branch} → ${ctx.base_branch}`;
  return `${ctx.source_branch} vs ${ctx.base_branch}`;
}

/** One fixed format, so the server's and the browser's renders agree. */
function reviewedAt(iso: string): string {
  return new Date(iso).toLocaleString("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** "the whole branch X", "pull request #35 (a → b)", "the changes on a since it left b". */
function targetPhrase(ctx: CodeReviewArtifact["context"]): string {
  if (ctx.mode === "repo") return `the whole branch ${ctx.source_branch}`;
  if (ctx.mode === "pr") return `pull request #${ctx.pr_id} (${ctx.source_branch} → ${ctx.base_branch})`;
  return `the changes on ${ctx.source_branch} since it left ${ctx.base_branch}`;
}

/* ── The standard review checklist ─────────────────────────────────────────── */

export interface ChecklistRow { check: string; result: string; detail: string }

const CHECK_TONE: Record<string, PillTone> = {
  Done: "success", "No issues": "success", "None found": "success", Partial: "warning", "Not run": "warning",
  "Not checked": "neutral", Gaps: "danger", Deviations: "warning", "Issues found": "danger",
};

/**
 * A CODE REVIEW REPORT, NOT A SECURITY REPORT. The standard checks of a review, answered
 * from the submitted review — the same rows as the Word report
 * (code_review_agent/review_document.review_checklist). Nothing established is "Done".
 */
export function reviewChecklist(artifact: CodeReviewArtifact): ChecklistRow[] {
  const findings = artifact.findings;
  const scope = artifact.scope ?? {};
  const rows: ChecklistRow[] = [];

  if (artifact.context.mode === "repo") {
    const read = scope.reviewable_files_read ?? 0;
    const total = scope.reviewable_files ?? 0;
    rows.push({ check: "Whole change read", result: total && read >= total ? "Done" : "Partial", detail: `${read} of ${total} reviewable files read` });
  } else {
    rows.push({ check: "Whole change read", result: "Done", detail: `${scope.files_changed ?? artifact.metrics.files_changed ?? 0} changed files reviewed` });
  }

  // SECURITY AS THE REVIEWER SEES IT. Two rows here were filled from a scanner pass this
  // agent ran — the same one the Security agent runs (PRD 21.5 owns the stack and the SBOM).
  const blocking = findings.filter((f) => f.category === "security" && (f.severity === "critical" || f.severity === "high"));
  rows.push({
    check: "Security issues in the code",
    result: blocking.length ? "Issues found" : "None found",
    detail: blocking.length
      ? `${blocking.length} critical/high security finding(s) in this review`
      : "no critical/high security finding in this review; the scan is the Security agent's",
  });

  const coverage = artifact.requirements_coverage;
  if (coverage.length === 0) rows.push({ check: "Meets the approved requirements", result: "Not checked", detail: "no requirements coverage recorded" });
  else {
    const gaps = coverage.filter((c) => ["violated", "unimplemented", "partial"].includes(c.status)).length;
    rows.push({ check: "Meets the approved requirements", result: gaps ? "Gaps" : "Done", detail: `${coverage.length - gaps} of ${coverage.length} acceptance criteria satisfied` });
  }

  const conformance = artifact.design_conformance;
  if (conformance.length === 0) rows.push({ check: "Follows the approved architecture", result: "Not checked", detail: "no design conformance recorded" });
  else {
    const off = conformance.filter((c) => c.status === "violates" || c.status === "drifts").length;
    rows.push({ check: "Follows the approved architecture", result: off ? "Deviations" : "Done", detail: `${conformance.length - off} of ${conformance.length} design rules conform` });
  }

  for (const [check, cats] of [
    ["Logic and correctness", ["logic_error"]],
    ["Performance", ["performance"]],
    ["Maintainability and style", ["maintainability", "style", "design", "other"]],
  ] as const) {
    const hits = findings.filter((f) => (cats as readonly string[]).includes(f.category));
    rows.push({ check, result: hits.length ? "Issues found" : "No issues", detail: hits.slice(0, 6).map((f) => `${f.id} (${f.severity})`).join(", ") || "none recorded" });
  }

  const verdict = VERDICT[artifact.merge_recommendation] ?? VERDICT.needs_discussion!;
  rows.push({ check: "Merge recommendation", result: verdict.label, detail: verdict.sentence });
  return rows;
}

/* ── Summary: the report ────────────────────────────────────────────────────── */

export function CodeReviewReport({ artifact, onOpenTab, approval }: {
  artifact: CodeReviewArtifact;
  onOpenTab: (tab: ReviewTab) => void;
  approval?: ReportApproval;
}) {
  const ctx = artifact.context;
  const verdict = VERDICT[artifact.merge_recommendation] ?? VERDICT.needs_discussion!;
  const scope = artifact.scope ?? {};
  const findings = artifact.findings;
  const critHigh = findings.filter((f) => f.severity === "critical" || f.severity === "high").length;
  const isBranch = ctx.mode === "repo";
  const readPct = isBranch && scope.reviewable_files ? (100 * (scope.reviewable_files_read ?? 0)) / scope.reviewable_files : null;
  const coverage = artifact.requirements_coverage ?? [];
  const met = coverage.filter((c) => c.status === "satisfied").length;
  const conformance = artifact.design_conformance ?? [];
  const conforms = conformance.filter((c) => c.status === "conforms").length;

  // THE REVIEW'S OWN NUMBERS. These were the scanners' — vulnerabilities, secrets, SBOM —
  // which is the Security agent's report (PRD 21.5). What a reviewer is asked for is
  // whether the change meets the approved spec and whether it should merge.
  const facts: Fact[] = [
    { label: "Findings", icon: ListChecks, value: findings.length, hint: `${critHigh} critical/high`, tone: critHigh ? "danger" : "success" },
    coverage.length
      ? { label: "Requirements", icon: ClipboardCheck, value: `${met} / ${coverage.length}`, hint: "acceptance criteria met", tone: met < coverage.length ? "warning" : "success" }
      : { label: "Requirements", icon: ClipboardCheck, value: "—", hint: "not checked", tone: "warning" },
    conformance.length
      ? { label: "Design", icon: ShieldCheck, value: `${conforms} / ${conformance.length}`, hint: "rules conform", tone: conforms < conformance.length ? "warning" : "success" }
      : { label: "Design", icon: ShieldCheck, value: "—", hint: "not checked", tone: "warning" },
    isBranch
      ? { label: "Scope", icon: FileSearch, value: `${scope.reviewable_files_read ?? 0} / ${scope.reviewable_files ?? 0}`, hint: "files read", tone: readPct !== null && readPct < 60 ? "warning" : "default" }
      : { label: "Scope", icon: FileSearch, value: scope.files_changed ?? artifact.metrics.files_changed, hint: "files changed" },
  ];

  return (
    <article className="mx-auto max-w-5xl space-y-8 p-4 md:p-6">
      <header className="space-y-3">
        <ReportHero
          eyebrow="Code review report"
          eyebrowTail={`${ctx.repo_name} · ${targetLine(ctx)}`}
          title={`${ctx.repo_name || "Repository"} — ${verdict.label}`}
          subtitle={verdict.sentence}
          meta={[ctx.head_sha ? `Commit ${ctx.head_sha.slice(0, 7)}` : "", artifact.created_at ? `Reviewed ${reviewedAt(artifact.created_at)}` : ""].filter(Boolean).join(" · ") || undefined}
          aside={
            <>
              <Pill tone={verdict.tone} className="px-2.5 py-1 text-xs">{verdict.label}</Pill>
              {/* THE REPORT'S APPROVAL, beside its verdict. Raising it used to change
                  nothing on the report itself — only a chip in the side panel, among
                  identically named reports — so it read as if nothing had happened. */}
              {approval?.document && (
                <Pill tone={approvalState(approval.document).tone} className="px-2.5 py-1 text-xs">
                  {approvalState(approval.document).label}
                </Pill>
              )}
              {approval?.document && approval.document.status === "draft" && approval.mayRaise && (
                <Button size="sm" className="h-8 gap-1.5 text-xs" disabled={approval.raising} onClick={approval.onRaise}>
                  {approval.raising && <Loader2 className="size-3.5 animate-spin" aria-hidden />}
                  Raise for approval
                </Button>
              )}
              {artifact.document.url ? (
                <Button asChild size="sm" variant="outline" className="h-8 gap-1.5 text-xs">
                  <a href={artifact.document.url} download>
                    <Download className="size-3.5" aria-hidden />
                    Download report
                  </a>
                </Button>
              ) : artifact.document.error ? (
                <Pill tone="danger" className="max-w-xs whitespace-normal">{artifact.document.error}</Pill>
              ) : null}
            </>
          }
        />
        <FactStrip facts={facts} />
      </header>

      <ReportSection n={1} title="Summary" id="cr-summary">
        {artifact.summary ? <MarkdownBody markdown={artifact.summary} /> : <Callout>The reviewer submitted no written summary.</Callout>}
      </ReportSection>

      <ReportSection n={2} title="Review checklist" id="cr-checklist">
        <ReportTable dense head={<><th className={th}>Check</th><th className={cn(th, "w-32")}>Result</th><th className={th}>Detail</th></>}>
          {reviewChecklist(artifact).map((r) => (
            <tr key={r.check}>
              <td className={cn(td, "font-medium")}>{r.check}</td>
              <td className={td}><Pill tone={r.check === "Merge recommendation" ? verdict.tone : CHECK_TONE[r.result] ?? "neutral"}>{r.result}</Pill></td>
              <td className={cn(td, "text-muted-foreground")}>{r.detail}</td>
            </tr>
          ))}
        </ReportTable>
      </ReportSection>

      <ReportSection
        n={3}
        title="Findings"
        id="cr-findings"
        aside={findings.length ? <button type="button" className="text-primary hover:underline" onClick={() => onOpenTab("findings")}>All findings</button> : undefined}
      >
        {findings.length === 0 ? (
          <Callout tone="success">No issues were found in the reviewed code.</Callout>
        ) : (
          <ReportTable dense head={<><th className={cn(th, "w-20")}>ID</th><th className={cn(th, "w-24")}>Severity</th><th className={th}>Finding</th><th className={cn(th, "w-56")}>Location</th></>}>
            {[...findings].sort((a, b) => (SEV_RANK[a.severity] ?? 9) - (SEV_RANK[b.severity] ?? 9)).map((f) => (
              <tr key={f.id}>
                <td className={cn(td, "font-mono text-[11px] font-semibold")}>{f.id}</td>
                <td className={td}><Pill tone={severityTone(f.severity)}>{title(f.severity)}</Pill></td>
                <td className={td}>
                  <p>{f.description}</p>
                  {f.recommendation && <p className="text-muted-foreground mt-0.5 text-xs"><span className="font-medium">Fix:</span> {f.recommendation}</p>}
                </td>
                <td className={cn(td, "font-mono text-[11px] break-all")}>{f.file ? `${f.file}${f.line ? `:${f.line}` : ""}` : "—"}</td>
              </tr>
            ))}
          </ReportTable>
        )}
      </ReportSection>

      <ReportSection n={4} title="Requirements and design" id="cr-requirements">
        {artifact.requirements_coverage.length === 0 && artifact.design_conformance.length === 0 ? (
          <Callout>No requirements coverage or design conformance was recorded for this review.</Callout>
        ) : (
          <div className="space-y-3">
            {artifact.requirements_coverage.length > 0 && (
              <ReportTable dense head={<><th className={th}>Acceptance criterion</th><th className={cn(th, "w-32")}>Status</th><th className={th}>Note</th></>}>
                {artifact.requirements_coverage.map((c, i) => (
                  <tr key={i}>
                    <td className={td}>{c.ac_id}</td>
                    <td className={td}><Pill tone={c.status === "satisfied" ? "success" : c.status === "partial" ? "warning" : "danger"}>{title(c.status)}</Pill></td>
                    <td className={cn(td, "text-muted-foreground")}>{c.note || "—"}</td>
                  </tr>
                ))}
              </ReportTable>
            )}
            {artifact.design_conformance.length > 0 && (
              <ReportTable dense head={<><th className={th}>Design rule</th><th className={cn(th, "w-32")}>Status</th><th className={th}>Note</th></>}>
                {artifact.design_conformance.map((c, i) => (
                  <tr key={i}>
                    <td className={td}>{c.rule}</td>
                    <td className={td}><Pill tone={c.status === "conforms" ? "success" : c.status === "unknown" ? "neutral" : c.status === "drifts" ? "warning" : "danger"}>{title(c.status)}</Pill></td>
                    <td className={cn(td, "text-muted-foreground")}>{c.note || "—"}</td>
                  </tr>
                ))}
              </ReportTable>
            )}
          </div>
        )}
      </ReportSection>

      <ReportSection n={5} title="Scope and method" id="cr-scope">
        <div className="space-y-3 text-sm">
          <p>
            Reviewed <span className="font-medium">{targetPhrase(ctx)}</span> in <span className="font-medium">{ctx.repo_name}</span>
            {ctx.head_sha && <> at commit <MonoChip>{ctx.head_sha.slice(0, 12)}</MonoChip></>}.
          </p>
          {isBranch && readPct !== null && (
            <div className="bg-card space-y-2 rounded-xl border px-4 py-3">
              <div className="flex items-baseline justify-between">
                <span className="font-medium">Files read</span>
                <span className="font-display tabular-nums">{scope.reviewable_files_read ?? 0} of {scope.reviewable_files ?? 0} reviewable files</span>
              </div>
              <PctBar pct={readPct} tone={readPct >= 90 ? "success" : readPct >= 60 ? "warning" : "danger"} />
              <p className="text-muted-foreground text-xs">
                {scope.lines_total ?? 0} lines of code · {scope.files_total ?? 0} files on the branch
                {scope.languages && Object.keys(scope.languages).length > 0 && ` · ${Object.entries(scope.languages).map(([k, v]) => `${k} (${v})`).join(", ")}`}
              </p>
              {(scope.not_read?.length ?? 0) > 0 && (
                <div className="flex flex-wrap items-center gap-1.5 pt-1">
                  <span className="text-muted-foreground text-xs">Not read:</span>
                  {scope.not_read!.slice(0, 30).map((p) => <MonoChip key={p}>{p}</MonoChip>)}
                  {scope.not_read!.length > 30 && <span className="text-muted-foreground text-xs">+{scope.not_read!.length - 30} more</span>}
                </div>
              )}
            </div>
          )}
          {(scope.documents?.length ?? 0) > 0 && (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-muted-foreground text-xs">Checked against:</span>
              {scope.documents!.map((d) => (
                <span key={d.title} className="inline-flex items-center gap-1">
                  <MonoChip>{d.title}</MonoChip>
                  {d.outcome !== "ok" && <Pill tone="warning">could not be read: {d.outcome}</Pill>}
                </span>
              ))}
            </div>
          )}
          <p className="text-muted-foreground text-xs">
            An AI reviewer read the code
            {(scope.documents?.length ?? 0) > 0
              ? " with the project's approved requirements and design documents"
              : scope.documents
                ? "; the project had no approved requirements or design document to check it against"
                : ""}
            . Dependency vulnerabilities, secrets, the SBOM and the security sign-off are the
            Security agent&apos;s report, not this one.
          </p>
        </div>
      </ReportSection>
    </article>
  );
}

export function BranchFilesView({ prepared, artifact }: { prepared: PrepareResult | null; artifact: CodeReviewArtifact | null }) {
  const read = new Set(artifact?.scope?.files_read ?? []);
  const files = prepared?.mode === "repo" ? prepared.files : null;
  if (files) {
    const reviewable = files.filter((f) => f.reviewable !== false);
    return (
      <div className="mx-auto max-w-5xl space-y-4 p-4 md:p-6">
        <p className="text-sm">
          <span className="font-display text-lg font-semibold tabular-nums">{files.length}</span> files on{" "}
          <MonoChip>{prepared!.source_branch}</MonoChip> · {reviewable.length} reviewable ·{" "}
          {reviewable.reduce((n, f) => n + f.added, 0)} lines of code
        </p>
        <ReportTable dense head={<><th className={th}>File</th><th className={th}>Language</th><th className={cn(th, "text-right")}>Lines</th><th className={cn(th, "w-28")}>Reviewable</th></>}>
          {files.map((f) => (
            <tr key={f.path} className={cn(f.reviewable === false && "text-muted-foreground")}>
              <td className={cn(td, "font-mono text-xs break-all")}>{f.path}</td>
              <td className={cn(td, "text-xs")}>{f.language ?? "—"}</td>
              <td className={cn(td, "text-right tabular-nums text-xs")}>{f.added}</td>
              <td className={td}>{f.reviewable === false ? <span className="text-xs">no</span> : <Pill tone="neutral">yes</Pill>}</td>
            </tr>
          ))}
        </ReportTable>
      </div>
    );
  }
  const scope = artifact?.scope;
  if (!artifact || scope?.mode !== "repo") {
    return (
      <div className="mx-auto max-w-xl px-4 py-12">
        <EmptyState icon={FileSearch} title="No branch files" description="Select a whole branch to review." variant="plain" />
      </div>
    );
  }
  return (
    <div className="mx-auto max-w-5xl space-y-4 p-4 md:p-6">
      <p className="text-sm">
        Read <span className="font-semibold tabular-nums">{scope.reviewable_files_read ?? 0}</span> of{" "}
        <span className="font-semibold tabular-nums">{scope.reviewable_files ?? 0}</span> reviewable files on{" "}
        <MonoChip>{artifact.context.source_branch}</MonoChip>
      </p>
      <ReportTable dense head={<><th className={th}>File</th><th className={cn(th, "w-28")}>Read</th></>}>
        {[...(scope.files_read ?? []).map((p) => ({ p, r: true })), ...(scope.not_read ?? []).map((p) => ({ p, r: false }))]
          .sort((a, b) => a.p.localeCompare(b.p))
          .map(({ p, r }) => (
            <tr key={p}>
              <td className={cn(td, "font-mono text-xs break-all")}>{p}</td>
              <td className={td}>{r ? <Pill tone="success"><Check className="size-3" aria-hidden />read</Pill> : <Pill tone="warning">not read</Pill>}</td>
            </tr>
          ))}
      </ReportTable>
      {read.size === 0 && <Callout tone="warning">The reviewer did not read any file on this branch.</Callout>}
    </div>
  );
}
