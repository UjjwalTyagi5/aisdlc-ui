"use client";

import * as React from "react";
import { AlertTriangle, GitCommitHorizontal, PackageX, ShieldAlert } from "lucide-react";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type {
  AssessedModule,
  DiscoveryAssessment,
  MigrationTier,
  RuntimeStatus,
} from "@/lib/schemas/modernization";

/**
 * The Dependency and Risk assessment, rendered for the person who has to accept it as the
 * planning baseline (Track 3).
 *
 * NOT a document viewer. The assessment is data — modules with scores and tiers, a
 * dependency graph, flags — and a reviewer's questions are data questions: which
 * modules are manual-only, what depends on this one, which runtimes are out of
 * support. So it is a sortable module list with a detail pane, a dependency view and
 * a flags view, the way the Development page shows code rather than a report about
 * code. Every number here comes from the backend's deterministic analysis; nothing is
 * recomputed in the browser.
 */

export const TIER_META: Record<MigrationTier, { label: string; variant: "success" | "info" | "danger"; hint: string }> = {
  mechanical: {
    label: "Mechanical",
    variant: "success",
    hint: "Codemod / upgrade tooling can do most of the work (same-language upgrade).",
  },
  llm_assisted: {
    label: "LLM-assisted",
    variant: "info",
    hint: "A rewrite with the legacy source and equivalence criteria in context.",
  },
  manual: {
    label: "Manual-only",
    variant: "danger",
    hint: "A platform feature with no target equivalent, or too risky to automate.",
  },
};

const RUNTIME_META: Record<RuntimeStatus, { label: string; variant: "danger" | "warning" | "success" | "outline" }> = {
  eol: { label: "End of life", variant: "danger" },
  approaching: { label: "EOL soon", variant: "warning" },
  legacy: { label: "Legacy", variant: "warning" },
  supported: { label: "Supported", variant: "success" },
  unknown: { label: "Unknown", variant: "outline" },
};

export function TierBadge({ tier }: { tier: MigrationTier }) {
  const meta = TIER_META[tier];
  return (
    <Badge variant={meta.variant} title={meta.hint} className="whitespace-nowrap">
      {meta.label}
    </Badge>
  );
}

function RuntimeBadge({ status }: { status: RuntimeStatus }) {
  const meta = RUNTIME_META[status];
  return <Badge variant={meta.variant} className="whitespace-nowrap font-normal">{meta.label}</Badge>;
}

function ScoreBar({ score }: { score: number }) {
  const tone = score >= 70 ? "bg-destructive" : score >= 35 ? "bg-warning" : "bg-success";
  return (
    <div className="flex items-center gap-2" aria-label={`Risk score ${score} of 100`}>
      <div className="bg-muted h-1.5 w-20 overflow-hidden rounded-full">
        <div className={cn("h-full rounded-full", tone)} style={{ width: `${Math.max(3, score)}%` }} />
      </div>
      <span className="font-mono text-xs tabular-nums">{score}</span>
    </div>
  );
}

export function AssessmentView({ assessment }: { assessment: DiscoveryAssessment }) {
  const [tier, setTier] = React.useState<MigrationTier | "all">("all");
  const [selected, setSelected] = React.useState<string | null>(null);
  const modules = assessment.modules;
  const visible = tier === "all" ? modules : modules.filter((m) => m.risk.tier === tier);
  const active = modules.find((m) => m.name === selected) ?? null;
  const { summary, repository } = assessment;

  return (
    <div className="space-y-6">
      {/* Summary strip */}
      <section aria-label="Assessment summary" className="space-y-3">
        <div className="text-muted-foreground flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
          <span className="text-foreground font-medium">{repository.name || repository.url || "Legacy repository"}</span>
          {repository.branch && <span className="font-mono">@ {repository.branch}</span>}
          {repository.commit && (
            <span className="inline-flex items-center gap-1 font-mono">
              <GitCommitHorizontal className="size-3" aria-hidden />
              {repository.commit.slice(0, 10)}
            </span>
          )}
          {assessment.target_stack && <span>Target: <span className="text-foreground">{assessment.target_stack}</span></span>}
          <span>Assessed {assessment.as_of}</span>
        </div>
        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
          <Stat label="Modules" value={summary.module_count} />
          <Stat label="Lines of code" value={summary.loc.toLocaleString()} />
          <Stat label="Mechanical" value={summary.tier_counts.mechanical} />
          <Stat label="LLM-assisted" value={summary.tier_counts.llm_assisted} />
          <Stat label="Manual-only" value={summary.tier_counts.manual} emphasis={summary.tier_counts.manual > 0} />
          <Stat label="End-of-life" value={summary.flag_counts.eol} emphasis={summary.flag_counts.eol > 0} />
          <Stat label="Vulnerable" value={summary.flag_counts.vulnerable} emphasis={summary.flag_counts.vulnerable > 0} />
        </dl>
        {summary.vendored_files > 0 && (
          <p className="text-muted-foreground text-xs">
            Lines of code leave out {summary.vendored_files.toLocaleString()} vendored front-end library
            file{summary.vendored_files === 1 ? "" : "s"} (jQuery, Bootstrap, minified bundles).
          </p>
        )}
        {assessment.scanners.trivy !== "ok" && (
          <p className="text-muted-foreground text-xs">
            Vulnerability scanner: {assessment.scanners.trivy}
            {assessment.scanners.note ? ` — ${assessment.scanners.note}` : ""}. Known-vulnerability
            counts may be incomplete.
          </p>
        )}
      </section>

      <Tabs defaultValue="modules">
        <TabsList>
          <TabsTrigger value="modules">Modules</TabsTrigger>
          <TabsTrigger value="dependencies">Dependencies</TabsTrigger>
          <TabsTrigger value="flags">
            Flags ({summary.flag_counts.eol + summary.flag_counts.deprecated + summary.flag_counts.vulnerable})
          </TabsTrigger>
        </TabsList>

        <TabsContent value="modules" className="mt-4 space-y-3">
          <div role="group" aria-label="Filter by migration tier" className="flex flex-wrap gap-2">
            {(["all", "manual", "llm_assisted", "mechanical"] as const).map((t) => (
              <button
                key={t}
                type="button"
                aria-pressed={tier === t}
                onClick={() => setTier(t)}
                className={cn(
                  "rounded-md border px-2.5 py-1 text-xs transition-colors",
                  tier === t ? "bg-primary/10 border-primary text-foreground" : "text-muted-foreground hover:bg-muted",
                )}
              >
                {t === "all" ? `All (${modules.length})` : `${TIER_META[t].label} (${summary.tier_counts[t]})`}
              </button>
            ))}
          </div>
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
            <div className="overflow-x-auto rounded-lg border">
              <table className="w-full text-sm">
                <thead className="bg-muted/40 text-muted-foreground text-left text-xs">
                  <tr>
                    <th className="px-3 py-2 font-medium">Module</th>
                    <th className="px-3 py-2 font-medium">Runtime</th>
                    <th className="px-3 py-2 text-right font-medium">LOC</th>
                    <th className="px-3 py-2 font-medium">Risk</th>
                    <th className="px-3 py-2 font-medium">Tier</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((m) => (
                    <tr
                      key={m.name}
                      onClick={() => setSelected(m.name === selected ? null : m.name)}
                      className={cn("hover:bg-muted/40 cursor-pointer border-t", m.name === selected && "bg-primary/5")}
                      aria-selected={m.name === selected}
                    >
                      <td className="px-3 py-2">
                        <p className="font-medium">{m.name}</p>
                        <p className="text-muted-foreground font-mono text-[11px]">{m.path}</p>
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex flex-col items-start gap-1">
                          <span className="text-xs">{runtimeText(m)}</span>
                          <RuntimeBadge status={m.runtime.status} />
                        </div>
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-xs tabular-nums">{m.loc.toLocaleString()}</td>
                      <td className="px-3 py-2"><ScoreBar score={m.risk.score} /></td>
                      <td className="px-3 py-2"><TierBadge tier={m.risk.tier} /></td>
                    </tr>
                  ))}
                  {visible.length === 0 && (
                    <tr>
                      <td colSpan={5} className="text-muted-foreground px-3 py-6 text-center text-sm">
                        No modules in this tier.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            <ModuleDetail module={active} />
          </div>
        </TabsContent>

        <TabsContent value="dependencies" className="mt-4">
          <DependencyList modules={modules} />
        </TabsContent>

        <TabsContent value="flags" className="mt-4">
          <FlagsPanel flags={assessment.flags} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function runtimeText(m: AssessedModule): string {
  return [m.runtime.name, m.runtime.version].filter(Boolean).join(" ") || "Not declared";
}

function Stat({ label, value, emphasis }: { label: string; value: React.ReactNode; emphasis?: boolean }) {
  return (
    <div className="rounded-lg border px-3 py-2">
      <dt className="text-muted-foreground text-[11px]">{label}</dt>
      <dd className={cn("font-display text-lg font-semibold tabular-nums", emphasis && "text-destructive")}>{value}</dd>
    </div>
  );
}

function ModuleDetail({ module }: { module: AssessedModule | null }) {
  if (!module) {
    return (
      <aside className="text-muted-foreground rounded-lg border border-dashed p-4 text-sm">
        Select a module to see why it scored what it did, what it depends on, and its dependencies.
      </aside>
    );
  }
  const packages = module.dependencies.filter((d) => d.kind === "package");
  return (
    <aside aria-label={`${module.name} detail`} className="space-y-4 rounded-lg border p-4">
      <header className="space-y-1">
        <div className="flex items-center justify-between gap-2">
          <h3 className="font-semibold">{module.name}</h3>
          <TierBadge tier={module.risk.tier} />
        </div>
        <p className="text-muted-foreground text-xs">{TIER_META[module.risk.tier].hint}</p>
      </header>
      <section className="space-y-1.5">
        <h4 className="text-muted-foreground text-xs font-semibold tracking-wider uppercase">
          Risk {module.risk.score}/100 — why
        </h4>
        <ul className="space-y-1.5 text-sm">
          {[...module.risk.factors].sort((a, b) => b.points - a.points).map((f) => (
            <li key={f.factor} className="flex gap-2">
              <span className="font-mono text-xs tabular-nums text-muted-foreground w-8 shrink-0 pt-0.5">+{f.points}</span>
              <span><span className="font-medium">{f.factor.replace(/_/g, " ")}</span> — {f.detail}</span>
            </li>
          ))}
        </ul>
      </section>
      <section className="space-y-1 text-sm">
        <h4 className="text-muted-foreground text-xs font-semibold tracking-wider uppercase">Coupling</h4>
        <p>Depends on: {module.depends_on.length ? module.depends_on.join(", ") : "no other module"}</p>
        <p>Used by: {module.dependents.length ? module.dependents.join(", ") : "no other module"}</p>
        <p>Tests: {module.has_tests ? "yes" : "none found"}</p>
        {module.vendored_files > 0 && (
          <p className="text-muted-foreground text-xs">
            {module.vendored_files} vendored front-end file{module.vendored_files === 1 ? "" : "s"} (jQuery,
            Bootstrap, bundles) — counted as files, not as lines of code.
          </p>
        )}
      </section>
      <section className="space-y-1.5">
        <h4 className="text-muted-foreground text-xs font-semibold tracking-wider uppercase">
          Packages ({packages.length})
        </h4>
        <ul className="max-h-56 space-y-1 overflow-auto text-xs">
          {packages.map((d) => (
            <li key={`${d.name}@${d.version}`} className="flex items-start justify-between gap-2">
              <span className="font-mono">{d.name} <span className="text-muted-foreground">{d.version}</span></span>
              {d.status !== "ok" && (
                <Badge variant={d.status === "vulnerable" ? "danger" : "warning"} className="font-normal">
                  {d.status}
                </Badge>
              )}
            </li>
          ))}
          {packages.length === 0 && <li className="text-muted-foreground">No external packages declared.</li>}
        </ul>
      </section>
    </aside>
  );
}

function DependencyList({ modules }: { modules: readonly AssessedModule[] }) {
  return (
    <ul className="space-y-3">
      {modules.map((m) => {
        const packages = m.dependencies.filter((d) => d.kind === "package");
        const flagged = packages.filter((d) => d.status !== "ok");
        return (
          <li key={m.name} className="rounded-lg border p-3">
            <p className="font-medium">{m.name}</p>
            <p className="text-muted-foreground mt-1 text-sm">
              → {m.depends_on.length ? m.depends_on.join(", ") : "no other module"} · {packages.length} external
              package{packages.length === 1 ? "" : "s"}
              {flagged.length > 0 && `, ${flagged.length} flagged`}
            </p>
            {flagged.length > 0 && (
              <ul className="mt-2 flex flex-wrap gap-1.5">
                {flagged.map((d) => (
                  <li key={d.name}>
                    <Badge variant={d.status === "vulnerable" ? "danger" : "warning"} className="font-mono font-normal">
                      {d.name} {d.version}
                    </Badge>
                  </li>
                ))}
              </ul>
            )}
          </li>
        );
      })}
    </ul>
  );
}

export function FlagsPanel({ flags }: { flags: DiscoveryAssessment["flags"] }) {
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <FlagGroup icon={AlertTriangle} title="End-of-life runtimes" empty="None found.">
        {flags.eol.map((f) => (
          <li key={`${f.module}-${f.runtime}`}>
            <span className="font-medium">{f.module}</span> — {f.runtime}{" "}
            <span className="text-muted-foreground">
              ({f.status === "eol" ? "ended" : "ends"}{f.eol_date ? ` ${f.eol_date}` : ""})
            </span>
          </li>
        ))}
      </FlagGroup>
      <FlagGroup icon={PackageX} title="Deprecated packages" empty="None found.">
        {flags.deprecated.map((f) => (
          <li key={`${f.module}-${f.package}`}>
            <span className="font-medium">{f.module}</span> — <code className="text-xs">{f.package}</code> {f.version}
            <p className="text-muted-foreground text-xs">{f.reason}</p>
          </li>
        ))}
      </FlagGroup>
      <FlagGroup icon={ShieldAlert} title="Known vulnerabilities" empty="None reported.">
        {flags.vulnerable.map((f, i) => (
          <li key={`${f.module}-${f.cve}-${i}`}>
            <span className="font-medium">{f.module}</span> — <code className="text-xs">{f.package}</code> {f.version}
            <p className="text-muted-foreground text-xs">
              {f.cve} ({f.severity}){f.fixed_version ? `, fixed in ${f.fixed_version}` : ""}
            </p>
          </li>
        ))}
      </FlagGroup>
    </div>
  );
}

function FlagGroup({ icon: Icon, title, empty, children }: {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  empty: string;
  children: React.ReactNode[];
}) {
  return (
    <section className="rounded-lg border p-3">
      <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold">
        <Icon className="text-muted-foreground size-4" aria-hidden />
        {title} <span className="text-muted-foreground font-normal">({children.length})</span>
      </h3>
      {children.length ? <ul className="space-y-2 text-sm">{children}</ul> : (
        <p className="text-muted-foreground text-sm">{empty}</p>
      )}
    </section>
  );
}
