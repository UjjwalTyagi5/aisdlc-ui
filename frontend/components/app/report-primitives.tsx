"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * The pieces every designed report on the platform is built from — the same
 * family as the Track 3 brief (`components/modernization/brief-sections.tsx`) and
 * the Word canvas (`backend/shared/docs/pwc_style.py`): a hero band running from
 * white into PwC orange, a key-facts strip, numbered sections over a rule, pills
 * for status, bars for a percentage.
 *
 * The boldness is spent in one place: the hero. Everything below it is quiet —
 * grey rules, tinted table headers, one accent colour — so the numbers read.
 */

/* ── hero ─────────────────────────────────────────────────────────────────── */

export function ReportHero({
  eyebrow,
  eyebrowTail,
  title,
  subtitle,
  meta,
  aside,
  children,
}: {
  /** The document kind, in caps: "TEST RUN REPORT", "BUSINESS REQUIREMENTS DOCUMENT". */
  eyebrow: string;
  /** What it is about — "Unit · QuickLink @ main". */
  eyebrowTail?: string;
  title: string;
  subtitle?: string;
  /** The small mono line under the title — "Generated 16 Sep 2026 · pytest". */
  meta?: string;
  /** Top-right of the band: a verdict pill, actions. */
  aside?: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <div className="from-card via-card to-primary/15 border-primary/15 relative overflow-hidden rounded-2xl border bg-gradient-to-r px-6 py-6 md:px-8 md:py-7">
      <div aria-hidden className="bg-primary/15 pointer-events-none absolute -top-24 -right-16 size-72 rounded-full blur-3xl" />
      <div className="relative flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <p className="font-mono text-[10.5px] tracking-[0.18em] uppercase">
            <span className="font-semibold text-orange-800 dark:text-orange-300">{eyebrow}</span>
            {eyebrowTail && <span className="text-neutral-600 dark:text-neutral-400"> · {eyebrowTail}</span>}
          </p>
          <h2 className="font-display mt-2 text-3xl font-semibold tracking-tight md:text-4xl">{title}</h2>
          {subtitle && <p className="text-foreground/80 mt-3 max-w-3xl text-[15px] leading-relaxed">{subtitle}</p>}
          {meta && <p className="mt-4 font-mono text-[11px] text-neutral-600 dark:text-neutral-400">{meta}</p>}
        </div>
        {aside && <div className="flex shrink-0 flex-wrap items-center gap-2">{aside}</div>}
      </div>
      {children && <div className="relative mt-4">{children}</div>}
    </div>
  );
}

/* ── the key-facts strip ───────────────────────────────────────────────────── */

export interface Fact {
  label: string;
  value: React.ReactNode;
  icon?: React.ElementType;
  tone?: "default" | "success" | "warning" | "danger";
  /** A second line under the value — "of 3", "threshold 80%". */
  hint?: string;
}

const FACT_TONE: Record<NonNullable<Fact["tone"]>, { chip: string; value: string }> = {
  default: { chip: "bg-primary/10 text-primary", value: "" },
  success: { chip: "bg-success/15 text-success", value: "text-success" },
  warning: { chip: "bg-warning/25 text-amber-800 dark:text-amber-300", value: "text-amber-800 dark:text-amber-300" },
  danger: { chip: "bg-destructive/10 text-destructive", value: "text-red-700 dark:text-red-400" },
};

export function FactStrip({ facts, className }: { facts: Fact[]; className?: string }) {
  const shown = facts.filter((f) => f.value !== null && f.value !== undefined && f.value !== "");
  if (shown.length === 0) return null;
  return (
    <dl className={cn("grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6", className)}>
      {shown.map((f) => {
        const tone = FACT_TONE[f.tone ?? "default"];
        const Icon = f.icon;
        return (
          <div key={f.label} className="bg-card flex min-w-0 items-start gap-3 rounded-xl border px-3.5 py-3">
            {Icon && (
              <span className={cn("mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg", tone.chip)}>
                <Icon className="size-4" aria-hidden />
              </span>
            )}
            <div className="min-w-0">
              <dt className="text-muted-foreground text-[10.5px] font-semibold tracking-wide uppercase">{f.label}</dt>
              {/* A long value (a file name) is cut to the card, whole on hover — it spilled out. */}
              <dd className={cn("font-display truncate text-base font-semibold tabular-nums", tone.value)}
                title={typeof f.value === "string" ? f.value : undefined}>{f.value}</dd>
              {f.hint && <dd className="text-muted-foreground text-[11px]">{f.hint}</dd>}
            </div>
          </div>
        );
      })}
    </dl>
  );
}

/* ── numbered section ──────────────────────────────────────────────────────── */

export function ReportSection({
  n,
  title,
  id,
  aside,
  children,
}: {
  n: number;
  title: string;
  id: string;
  /** Right of the title — a count, a filter, a link. */
  aside?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section aria-labelledby={id} className="space-y-4">
      <div className="flex items-baseline gap-3 border-b pb-2">
        <span className="text-primary font-mono text-sm font-semibold tabular-nums">{String(n).padStart(2, "0")}</span>
        <h3 id={id} className="font-display text-lg font-semibold tracking-tight">{title}</h3>
        {aside && <span className="text-muted-foreground ml-auto text-xs">{aside}</span>}
      </div>
      {children}
    </section>
  );
}

/* ── pills ────────────────────────────────────────────────────────────────── */

export type PillTone = "success" | "danger" | "warning" | "info" | "neutral" | "accent";

const PILL_TONE: Record<PillTone, string> = {
  success: "bg-success/15 text-emerald-800 dark:text-emerald-300",
  danger: "bg-destructive/10 text-red-700 dark:text-red-400",
  warning: "bg-warning/25 text-amber-800 dark:text-amber-300",
  info: "bg-info/15 text-sky-800 dark:text-sky-300",
  neutral: "bg-muted text-neutral-700 dark:text-neutral-300",
  accent: "bg-primary/10 text-orange-900 dark:text-orange-200",
};

export function Pill({ tone = "neutral", children, className, dot }: {
  tone?: PillTone; children: React.ReactNode; className?: string; dot?: boolean;
}) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-[11px] font-semibold whitespace-nowrap", PILL_TONE[tone], className)}>
      {dot && <span aria-hidden className="size-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}

/** The tone a word carries: a test status, a priority, a severity, a risk level. The
 *  Word canvas colours the same words (`pill_for` in markdown_docx.py). */
export function toneForWord(word: string): PillTone | null {
  const w = word.trim().toLowerCase();
  if (["passed", "pass", "ok", "done", "low", "approved", "healthy", "supported"].includes(w)) return "success";
  if (["failed", "fail", "error", "critical", "high", "blocked", "rejected", "eol", "end of life"].includes(w)) return "danger";
  if (["medium", "skipped", "partial", "warning", "at risk", "pending", "deprecated"].includes(w)) return "warning";
  if (["info", "new", "planned", "proposed"].includes(w)) return "info";
  return null;
}

/* ── percentage bar ────────────────────────────────────────────────────────── */

export function pctTone(pct: number, threshold?: number | null): PillTone {
  const t = threshold ?? 80;
  if (pct >= t) return "success";
  if (pct >= Math.max(0, t - 30)) return "warning";
  return "danger";
}

const BAR_FILL: Record<PillTone, string> = {
  success: "bg-success",
  danger: "bg-destructive",
  warning: "bg-warning",
  info: "bg-info",
  neutral: "bg-neutral-400",
  accent: "bg-primary",
};

export function PctBar({ pct, threshold, tone, className, thin }: {
  pct: number; threshold?: number | null; tone?: PillTone; className?: string; thin?: boolean;
}) {
  const t = tone ?? pctTone(pct, threshold);
  const width = Math.max(0, Math.min(100, pct));
  return (
    <div
      role="meter"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(width)}
      className={cn("bg-muted relative w-full overflow-hidden rounded-full", thin ? "h-1.5" : "h-2.5", className)}
    >
      <div className={cn("h-full rounded-full transition-[width]", BAR_FILL[t])} style={{ width: `${width}%` }} />
      {typeof threshold === "number" && threshold > 0 && threshold < 100 && (
        <span
          aria-hidden
          title={`Threshold ${threshold}%`}
          className="bg-foreground/60 absolute top-0 h-full w-px"
          style={{ left: `${threshold}%` }}
        />
      )}
    </div>
  );
}

/* ── callout ──────────────────────────────────────────────────────────────── */

export function Callout({ tone = "neutral", title, children, className }: {
  tone?: "neutral" | "danger" | "warning" | "success"; title?: string; children: React.ReactNode; className?: string;
}) {
  const edge = {
    neutral: "shadow-[inset_3px_0_0_0_var(--muted-foreground)] bg-muted/60",
    danger: "shadow-[inset_3px_0_0_0_var(--destructive)] bg-destructive/5",
    warning: "shadow-[inset_3px_0_0_0_var(--warning)] bg-warning/10",
    success: "shadow-[inset_3px_0_0_0_var(--success)] bg-success/5",
  }[tone];
  return (
    <div className={cn("rounded-lg px-4 py-3 text-sm", edge, className)}>
      {title && <p className="mb-1 text-[11px] font-semibold tracking-wide uppercase">{title}</p>}
      <div className="text-foreground/85 leading-relaxed">{children}</div>
    </div>
  );
}

/* ── tinted-header table ───────────────────────────────────────────────────── */

export function ReportTable({ head, children, className, dense }: {
  head: React.ReactNode; children: React.ReactNode; className?: string; dense?: boolean;
}) {
  return (
    <div className={cn("overflow-x-auto rounded-xl border", className)}>
      <table className={cn("w-full border-collapse text-sm", dense && "text-[13px]")}>
        <thead>
          <tr className="from-primary/5 to-primary/15 bg-gradient-to-r text-[10.5px] tracking-wide text-orange-900 uppercase dark:text-orange-200">
            {head}
          </tr>
        </thead>
        <tbody className="[&>tr]:border-t [&>tr:hover]:bg-muted/40">{children}</tbody>
      </table>
    </div>
  );
}

export const th = "px-3 py-2 text-left font-semibold";
export const td = "px-3 py-2 align-top";

/* ── mono chip (a file path, a command) ─────────────────────────────────────── */

export function MonoChip({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <span className={cn("border-line-soft bg-surface-2 text-muted-foreground rounded border px-1.5 py-0.5 font-mono text-[11px]", className)}>
      {children}
    </span>
  );
}

export function formatDuration(ms: number): string {
  if (!ms || ms < 0) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.round((ms % 60_000) / 1000);
  return `${m}m ${s}s`;
}
