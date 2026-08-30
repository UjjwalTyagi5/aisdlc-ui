"use client";

import * as React from "react";
import Link from "next/link";
import { ArrowUpRight, type LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * StatTile — the Dashboard's KPI card (PRD §36, "scope summary" and
 * "spend vs. budget tiles").
 *
 * Deliberately reuses the existing elevation and brand tokens rather than
 * introducing a parallel card style, so the Dashboard reads as the same
 * product as the rest of the app.
 */

export interface StatTileProps {
  label: string;
  value: string;
  /** Optional smaller line under the value — e.g. "of $12,800 cap". */
  sub?: string;
  icon?: LucideIcon;
  /** Renders a progress bar; 0–1. Used for spend vs. budget. */
  progress?: number;
  /**
   * Tone of the progress bar / value. `warning` at ≥80% of cap and
   * `destructive` at ≥100% mirror the alert thresholds in PRD §34.5.
   */
  tone?: "default" | "warning" | "destructive" | "success";
  /** Makes the whole tile a link. */
  href?: string;
  className?: string;
}

const toneText: Record<NonNullable<StatTileProps["tone"]>, string> = {
  default: "text-foreground",
  warning: "text-warning",
  destructive: "text-destructive",
  success: "text-success",
};

const toneBar: Record<NonNullable<StatTileProps["tone"]>, string> = {
  default: "bg-primary",
  warning: "bg-warning",
  destructive: "bg-destructive",
  success: "bg-success",
};

export function StatTile({
  label,
  value,
  sub,
  icon: Icon,
  progress,
  tone = "default",
  href,
  className,
}: StatTileProps) {
  const pct =
    progress === undefined ? undefined : Math.max(0, Math.min(1, progress));

  const body = (
    <>
      <div className="flex items-start justify-between gap-2">
        <span className="text-muted-foreground font-mono text-[10.5px] font-medium tracking-widest uppercase">
          {label}
        </span>
        {Icon ? (
          <Icon className="text-primary/70 size-4 shrink-0" aria-hidden />
        ) : href ? (
          <ArrowUpRight
            className="text-muted-foreground/40 group-hover:text-brand-bright size-4 shrink-0 transition-colors"
            aria-hidden
          />
        ) : null}
      </div>

      <div
        className={cn(
          "font-display mt-2 text-[26px] leading-none font-bold tracking-[-0.02em]",
          toneText[tone],
        )}
      >
        {value}
      </div>

      {sub && <div className="text-muted-foreground mt-1.5 text-[12px]">{sub}</div>}

      {pct !== undefined && (
        <div
          className="bg-surface-2 mt-3 h-1.5 w-full overflow-hidden rounded-full"
          role="progressbar"
          aria-valuenow={Math.round(pct * 100)}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={`${label}: ${Math.round(pct * 100)}% used`}
        >
          <div
            className={cn("h-full rounded-full transition-[width]", toneBar[tone])}
            style={{ width: `${pct * 100}%` }}
          />
        </div>
      )}
    </>
  );

  const shell = cn(
    // PwC-branded KPI tile: a faint orange wash top-down over the usual
    // elevated panel, plus a top accent bar — reads as "brand" without
    // competing with the value/tone colors printed on top of it.
    "border-primary/15 from-primary/[0.07] to-panel-elevated relative overflow-hidden rounded-xl border bg-gradient-to-b p-4",
    "before:bg-primary/60 before:absolute before:inset-x-0 before:top-0 before:h-[3px] before:content-['']",
    href &&
      "group hover:border-primary/40 focus-visible:ring-ring block transition-colors focus-visible:ring-2 focus-visible:outline-none",
    className,
  );

  if (href) {
    return (
      <Link href={href} className={shell}>
        {body}
      </Link>
    );
  }
  return <div className={shell}>{body}</div>;
}

/** Responsive grid wrapper so every Dashboard lays its tiles out identically. */
export function StatTileGrid({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {children}
    </div>
  );
}

/** Formats a USD amount compactly — $1.2k, $12,284. */
export function usd(n: number): string {
  if (n >= 10_000) {
    return `$${n.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
  }
  return `$${n.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
}

/** Budget tone against the PRD §34.5 warn / hard-stop thresholds. */
export function budgetTone(spend: number, cap?: number | null): StatTileProps["tone"] {
  if (!cap || cap <= 0) return "default";
  const ratio = spend / cap;
  if (ratio >= 1) return "destructive";
  if (ratio >= 0.8) return "warning";
  return "default";
}
