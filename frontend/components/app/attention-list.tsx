"use client";

import * as React from "react";
import Link from "next/link";
import {
  AlertOctagon,
  AlertTriangle,
  ChevronRight,
  CircleCheck,
  Clock,
  PlugZap,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * AttentionList — the Dashboard's "what needs you" panel (PRD §36).
 *
 * The PRD names exactly three kinds of item:
 *   · overdue approvals
 *   · budgets near or over cap
 *   · broken connectors
 *
 * Integration errors surface here too, per PRD §37 ("Error (integration) —
 * surfaced on the Dashboard attention list").
 *
 * The list is deliberately not a notification feed: every row is something a
 * human must act on, ordered by severity, with one clear destination.
 */

export type AttentionKind =
  | "overdue_approval"
  | "budget_near_cap"
  | "budget_over_cap"
  | "broken_connector";

export interface AttentionItem {
  id: string;
  kind: AttentionKind;
  title: string;
  detail: string;
  href: string;
  /** Optional right-aligned meta, e.g. "4h overdue". */
  meta?: string;
}

const KIND_META: Record<
  AttentionKind,
  { icon: LucideIcon; tone: string; iconBg: string; severity: number; label: string }
> = {
  budget_over_cap: {
    icon: AlertOctagon,
    tone: "text-destructive",
    iconBg: "bg-destructive/10",
    severity: 0,
    label: "Over cap",
  },
  overdue_approval: {
    icon: Clock,
    tone: "text-warning",
    iconBg: "bg-warning/10",
    severity: 1,
    label: "Overdue",
  },
  broken_connector: {
    icon: PlugZap,
    tone: "text-destructive",
    iconBg: "bg-destructive/10",
    severity: 2,
    label: "Broken",
  },
  budget_near_cap: {
    icon: AlertTriangle,
    tone: "text-warning",
    iconBg: "bg-warning/10",
    severity: 3,
    label: "Near cap",
  },
};

export function AttentionList({
  items,
  title = "Needs your attention",
  emptyMessage = "Nothing needs your attention. No overdue approvals, no budgets near cap, no broken connectors.",
}: {
  items: AttentionItem[];
  title?: string;
  emptyMessage?: string;
}) {
  const sorted = React.useMemo(
    () => [...items].sort((a, b) => KIND_META[a.kind].severity - KIND_META[b.kind].severity),
    [items],
  );

  return (
    <section
      aria-labelledby="attention-heading"
      className="border-line-soft bg-panel-elevated rounded-xl border"
    >
      <div className="border-line-soft flex items-center justify-between border-b px-4 py-3">
        <h2
          id="attention-heading"
          className="font-display text-[13px] font-semibold tracking-tight"
        >
          {title}
        </h2>
        {sorted.length > 0 && (
          <span className="border-line-soft text-muted-foreground rounded-full border px-2 py-0.5 font-mono text-[10.5px]">
            {sorted.length}
          </span>
        )}
      </div>

      {sorted.length === 0 ? (
        <div className="flex items-start gap-3 px-4 py-6">
          <CircleCheck className="text-success mt-px size-4 shrink-0" aria-hidden />
          <p className="text-muted-foreground text-[13px] leading-relaxed">
            {emptyMessage}
          </p>
        </div>
      ) : (
        <ul className="divide-line-soft divide-y">
          {sorted.map((item) => {
            const meta = KIND_META[item.kind];
            const Icon = meta.icon;
            return (
              <li key={item.id}>
                <Link
                  href={item.href}
                  className="hover:bg-surface-1 focus-visible:ring-ring group flex items-start gap-3 px-4 py-3 transition-colors focus-visible:ring-2 focus-visible:outline-none focus-visible:-outline-offset-2"
                >
                  <span
                    className={cn(
                      "mt-px flex size-6 shrink-0 items-center justify-center rounded-md",
                      meta.iconBg,
                    )}
                  >
                    <Icon className={cn("size-3.5", meta.tone)} aria-hidden />
                  </span>

                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <span className="text-[13px] font-medium">{item.title}</span>
                      <span
                        className={cn(
                          "rounded-full px-1.5 py-px font-mono text-[10px] tracking-wide uppercase",
                          meta.iconBg,
                          meta.tone,
                        )}
                      >
                        {meta.label}
                      </span>
                    </span>
                    <span className="text-muted-foreground mt-0.5 block text-[12px] leading-snug">
                      {item.detail}
                    </span>
                  </span>

                  {item.meta && (
                    <span className="text-muted-foreground shrink-0 font-mono text-[11px]">
                      {item.meta}
                    </span>
                  )}
                  <ChevronRight
                    className="text-muted-foreground/40 group-hover:text-foreground mt-0.5 size-4 shrink-0 transition-colors"
                    aria-hidden
                  />
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
