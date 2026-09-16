"use client";

import * as React from "react";
import {
  Boxes,
  Bug,
  Check,
  Download,
  FileSearch,
  KeyRound,
  ListChecks,
  ShieldAlert,
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
import type {
  CodeReviewArtifact,
  PrepareResult,
  SbomComponent,
  SecurityScan,
} from "@/lib/schemas/code-review";

/**
 * The Code Review agent's review, as a report — and its Security, SBOM and Files tabs.
 *
 * TWO SOURCES, KEPT APART. The verdict, summary and findings are the reviewer's; the
 * security results and SBOM are the scanners' (backend code_security_scan.py). A
 * scanner that did not run is "Blocked" and its area "not established" — never shown as
 * a clean result — and a vulnerability in a package nobody declared names the direct
 * dependency that brings it in, which is the part a developer can act on.
 *
 * The same document is written as Word (backend review_document.py); the band's
 * download link opens it.
 */

export type ReviewTab = "summary" | "findings" | "security" | "sbom" | "files" | "diff" | "documents";

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

/** No scanners recorded = the review predates the security review, or it did not run. */
export function scanRan(security: SecurityScan | undefined): security is SecurityScan {
  return !!security && security.scanners.length > 0;
}

function scannerOk(security: SecurityScan | undefined, name: string): boolean {
  return !!security?.scanners.some((s) => s.name === name && s.status === "ok");
}

function versionKey(v: string): (number | string)[] {
  return v.split(/[.+-]/).map((p) => (/^\d+$/.test(p) ? Number(p) : p));
}

function compareVersions(a: string, b: string): number {
  const x = versionKey(a);
  const y = versionKey(b);
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    const p = x[i] ?? 0;
    const q = y[i] ?? 0;
    if (p === q) continue;
    if (typeof p === "number" && typeof q === "number") return p - q;
    return String(p).localeCompare(String(q));
  }
  return 0;
}

/** The version that fixes every listed vulnerability — mirrors review_document._upgrade_to. */
export function upgradeTo(fixes: string[]): string {
  const perCve: string[] = [];
  for (const fixed of fixes) {
    const options = (fixed || "").split(",").map((o) => o.trim()).filter(Boolean);
    if (options.length === 0) return "";
    perCve.push(options.sort(compareVersions).at(-1)!);
  }
  return perCve.sort(compareVersions).at(-1) ?? "";
}

export function groupVulnerabilities(security: SecurityScan) {
  const via = new Map(security.sbom.components.map((c) => [`${c.name}@${c.version}`, c.via]));
  const groups = new Map<string, SecurityScan["vulnerabilities"]>();
  for (const v of security.vulnerabilities) {
    const key = `${v.package}@${v.installed}`;
    groups.set(key, [...(groups.get(key) ?? []), v]);
  }
  return [...groups.entries()]
    .map(([key, items]) => {
      const worst = items.reduce((w, i) => ((SEV_RANK[i.severity] ?? 9) < (SEV_RANK[w] ?? 9) ? i.severity : w), items[0]!.severity);
      const counts = new Map<string, number>();
      for (const i of items) counts.set(i.severity, (counts.get(i.severity) ?? 0) + 1);
      return {
        package: items[0]!.package,
        installed: items[0]!.installed,
        worst,
        count: items.length,
        breakdown: [...counts.entries()].sort((a, b) => (SEV_RANK[a[0]] ?? 9) - (SEV_RANK[b[0]] ?? 9)).map(([s, n]) => `${n} ${s}`).join(", "),
        upgrade: upgradeTo(items.map((i) => i.fixed)) || "no fix released",
        via: via.get(key) || "direct dependency",
      };
    })
    .sort((a, b) => (SEV_RANK[a.worst] ?? 9) - (SEV_RANK[b.worst] ?? 9) || a.package.localeCompare(b.package));
}

function shortTitle(t: string, pkg: string): string {
  let out = t || "";
  const re = new RegExp(`^\\s*(?:node-)?${pkg.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}:\\s*`, "i");
  for (let i = 0; i < 3; i++) out = out.replace(re, "");
  return out.trim();
}

/* ── Summary: the report ────────────────────────────────────────────────────── */

export function CodeReviewReport({ artifact, onOpenTab }: {
  artifact: CodeReviewArtifact;
  onOpenTab: (tab: ReviewTab) => void;
}) {
  const ctx = artifact.context;
  const verdict = VERDICT[artifact.merge_recommendation] ?? VERDICT.needs_discussion!;
  const security = artifact.security;
  const ran = scanRan(security);
  const totals = security?.totals ?? {};
  const scope = artifact.scope ?? {};
  const findings = artifact.findings;
  const critHigh = findings.filter((f) => f.severity === "critical" || f.severity === "high").length;
  const direct = security?.sbom.components.filter((c) => c.direct).length ?? 0;
  const isBranch = ctx.mode === "repo";
  const readPct = isBranch && scope.reviewable_files ? (100 * (scope.reviewable_files_read ?? 0)) / scope.reviewable_files : null;
  const blocked = ran ? security.scanners.filter((s) => s.status !== "ok") : [];

  const facts: Fact[] = [
    { label: "Findings", icon: ListChecks, value: findings.length, hint: `${critHigh} critical/high`, tone: critHigh ? "danger" : "success" },
    ran && scannerOk(security, "Trivy")
      ? { label: "Vulnerabilities", icon: Bug, value: totals.vulnerabilities ?? 0, hint: `${totals.vulnerabilities_high ?? 0} high or critical`, tone: (totals.vulnerabilities_high ?? 0) > 0 ? "danger" : (totals.vulnerabilities ?? 0) > 0 ? "warning" : "success" }
      : { label: "Vulnerabilities", icon: Bug, value: "—", hint: "not scanned", tone: "warning" },
    ran && scannerOk(security, "Gitleaks")
      ? { label: "Secrets", icon: KeyRound, value: totals.secrets ?? 0, hint: "hardcoded", tone: (totals.secrets ?? 0) > 0 ? "danger" : "success" }
      : { label: "Secrets", icon: KeyRound, value: "—", hint: "not scanned", tone: "warning" },
    ran && scannerOk(security, "Semgrep")
      ? { label: "Static analysis", icon: ShieldAlert, value: totals.sast ?? 0, hint: "OWASP Top 10", tone: (totals.sast ?? 0) > 0 ? "warning" : "success" }
      : { label: "Static analysis", icon: ShieldAlert, value: "—", hint: "not run", tone: "warning" },
    { label: "SBOM", icon: Boxes, value: ran ? (totals.components ?? 0) : "—", hint: ran ? `${direct} direct` : "not built" },
    isBranch
      ? { label: "Scope", icon: FileSearch, value: `${scope.reviewable_files_read ?? 0} / ${scope.reviewable_files ?? 0}`, hint: "files read", tone: readPct !== null && readPct < 60 ? "warning" : "default" }
      : { label: "Scope", icon: FileSearch, value: scope.files_changed ?? artifact.metrics.files_changed, hint: "files changed" },
  ];

  return (
    <article className="mx-auto max-w-5xl space-y-8 p-4 md:p-6">
      <header className="space-y-3">
        <ReportHero
          eyebrow="Code review & security report"
          eyebrowTail={`${ctx.repo_name} · ${targetLine(ctx)}`}
          title={`${ctx.repo_name || "Repository"} — ${verdict.label}`}
          subtitle={verdict.sentence}
          meta={[ctx.head_sha ? `Commit ${ctx.head_sha.slice(0, 7)}` : "", artifact.created_at ? `Reviewed ${reviewedAt(artifact.created_at)}` : ""].filter(Boolean).join(" · ") || undefined}
          aside={
            <>
              <Pill tone={verdict.tone} className="px-2.5 py-1 text-xs">{verdict.label}</Pill>
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

      <ReportSection
        n={2}
        title="Security review"
        id="cr-security"
        aside={ran ? <button type="button" className="text-primary hover:underline" onClick={() => onOpenTab("security")}>Full results</button> : undefined}
      >
        {!ran ? (
          <Callout tone="warning" title="Not established">
            The security review did not run for this review, so nothing about the code&apos;s security is established.
          </Callout>
        ) : (
          <>
            {artifact.security_summary && <MarkdownBody markdown={artifact.security_summary} />}
            <ScannersTable security={security} />
            {blocked.length > 0 && (
              <Callout tone="warning" title="Not established">
                {blocked.map((s) => s.name).join(", ")} did not run, so the areas {blocked.length === 1 ? "it checks are" : "they check are"} unknown — not clean.
              </Callout>
            )}
          </>
        )}
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
          <Callout>The project had no requirements or approved design the reviewer could check this code against.</Callout>
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
          <p className="text-muted-foreground text-xs">
            An AI reviewer read the code with the project&apos;s requirements and design. The security results and SBOM come from
            the scanners{ran ? ` (${security.scanners.map((s) => `${s.name}: ${s.status === "ok" ? "ran" : s.status}`).join("; ")})` : ""}, not from the reviewer.
          </p>
        </div>
      </ReportSection>
    </article>
  );
}

function ScannersTable({ security }: { security: SecurityScan }) {
  return (
    <ReportTable dense head={<><th className={th}>Scanner</th><th className={th}>What it checks</th><th className={cn(th, "w-24")}>Result</th><th className={cn(th, "w-20 text-right")}>Findings</th><th className={th}>Note</th></>}>
      {security.scanners.map((s) => {
        const ok = s.status === "ok";
        const result = ok ? (s.findings ? "Failed" : "Passed") : "Blocked";
        return (
          <tr key={s.name}>
            <td className={cn(td, "font-medium")}>{s.name}</td>
            <td className={cn(td, "text-muted-foreground")}>{s.purpose}</td>
            <td className={td}><Pill tone={result === "Passed" ? "success" : result === "Failed" ? "danger" : "warning"}>{result}</Pill></td>
            <td className={cn(td, "text-right tabular-nums")}>{ok ? s.findings ?? 0 : "not run"}</td>
            <td className={cn(td, "text-muted-foreground text-xs")}>{s.message || (ok && s.seconds != null ? `Ran in ${s.seconds} s` : s.status)}</td>
          </tr>
        );
      })}
    </ReportTable>
  );
}

/* ── Security tab ───────────────────────────────────────────────────────────── */

export function SecurityView({ artifact }: { artifact: CodeReviewArtifact | null }) {
  const security = artifact?.security;
  if (!artifact || !scanRan(security)) {
    return (
      <div className="mx-auto max-w-xl px-4 py-12">
        <EmptyState
          icon={ShieldCheck}
          title={artifact ? "No security review on this review" : "No security review yet"}
          description={artifact
            ? "This review was saved before the security review existed, or it did not run. Run a new review to scan the branch."
            : "Run the review: the security review scans the whole checked-out branch for secrets, OWASP Top 10 issues and vulnerable dependencies."}
          variant="plain"
        />
      </div>
    );
  }
  const groups = groupVulnerabilities(security);
  const vulns = [...security.vulnerabilities].sort((a, b) => (SEV_RANK[a.severity] ?? 9) - (SEV_RANK[b.severity] ?? 9));
  return (
    <div className="mx-auto max-w-5xl space-y-8 p-4 md:p-6">
      <ReportSection n={1} title="Scanners" id="sec-scanners">
        <ScannersTable security={security} />
      </ReportSection>

      <ReportSection n={2} title="Vulnerable dependencies" id="sec-vulns" aside={vulns.length ? `${vulns.length} vulnerabilities` : undefined}>
        {!scannerOk(security, "Trivy") ? (
          <Callout tone="warning" title="Not established">Dependencies were not checked — the vulnerability scanner did not run.</Callout>
        ) : groups.length === 0 ? (
          <Callout tone="success">No known vulnerabilities were found in the dependencies.</Callout>
        ) : (
          <div className="space-y-4">
            <ReportTable dense head={<><th className={th}>Package</th><th className={th}>Installed</th><th className={cn(th, "w-24")}>Severity</th><th className={th}>Vulnerabilities</th><th className={th}>Upgrade to</th><th className={th}>Brought in by</th></>}>
              {groups.map((g) => (
                <tr key={`${g.package}@${g.installed}`}>
                  <td className={cn(td, "font-medium")}>{g.package}</td>
                  <td className={cn(td, "font-mono text-xs")}>{g.installed}</td>
                  <td className={td}><Pill tone={severityTone(g.worst)}>{title(g.worst)}</Pill></td>
                  <td className={td}>{g.count} <span className="text-muted-foreground text-xs">({g.breakdown})</span></td>
                  <td className={cn(td, "font-mono text-xs")}>{g.upgrade}</td>
                  <td className={td}>{g.via}</td>
                </tr>
              ))}
            </ReportTable>
            <ReportTable dense head={<><th className={th}>Vulnerability</th><th className={cn(th, "w-24")}>Severity</th><th className={th}>Package</th><th className={th}>Issue</th><th className={th}>Fixed in</th></>}>
              {vulns.map((v, i) => (
                <tr key={`${v.id}-${i}`}>
                  <td className={cn(td, "font-mono text-[11px] font-semibold whitespace-nowrap")}>{v.id}</td>
                  <td className={td}><Pill tone={severityTone(v.severity)}>{title(v.severity)}</Pill></td>
                  <td className={cn(td, "whitespace-nowrap")}>{v.package} <span className="text-muted-foreground font-mono text-xs">{v.installed}</span></td>
                  <td className={td}>{shortTitle(v.title, v.package) || "—"}</td>
                  <td className={cn(td, "font-mono text-xs")}>{v.fixed || "—"}</td>
                </tr>
              ))}
            </ReportTable>
          </div>
        )}
      </ReportSection>

      <ReportSection n={3} title="Hardcoded secrets" id="sec-secrets">
        {!scannerOk(security, "Gitleaks") ? (
          <Callout tone="warning" title="Not established">Secrets were not scanned — the scanner did not run.</Callout>
        ) : security.secrets.length === 0 ? (
          <Callout tone="success">No hardcoded secrets were detected.</Callout>
        ) : (
          <>
            <ReportTable dense head={<><th className={th}>Rule</th><th className={th}>Location</th><th className={th}>Description</th></>}>
              {security.secrets.map((s, i) => (
                <tr key={i} className="shadow-[inset_3px_0_0_0_var(--destructive)]">
                  <td className={cn(td, "font-mono text-xs")}>{s.rule}</td>
                  <td className={cn(td, "font-mono text-xs")}>{s.file}{s.line ? `:${s.line}` : ""}</td>
                  <td className={td}>{s.description}</td>
                </tr>
              ))}
            </ReportTable>
            <Callout tone="danger">Secret values are redacted. Rotate every exposed credential, then remove it from the code and its history.</Callout>
          </>
        )}
      </ReportSection>

      <ReportSection n={4} title="Static analysis" id="sec-sast">
        {!scannerOk(security, "Semgrep") ? (
          <Callout tone="warning" title="Not established">Static analysis did not run.</Callout>
        ) : security.sast.length === 0 ? (
          <Callout tone="success">Static analysis found no OWASP Top 10 issues.</Callout>
        ) : (
          <ReportTable dense head={<><th className={cn(th, "w-24")}>Severity</th><th className={th}>Rule</th><th className={th}>Location</th><th className={th}>Message</th></>}>
            {[...security.sast].sort((a, b) => (SEV_RANK[a.severity] ?? 9) - (SEV_RANK[b.severity] ?? 9)).map((f, i) => (
              <tr key={i}>
                <td className={td}><Pill tone={severityTone(f.severity)}>{title(f.severity)}</Pill></td>
                <td className={cn(td, "font-mono text-xs")}>{f.rule.split(".").at(-1)}</td>
                <td className={cn(td, "font-mono text-xs break-all")}>{f.file}{f.line ? `:${f.line}` : ""}</td>
                <td className={td}>{f.message}</td>
              </tr>
            ))}
          </ReportTable>
        )}
      </ReportSection>
    </div>
  );
}

/* ── SBOM tab ───────────────────────────────────────────────────────────────── */

type SbomFilter = "all" | "direct" | "vulnerable";

export function SbomView({ artifact }: { artifact: CodeReviewArtifact | null }) {
  const [filter, setFilter] = React.useState<SbomFilter>("direct");
  const [query, setQuery] = React.useState("");
  const [limit, setLimit] = React.useState(200);
  const security = artifact?.security;
  const components: SbomComponent[] = React.useMemo(() => security?.sbom.components ?? [], [security]);
  const shown = React.useMemo(() => {
    const q = query.trim().toLowerCase();
    return components
      .filter((c) => (filter === "direct" ? c.direct : filter === "vulnerable" ? (c.vulnerabilities ?? 0) > 0 : true))
      .filter((c) => !q || c.name.toLowerCase().includes(q) || c.license.toLowerCase().includes(q) || c.via.toLowerCase().includes(q))
      .sort((a, b) => (b.vulnerabilities ?? 0) - (a.vulnerabilities ?? 0) || Number(b.direct) - Number(a.direct) || a.name.localeCompare(b.name));
  }, [components, filter, query]);

  if (!artifact || !scanRan(security)) {
    return (
      <div className="mx-auto max-w-xl px-4 py-12">
        <EmptyState icon={Boxes} title="No SBOM yet" description="The SBOM is built by the security review when the review runs." variant="plain" />
      </div>
    );
  }
  const direct = components.filter((c) => c.direct).length;
  const vulnerable = components.filter((c) => (c.vulnerabilities ?? 0) > 0).length;
  return (
    <div className="mx-auto max-w-6xl space-y-4 p-4 md:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm">
          <span className="font-display text-lg font-semibold tabular-nums">{components.length}</span> components across{" "}
          {security.sbom.manifests.length} manifest(s) · {direct} direct · {components.length - direct} transitive ·{" "}
          <span className={cn(vulnerable ? "text-red-700 dark:text-red-400" : "")}>{vulnerable} vulnerable</span>
        </p>
        <div className="flex items-center gap-2">
          {(["direct", "vulnerable", "all"] as const).map((f) => (
            <Button key={f} size="sm" variant={filter === f ? "default" : "outline"} className="h-8 text-xs" onClick={() => setFilter(f)} aria-pressed={filter === f}>
              {f === "direct" ? "Direct" : f === "vulnerable" ? "Vulnerable" : "All"}
            </Button>
          ))}
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter by name, licence…"
            aria-label="Filter components"
            className="border-line-soft bg-surface-1 h-8 w-48 rounded-md border px-2 text-xs"
          />
        </div>
      </div>
      {security.sbom.notes.map((n) => <Callout key={n} className="text-xs">{n}</Callout>)}
      {!scannerOk(security, "Trivy") && (
        <Callout tone="warning" title="Vulnerabilities not checked">The vulnerability scanner did not run, so no component&apos;s vulnerability status is known.</Callout>
      )}
      {shown.length === 0 ? (
        <Callout>No components match.</Callout>
      ) : (
        <ReportTable dense head={<><th className={th}>Package</th><th className={th}>Version</th><th className={th}>Declared</th><th className={th}>Licence</th><th className={th}>Scope</th><th className={th}>Brought in by</th><th className={cn(th, "text-right")}>Vulnerabilities</th></>}>
          {shown.slice(0, limit).map((c) => (
            <tr key={`${c.manifest}:${c.name}@${c.version}:${c.via}`}>
              <td className={cn(td, c.direct && "font-medium")}>{c.name}</td>
              <td className={cn(td, "font-mono text-xs")} title={c.version_source}>{c.version || "—"}</td>
              <td className={cn(td, "text-muted-foreground font-mono text-xs")}>{c.declared || "—"}</td>
              <td className={cn(td, "text-xs")}>{c.license || "—"}</td>
              <td className={cn(td, "text-xs")}>{c.scope}</td>
              <td className={cn(td, "text-xs")}>{c.direct ? <span className="text-muted-foreground">direct</span> : c.via || "—"}</td>
              <td className={cn(td, "text-right")}>
                {c.vulnerabilities == null ? <span className="text-muted-foreground text-xs">not checked</span>
                  : c.vulnerabilities > 0 ? <Pill tone="danger">{c.vulnerabilities}</Pill> : <span className="text-muted-foreground tabular-nums">0</span>}
              </td>
            </tr>
          ))}
        </ReportTable>
      )}
      {shown.length > limit && (
        <Button variant="outline" size="sm" onClick={() => setLimit((l) => l + 200)}>Show {Math.min(200, shown.length - limit)} more of {shown.length - limit}</Button>
      )}
    </div>
  );
}

/* ── Files tab (whole branch) ───────────────────────────────────────────────── */

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
