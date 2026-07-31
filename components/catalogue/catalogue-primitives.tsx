"use client";

import * as React from "react";
import { ChevronDown } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";
import { CAPABILITY_CLASS_META } from "@/lib/capability-class";
import type { CapabilityClass } from "@/lib/schemas/enums";

/**
 * The Agent Catalogue's shared building blocks.
 *
 * Split out because the catalogue is long and repetitive by nature — nine
 * sections that all need the same section frame, the same chip, the same
 * disclosure. Keeping them here stops the page file from becoming a wall of
 * markup, and means a spacing or tone change happens once.
 *
 * Everything reuses existing design tokens (`--brand-bright`, `--surface-*`,
 * `--line-soft`) and the existing capability-class palette. Nothing here
 * introduces a new colour.
 */

/** A titled section with an eyebrow, used once per catalogue section. */
export function CatalogueSection({
  id,
  eyebrow,
  title,
  lead,
  actions,
  children,
  className,
}: {
  id: string;
  eyebrow: string;
  title: string;
  lead?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section id={id} className={cn("scroll-mt-24", className)} aria-labelledby={`${id}-title`}>
      <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div className="max-w-3xl">
          <div className="text-brand-bright mb-2 flex items-center gap-2 font-mono text-[11px] tracking-[0.14em] uppercase">
            <span className="bg-brand-bright inline-block h-px w-5" aria-hidden />
            {eyebrow}
          </div>
          <h2
            id={`${id}-title`}
            className="font-display text-[24px] leading-tight font-bold tracking-[-0.02em]"
          >
            {title}
          </h2>
          {lead && <p className="text-muted-foreground mt-2 text-[13.5px] leading-relaxed">{lead}</p>}
        </div>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </div>
      {children}
    </section>
  );
}

/** A neutral chip. `tone="brand"` for the one-per-context emphasis case. */
export function Chip({
  children,
  tone = "neutral",
  className,
}: {
  children: React.ReactNode;
  tone?: "neutral" | "brand" | "muted";
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10px] font-semibold whitespace-nowrap",
        tone === "brand" && "border-brand-bright/30 bg-brand-bright/10 text-brand-bright",
        tone === "neutral" && "border-line-soft bg-surface-2 text-muted-foreground",
        tone === "muted" && "border-transparent bg-transparent text-muted-foreground",
        className,
      )}
    >
      {children}
    </span>
  );
}

/**
 * A capability-class chip.
 *
 * Reads `CAPABILITY_CLASS_META` rather than restating the three classes, so
 * the catalogue cannot drift from how a gate row or the approvals queue
 * renders the same class.
 */
export function CapabilityClassChip({
  value,
  className,
}: {
  value: CapabilityClass;
  className?: string;
}) {
  const meta = CAPABILITY_CLASS_META[value];
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10px] font-semibold",
        meta.chipClass,
        className,
      )}
      title={`${meta.meaning} ${meta.uiBehaviour}`}
    >
      {meta.label}
    </span>
  );
}

/** A labelled statistic for the metrics strip. */
export function MetricTile({
  icon: Icon,
  value,
  label,
  sub,
}: {
  icon: LucideIcon;
  value: string;
  label: string;
  sub?: string;
}) {
  return (
    <div className="border-line-soft bg-panel-elevated rounded-xl border px-4 py-3.5">
      <div className="flex items-start justify-between gap-2">
        <span className="text-muted-foreground font-mono text-[10px] tracking-[0.12em] uppercase">
          {label}
        </span>
        <Icon className="text-muted-foreground/60 size-3.5 shrink-0" aria-hidden />
      </div>
      <p className="font-display mt-1.5 text-[26px] leading-none font-bold tracking-[-0.02em]">
        {value}
      </p>
      {sub && <p className="text-muted-foreground mt-1 text-[11.5px]">{sub}</p>}
    </div>
  );
}

/**
 * Progressive disclosure for the dense reference material.
 *
 * The catalogue carries a lot of detail per agent — inputs, outputs, approval
 * flow, per track. Showing all of it at once turns a discovery page into a
 * specification document, so the depth is one click away rather than absent.
 */
export function Disclosure({
  summary,
  count,
  children,
  defaultOpen = false,
}: {
  summary: React.ReactNode;
  count?: number;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = React.useState(defaultOpen);
  return (
    <div className="border-line-soft rounded-lg border">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="hover:bg-surface-1 flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left transition-colors"
      >
        <ChevronDown
          className={cn(
            "text-muted-foreground size-3.5 shrink-0 transition-transform",
            open && "rotate-180",
          )}
          aria-hidden
        />
        <span className="flex-1 text-[12.5px] font-medium">{summary}</span>
        {count !== undefined && <Chip tone="muted">{count}</Chip>}
      </button>
      {open && <div className="border-line-soft border-t px-3 py-3">{children}</div>}
    </div>
  );
}

/** A labelled list of short strings — inputs, outputs, deliverables. */
export function LabelledList({
  label,
  items,
  className,
}: {
  label: string;
  items: readonly string[];
  className?: string;
}) {
  if (items.length === 0) return null;
  return (
    <div className={className}>
      <p className="text-muted-foreground mb-1.5 font-mono text-[10px] tracking-[0.12em] uppercase">
        {label}
      </p>
      <ul className="space-y-1">
        {items.map((it) => (
          <li key={it} className="flex gap-2 text-[12.5px] leading-relaxed">
            <span className="bg-brand-bright/40 mt-[7px] size-1 shrink-0 rounded-full" aria-hidden />
            <span>{it}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Empty state for a filtered view that matched nothing. */
export function NoResults({ onClear }: { onClear: () => void }) {
  return (
    <div className="border-line-soft bg-surface-1 flex flex-col items-center gap-2 rounded-xl border border-dashed px-6 py-14 text-center">
      <p className="font-display text-[15px] font-semibold">Nothing matches those filters</p>
      <p className="text-muted-foreground max-w-md text-[13px]">
        The catalogue only contains what the platform documentation defines — try widening the
        track, phase or persona filter.
      </p>
      <button
        onClick={onClear}
        className="text-brand-bright mt-1 font-mono text-[12px] underline underline-offset-2"
      >
        Clear all filters
      </button>
    </div>
  );
}
