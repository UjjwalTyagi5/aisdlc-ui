import * as React from "react";
import {
  ArrowRight,
  Boxes,
  CalendarClock,
  CalendarX2,
  CircleCheck,
  CircleDashed,
  CircleHelp,
  CircleMinus,
  CircleX,
  Clock,
  FileCheck2,
  FolderGit2,
  Gauge,
  Globe,
  Info,
  Landmark,
  Lightbulb,
  Lock,
  ShieldAlert,
  Snowflake,
  Sparkles,
  Target,
  TrendingUp,
  TriangleAlert,
  Users,
  Wallet,
  Scale,
} from "lucide-react";

import type {
  BriefLayer,
  BriefModuleChange,
  MigrationIntentBrief,
} from "@/lib/schemas/modernization";
import { cn } from "@/lib/utils";

import {
  CHANGE_LABEL,
  DRIVER_LABEL,
  EFFORT_LABEL,
  MILESTONE_LABEL,
  STATUS_LABEL,
  displayDrivers,
  displayLayers,
  keyFacts,
  parseDate,
  prettyDate,
  sortedMilestones,
  type KeyFact,
} from "./brief-display";

/* ── tones ─────────────────────────────────────────────────────────────────── */
/*
 * One colour, one meaning, so the brief reads at a glance:
 *   grey    today, and anything that is context
 *   orange  the plan: the target, the recommendation, and how much changes (PwC orange,
 *           deepening with the size of the change: upgrade → re-platform → rewrite)
 *   red     end of life and risks
 *   amber   legacy or support ending (a dot, never a fill)
 *   green   fine as it is: a part kept as is, a runtime still supported (a fill or a dot
 *           with dark text, never green text)
 * `text-primary` is 4.6:1 on white but 4.1:1 on its own tint, so small orange text on a
 * tint is orange-800/900 (6.4:1 and up).
 */

const STATUS_DOT: Record<string, string> = {
  eol: "bg-destructive", approaching: "bg-amber-500", legacy: "bg-amber-500", supported: "bg-emerald-500",
};

/** Change types from the least to the most that changes: the order of the change mix. */
const CHANGE_ORDER = ["keep", "upgrade", "replatform", "replace", "rewrite", "new", "retire"];

const CHANGE_TONE: Record<string, string> = {
  keep: "bg-emerald-500/15 text-foreground",
  upgrade: "bg-primary/12 text-orange-800 dark:text-orange-200",
  replatform: "bg-primary/25 text-orange-900 dark:text-orange-100",
  replace: "bg-primary/40 text-orange-950 dark:text-orange-50",
  rewrite: "bg-primary text-primary-foreground",
  new: "bg-orange-800 text-white dark:bg-orange-300 dark:text-orange-950",
  retire: "bg-muted text-muted-foreground",
};
const CHANGE_BAR: Record<string, string> = {
  keep: "bg-emerald-500/60", upgrade: "bg-primary/30", replatform: "bg-primary/60", replace: "bg-primary/80",
  rewrite: "bg-primary", new: "bg-orange-800 dark:bg-orange-300", retire: "bg-neutral-300 dark:bg-neutral-600",
};

const DRIVER_ICON: Record<string, React.ElementType> = {
  end_of_support: CalendarX2, security: ShieldAlert, cost: Landmark, skills: Users,
  compliance: FileCheck2, performance: Gauge, other: Info,
};

/** The deadline is the one date in orange; every other milestone is grey. */
const milestoneDot = (kind: string) => (kind === "deadline" ? "bg-primary" : "bg-neutral-400 dark:bg-neutral-500");
const milestoneInk = (kind: string) => (kind === "deadline" ? "text-primary" : "text-muted-foreground");

/* ── small parts ──────────────────────────────────────────────────────────── */

export function ChangeBadge({ type, className }: { type: string; className?: string }) {
  if (!CHANGE_LABEL[type]) return null;
  return (
    <span className={cn("inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-semibold", CHANGE_TONE[type], className)}>
      {CHANGE_LABEL[type]}
    </span>
  );
}

function EffortMeter({ effort }: { effort: string }) {
  const level = { low: 1, medium: 2, high: 3 }[effort] ?? 0;
  if (!level) return null;
  return (
    <span className="inline-flex items-center gap-1.5" title={`${EFFORT_LABEL[effort]} effort`}>
      <span aria-hidden className="flex items-end gap-0.5">
        {[1, 2, 3].map((i) => (
          <span key={i} className={cn("w-1.5 rounded-sm", i <= level ? "bg-primary" : "bg-muted", i === 1 ? "h-2" : i === 2 ? "h-3" : "h-4")} />
        ))}
      </span>
      <span className="text-muted-foreground text-[11px] font-medium">{EFFORT_LABEL[effort]} effort</span>
    </span>
  );
}

/** Today is grey; end of life adds a red edge and a red label, so it never competes
 *  with the orange target beside it. */
function TodayBox({ current, status, className }: { current: string; status: string; className?: string }) {
  const eol = status === "eol";
  return (
    <div className={cn("bg-muted rounded-lg px-3 py-2", eol && "shadow-[inset_3px_0_0_0_var(--destructive)]", className)}>
      <p className="text-sm font-medium leading-snug">{current || "—"}</p>
      {STATUS_DOT[status] && STATUS_LABEL[status] && (
        <p className={cn("mt-0.5 flex items-center gap-1.5 text-[11px] font-semibold",
          eol ? "text-red-700 dark:text-red-400" : "text-neutral-600 dark:text-neutral-400")}>
          <span aria-hidden className={cn("size-1.5 rounded-full", STATUS_DOT[status])} />
          {STATUS_LABEL[status]}
        </p>
      )}
    </div>
  );
}

function TargetBox({ target, className }: { target: string; className?: string }) {
  return (
    <div className={cn("bg-primary/10 ring-primary/15 rounded-lg px-3 py-2 text-sm font-semibold leading-snug ring-1 ring-inset", className)}>
      {target || "—"}
    </div>
  );
}

export function Section({ n, title, children, id }: { n: number; title: string; children: React.ReactNode; id: string }) {
  return (
    <section aria-labelledby={id} className="space-y-4">
      <div className="flex items-baseline gap-3 border-b pb-2">
        <span className="text-primary font-mono text-sm font-semibold tabular-nums">{String(n).padStart(2, "0")}</span>
        <h3 id={id} className="font-display text-lg font-semibold tracking-tight">{title}</h3>
      </div>
      {children}
    </section>
  );
}

/* ── the hero ─────────────────────────────────────────────────────────────── */

const FACT_ICON: Record<KeyFact["key"], React.ElementType> = {
  deadline: CalendarClock, budget: Wallet, scope: Boxes, eol: TriangleAlert, target: Sparkles,
};

export function BriefHero({ brief, recordedAt }: { brief: MigrationIntentBrief; recordedAt?: string | null }) {
  const facts = keyFacts(brief);
  return (
    <header className="space-y-3">
      {/* White into PwC orange: the brand is the light, not a dark band. */}
      <div className="from-card via-card to-primary/15 border-primary/15 relative overflow-hidden rounded-2xl border bg-gradient-to-r px-6 py-6 md:px-8 md:py-7">
        <div aria-hidden className="bg-primary/15 pointer-events-none absolute -top-24 -right-16 size-72 rounded-full blur-3xl" />
        <p className="relative font-mono text-[10.5px] tracking-[0.18em] uppercase">
          <span className="font-semibold text-orange-800 dark:text-orange-300">Migration-intent brief</span>
          <span className="text-neutral-600 dark:text-neutral-400"> · Track 3 · Code Modernization</span>
        </p>
        <h2 className="font-display relative mt-2 text-3xl font-semibold tracking-tight md:text-4xl">
          {brief.system_name || "Unnamed system"}
        </h2>
        {brief.goal && <p className="text-foreground/80 relative mt-3 max-w-3xl text-[15px] leading-relaxed">{brief.goal}</p>}
        {recordedAt && (
          <p className="relative mt-4 font-mono text-[11px] text-neutral-600 dark:text-neutral-400">
            Recorded {new Date(recordedAt).toLocaleString()}
          </p>
        )}
      </div>
      {facts.length > 0 && (
        <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
          {facts.map((f) => {
            const Icon = FACT_ICON[f.key];
            return (
              <div key={f.key} className="bg-card flex items-start gap-3 rounded-xl border px-3.5 py-3">
                <span className={cn("mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg",
                  f.key === "eol" ? "bg-destructive/10 text-destructive" : "bg-primary/10 text-primary")}>
                  <Icon className="size-4" aria-hidden />
                </span>
                <div className="min-w-0">
                  <dt className="text-muted-foreground text-[10.5px] font-semibold tracking-wide uppercase">{f.label}</dt>
                  <dd className={cn("font-display text-base font-semibold", f.key === "eol" && "text-red-700 dark:text-red-400")}>{f.value}</dd>
                </div>
              </div>
            );
          })}
        </dl>
      )}
    </header>
  );
}

/* ── 1. the change at a glance ────────────────────────────────────────────── */

export function ChangeAtAGlance({ brief }: { brief: MigrationIntentBrief }) {
  const layers = displayLayers(brief);
  if (!layers.length) return <p className="text-muted-foreground text-sm">Not recorded yet.</p>;
  return (
    <div className="overflow-x-auto rounded-xl border">
      <table className="w-full min-w-[680px] border-collapse text-left">
        <thead>
          <tr className="from-primary/5 to-primary/15 bg-gradient-to-r text-[10.5px] tracking-wide text-orange-900 uppercase dark:text-orange-200">
            <th className="px-4 py-2.5 font-semibold">Part of the system</th>
            <th className="px-2 py-2.5 font-semibold">Today</th>
            <th className="w-8 px-0 py-2.5" aria-label="becomes" />
            <th className="px-2 py-2.5 font-semibold">Target</th>
            <th className="px-4 py-2.5 font-semibold">Change</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {layers.map((layer: BriefLayer, i) => (
            <tr key={`${layer.layer}-${i}`} className="align-middle">
              <td className="px-4 py-2.5">
                <p className="text-sm font-semibold">{layer.layer}</p>
                {layer.modules.length > 0 && (
                  <p className="text-muted-foreground font-mono text-[11px]">{layer.modules.join(", ")}</p>
                )}
              </td>
              <td className="px-2 py-2"><TodayBox current={layer.current} status={layer.current_status} /></td>
              <td className="px-0 text-center"><ArrowRight className="text-muted-foreground mx-auto size-4" aria-hidden /></td>
              <td className="px-2 py-2"><TargetBox target={layer.target} /></td>
              <td className="px-4 py-2"><ChangeBadge type={layer.change_type} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ── 2. why ───────────────────────────────────────────────────────────────── */

export function WhyNow({ brief }: { brief: MigrationIntentBrief }) {
  const drivers = displayDrivers(brief);
  if (!drivers.length) return <p className="text-muted-foreground text-sm">None recorded.</p>;
  return (
    <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {drivers.map((d, i) => {
        // The icon tells the kinds apart; one orange keeps six cards from becoming six colours.
        const Icon = DRIVER_ICON[d.category] ?? Info;
        return (
          <li key={`${d.title}-${i}`} className="bg-card flex gap-3 rounded-xl border p-4">
            <span className="bg-primary/10 text-primary flex size-9 shrink-0 items-center justify-center rounded-lg">
              <Icon className="size-4.5" aria-hidden />
            </span>
            <div className="min-w-0">
              <p className="text-muted-foreground text-[10.5px] font-semibold tracking-wide uppercase">
                {DRIVER_LABEL[d.category] ?? "Other"}
              </p>
              <p className="mt-0.5 text-sm font-semibold leading-snug">{d.title || d.detail}</p>
              {d.title && d.detail && <p className="text-muted-foreground mt-1 text-[13px] leading-relaxed">{d.detail}</p>}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

/* ── 3. the recommendation ────────────────────────────────────────────────── */

export function RecommendedStack({ brief }: { brief: MigrationIntentBrief }) {
  const rec = brief.recommendation!;
  const byAgent = rec.recommended_by !== "user";
  return (
    <div className="space-y-4">
      <div className="border-primary/25 bg-primary/5 relative overflow-hidden rounded-xl border p-5">
        <span aria-hidden className="bg-primary absolute inset-y-0 left-0 w-1" />
        <p className="text-primary flex items-center gap-1.5 text-[11px] font-semibold tracking-wide uppercase">
          <Sparkles className="size-3.5" aria-hidden />
          {byAgent ? "Recommended by the Migration Intent agent" : "Set by the business"}
        </p>
        {rec.summary && <p className="mt-2 text-[15px] leading-relaxed">{rec.summary}</p>}
        {byAgent && <p className="text-muted-foreground mt-2 text-xs italic">Accepted when this brief is signed off.</p>}
      </div>
      {(rec.rationale.length > 0 || rec.alternatives.length > 0) && (
        <div className="grid gap-4 lg:grid-cols-[1.35fr_1fr]">
          {rec.rationale.length > 0 && (
            <div>
              <p className="mb-2 text-sm font-semibold">Why this stack</p>
              <ul className="space-y-2">
                {rec.rationale.map((r, i) => (
                  <li key={i} className="flex gap-2 text-[13.5px] leading-relaxed">
                    <CircleCheck className="text-primary mt-0.5 size-4 shrink-0" aria-hidden />
                    <span>{r}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {rec.alternatives.length > 0 && (
            <div>
              <p className="mb-2 text-sm font-semibold">Alternatives considered</p>
              <ul className="space-y-2">
                {rec.alternatives.map((a, i) => (
                  <li key={i} className="bg-muted/40 flex gap-2 rounded-lg border px-3 py-2">
                    <CircleX className="text-muted-foreground mt-0.5 size-4 shrink-0" aria-hidden />
                    <div>
                      <p className="text-[13px] font-semibold">{a.option}</p>
                      {a.why_not && <p className="text-muted-foreground text-[12.5px] leading-relaxed">{a.why_not}</p>}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ── 4. scope ─────────────────────────────────────────────────────────────── */

export function ScopeView({ brief }: { brief: MigrationIntentBrief }) {
  const inScope = brief.in_scope.filter((s) => s.trim());
  const outScope = brief.out_of_scope.filter((s) => s.trim());
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <div className="overflow-hidden rounded-xl border">
        <p className="bg-primary/10 px-4 py-2 text-[11px] font-semibold tracking-wide text-orange-900 uppercase dark:text-orange-200">
          In scope · {inScope.length}
        </p>
        <ul className="space-y-1.5 p-4">
          {inScope.length ? inScope.map((s, i) => (
            <li key={i} className="flex gap-2 text-[13.5px]">
              <CircleCheck className="text-primary mt-0.5 size-4 shrink-0" aria-hidden />
              <span>{s}</span>
            </li>
          )) : <li className="text-muted-foreground text-sm">None recorded.</li>}
        </ul>
      </div>
      <div className="overflow-hidden rounded-xl border">
        <p className="bg-muted text-muted-foreground px-4 py-2 text-[11px] font-semibold tracking-wide uppercase">
          Out of scope · {outScope.length}
        </p>
        <ul className="space-y-1.5 p-4">
          {outScope.length ? outScope.map((s, i) => (
            <li key={i} className="text-muted-foreground flex gap-2 text-[13.5px]">
              <CircleMinus className="mt-0.5 size-4 shrink-0" aria-hidden />
              <span>{s}</span>
            </li>
          )) : <li className="text-muted-foreground text-sm">Nothing excluded.</li>}
        </ul>
      </div>
    </div>
  );
}

/* ── 5. module changes ────────────────────────────────────────────────────── */

function ChangeMix({ items }: { items: BriefModuleChange[] }) {
  const counts = new Map<string, number>();
  items.forEach((m) => m.change_type && counts.set(m.change_type, (counts.get(m.change_type) ?? 0) + 1));
  const total = [...counts.values()].reduce((a, b) => a + b, 0);
  if (!total) return null;
  // Lightest to strongest orange, so the bar reads as how much of the system changes.
  const rank = (t: string) => (CHANGE_ORDER.indexOf(t) + 1 || CHANGE_ORDER.length + 1);
  const entries = [...counts.entries()].sort(([a], [b]) => rank(a) - rank(b));
  return (
    <div className="space-y-2" aria-label="Change mix">
      {/* A 2px gap between segments keeps neighbouring steps of the ramp apart. */}
      <div aria-hidden className="flex h-2.5 gap-0.5 overflow-hidden rounded-full">
        {entries.map(([type, n]) => (
          <span key={type} className={CHANGE_BAR[type] ?? "bg-neutral-300"} style={{ width: `${(n / total) * 100}%` }} />
        ))}
      </div>
      <p className="text-muted-foreground flex flex-wrap gap-x-4 gap-y-1 text-xs">
        {entries.map(([type, n]) => (
          <span key={type} className="inline-flex items-center gap-1.5">
            <span aria-hidden className={cn("size-2 rounded-full", CHANGE_BAR[type] ?? "bg-neutral-300")} />
            {n} {CHANGE_LABEL[type]?.toLowerCase()}
          </span>
        ))}
      </p>
    </div>
  );
}

export function ModuleChanges({ brief }: { brief: MigrationIntentBrief }) {
  const items = brief.module_changes;
  return (
    <div className="space-y-4">
      <ChangeMix items={items} />
      <ul className="grid gap-3 lg:grid-cols-2">
        {items.map((m, i) => (
          <li key={`${m.module}-${i}`} className="bg-card flex flex-col gap-3 rounded-xl border p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="min-w-0">
                <p className="font-mono text-[13px] font-semibold">{m.module}</p>
                {m.path && m.path !== m.module && <p className="text-muted-foreground font-mono text-[11px]">{m.path}</p>}
              </div>
              <div className="flex items-center gap-3">
                <EffortMeter effort={m.effort} />
                <ChangeBadge type={m.change_type} />
              </div>
            </div>
            <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2">
              <TodayBox current={m.current} status={m.current_status} />
              <ArrowRight className="text-muted-foreground size-4" aria-hidden />
              <TargetBox target={m.target} />
            </div>
            {m.changes.length > 0 && (
              <ul className="space-y-1">
                {m.changes.map((c, j) => (
                  <li key={j} className="flex gap-2 text-[13px] leading-relaxed">
                    <span aria-hidden className="bg-primary/70 mt-2 size-1.5 shrink-0 rounded-full" />
                    <span>{c}</span>
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ── 6. trade-offs ────────────────────────────────────────────────────────── */

export function TradeOffs({ brief }: { brief: MigrationIntentBrief }) {
  return (
    <ul className="space-y-2.5">
      {brief.trade_offs.map((t, i) => (
        <li key={i} className="grid gap-2 rounded-xl border p-3 md:grid-cols-[minmax(12rem,0.9fr)_1fr_1fr] md:items-stretch">
          <p className="flex items-center gap-2 px-1 text-sm font-semibold leading-snug">
            <Scale className="text-muted-foreground size-4 shrink-0" aria-hidden />
            {t.decision}
          </p>
          <div className="bg-primary/8 rounded-lg px-3 py-2">
            <p className="flex items-center gap-1 text-[10.5px] font-semibold tracking-wide text-orange-900 uppercase dark:text-orange-200">
              <TrendingUp className="size-3" aria-hidden /> What we gain
            </p>
            <p className="mt-0.5 text-[13px] leading-relaxed">{t.gain || "—"}</p>
          </div>
          <div className="bg-muted rounded-lg px-3 py-2">
            <p className="flex items-center gap-1 text-[10.5px] font-semibold tracking-wide text-neutral-600 uppercase dark:text-neutral-400">
              <Wallet className="size-3" aria-hidden /> What it costs
            </p>
            <p className="mt-0.5 text-[13px] leading-relaxed">{t.cost || "—"}</p>
          </div>
        </li>
      ))}
    </ul>
  );
}

/* ── 7. timeline ──────────────────────────────────────────────────────────── */

export function Timeline({ brief }: { brief: MigrationIntentBrief }) {
  const items = sortedMilestones(brief);
  const dated = items
    .map((m) => ({ m, d: parseDate(m.date) }))
    .filter((x): x is { m: (typeof items)[number]; d: Date } => x.d !== null);
  const start = dated[0]?.d.getTime() ?? 0;
  const span = Math.max((dated[dated.length - 1]?.d.getTime() ?? 0) - start, 1);
  const pos = (d: Date) => 6 + ((d.getTime() - start) / span) * 88;
  const now = Date.now();
  const showToday = dated.length >= 2 && now >= start && now <= start + span;

  return (
    <div className="space-y-4">
      {dated.length >= 2 && (
        <div className="bg-card relative hidden h-44 rounded-xl border md:block" aria-hidden>
          {/* Grey into orange: from where the programme starts to its deadline. */}
          <div className="from-border to-primary/50 absolute top-1/2 right-[3%] left-[3%] h-1 -translate-y-1/2 rounded-full bg-gradient-to-r" />
          {showToday && (
            <div className="absolute top-1/2 -translate-x-1/2" style={{ left: `${pos(new Date(now))}%` }}>
              <div className="bg-muted-foreground/60 mx-auto h-8 w-px -translate-y-1/2" />
              <p className="text-muted-foreground mt-1 text-[10px] font-medium">Today</p>
            </div>
          )}
          {dated.map(({ m, d }, i) => {
            const up = i % 2 === 0;
            return (
              <div key={`${m.label}-${i}`} className="absolute top-1/2 -translate-x-1/2" style={{ left: `${pos(d)}%` }}>
                <span className={cn("ring-card absolute top-0 left-1/2 size-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full ring-4", milestoneDot(m.kind))} />
                <div className={cn("absolute left-1/2 w-36 -translate-x-1/2 text-center", up ? "bottom-4" : "top-4")}>
                  <p className={cn("text-[11px] font-semibold", m.kind === "deadline" ? "text-primary" : "text-foreground")}>{prettyDate(m.date)}</p>
                  <p className="text-muted-foreground text-[11px] leading-tight">{m.label}</p>
                </div>
              </div>
            );
          })}
        </div>
      )}
      <ol className="divide-y rounded-xl border">
        {items.map((m, i) => (
          <li key={`${m.label}-${i}`} className="grid grid-cols-[8.5rem_1fr_auto] items-center gap-3 px-4 py-2.5 text-sm">
            <span className="flex items-center gap-2 font-semibold tabular-nums">
              <span aria-hidden className={cn("size-2 rounded-full", milestoneDot(m.kind))} />
              {prettyDate(m.date)}
            </span>
            <span>{m.label}</span>
            <span className={cn("text-[11px] font-semibold", milestoneInk(m.kind))}>
              {MILESTONE_LABEL[m.kind] ?? "Milestone"}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

/* ── 8. constraints ───────────────────────────────────────────────────────── */

const CONSTRAINT_ICONS: [RegExp, React.ElementType][] = [
  [/freeze|frozen/i, Snowflake],
  [/budget|\$|cost|€|£/i, Wallet],
  [/downtime|window|hour|cutover/i, Clock],
  [/api|contract|byte|format|interface|unchanged|must not change/i, Lock],
  [/data|region|residen|gdpr|pii/i, Globe],
  [/deadline|by \d|before|until|june|july|march|20\d\d/i, CalendarClock],
];

export function Constraints({ brief }: { brief: MigrationIntentBrief }) {
  const items = brief.constraints.filter((c) => c.trim());
  if (!items.length) return <p className="text-muted-foreground text-sm">None recorded.</p>;
  return (
    <ul className="grid gap-2 md:grid-cols-2">
      {items.map((c, i) => {
        const Icon = CONSTRAINT_ICONS.find(([re]) => re.test(c))?.[1] ?? Lock;
        return (
          <li key={i} className="bg-card flex items-start gap-3 rounded-xl border px-3.5 py-2.5">
            <span className="bg-primary/10 text-primary flex size-7 shrink-0 items-center justify-center rounded-md">
              <Icon className="size-3.5" aria-hidden />
            </span>
            <span className="text-[13.5px] leading-relaxed">{c}</span>
          </li>
        );
      })}
    </ul>
  );
}

/* ── 9. success ───────────────────────────────────────────────────────────── */

/** What a model writes when there is no figure for today — shown as nothing. */
function isPlaceholder(text: string): boolean {
  return ["", "-", "—", "n/a", "na", "none", "unknown", "tbd", "not measured"].includes(text.trim().toLowerCase());
}

export function Success({ brief }: { brief: MigrationIntentBrief }) {
  const criteria = brief.success_criteria.filter((c) => c.trim());
  return (
    <div className="space-y-4">
      {brief.success_measures.length > 0 && (
        <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {brief.success_measures.map((m, i) => (
            <li key={i} className="bg-card rounded-xl border p-4">
              <p className="text-muted-foreground flex items-center gap-1.5 text-[11px] font-semibold tracking-wide uppercase">
                <Target className="size-3.5" aria-hidden /> {m.metric}
              </p>
              <div className="mt-2 flex items-baseline gap-2">
                {!isPlaceholder(m.current) && (
                  <>
                    <span className="text-muted-foreground text-sm">{m.current}</span>
                    <ArrowRight className="text-muted-foreground size-3.5 self-center" aria-hidden />
                  </>
                )}
                <span className="font-display text-primary text-2xl font-semibold">{m.target || "—"}</span>
              </div>
            </li>
          ))}
        </ul>
      )}
      {criteria.length > 0 && (
        <div>
          {brief.success_measures.length > 0 && <p className="mb-2 text-sm font-semibold">Acceptance criteria</p>}
          <ul className="space-y-1.5">
            {criteria.map((c, i) => (
              <li key={i} className="flex gap-2 text-[13.5px] leading-relaxed">
                <CircleDashed className="text-primary mt-0.5 size-4 shrink-0" aria-hidden />
                <span>{c}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/* ── 10–11. people, assumptions, risks, open questions ───────────────────── */

export function People({ brief }: { brief: MigrationIntentBrief }) {
  return (
    <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
      {brief.stakeholders.map((s, i) => (
        <li key={`${s.name}-${i}`} className="bg-card flex items-center gap-3 rounded-xl border px-3.5 py-3">
          <span className="bg-primary/10 flex size-9 shrink-0 items-center justify-center rounded-full text-xs font-semibold text-orange-800 dark:text-orange-200">
            {s.name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase()}
          </span>
          <div className="min-w-0">
            <p className="text-sm font-semibold">{s.name}</p>
            {s.role && <p className="text-muted-foreground text-xs leading-snug">{s.role}</p>}
          </div>
        </li>
      ))}
    </ul>
  );
}

export function RisksAndQuestions({ brief }: { brief: MigrationIntentBrief }) {
  const cols: { label: string; items: string[]; icon: React.ElementType; tone: string; empty: string }[] = [
    { label: "Assumptions", items: brief.assumptions, icon: Lightbulb, tone: "bg-muted text-neutral-700 dark:text-neutral-300", empty: "None recorded." },
    { label: "Risks", items: brief.risks, icon: TriangleAlert, tone: "bg-destructive/10 text-red-700 dark:text-red-400", empty: "None recorded." },
    { label: "Open questions", items: brief.open_questions, icon: CircleHelp, tone: "bg-muted text-neutral-700 dark:text-neutral-300", empty: "None." },
  ];
  return (
    <div className="grid gap-3 md:grid-cols-3">
      {cols.map(({ label, items, icon: Icon, tone, empty }) => {
        const clean = items.filter((x) => x.trim());
        return (
          <div key={label} className="overflow-hidden rounded-xl border">
            <p className={cn("flex items-center gap-1.5 px-4 py-2 text-[11px] font-semibold tracking-wide uppercase", tone)}>
              <Icon className="size-3.5" aria-hidden /> {label}
            </p>
            <ul className="space-y-1.5 p-4">
              {clean.length ? clean.map((x, i) => (
                <li key={i} className="flex gap-2 text-[13px] leading-relaxed">
                  <span aria-hidden className="bg-muted-foreground/60 mt-2 size-1.5 shrink-0 rounded-full" />
                  <span>{x}</span>
                </li>
              )) : <li className="text-muted-foreground text-[13px]">{empty}</li>}
            </ul>
          </div>
        );
      })}
    </div>
  );
}

const PROVIDER: Record<string, string> = { ado: "Azure DevOps", github: "GitHub", gitlab: "GitLab", bitbucket: "Bitbucket" };

export function RepositoryLine({ brief }: { brief: MigrationIntentBrief }) {
  const repo = brief.legacy_repository;
  if (!repo || !(repo.url || repo.name)) return null;
  let url = repo.url;
  try {
    url = decodeURI(repo.url);
  } catch {
    /* keep it as stored */
  }
  const where = [PROVIDER[repo.provider.toLowerCase()] ?? repo.provider, repo.project, repo.name].filter(Boolean).join(" / ");
  return (
    <p className="text-muted-foreground flex flex-wrap items-center gap-x-2 gap-y-1 border-t pt-4 text-xs">
      <FolderGit2 className="size-3.5" aria-hidden />
      <span className="font-semibold tracking-wide uppercase">Legacy repository</span>
      <span className="text-foreground">{where || url}</span>
      {url && where && <span className="font-mono">{url}</span>}
    </p>
  );
}
