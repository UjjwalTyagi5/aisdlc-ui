"use client";

import { ClipboardCheck, MessageCircleQuestion, Timer } from "lucide-react";

import { cn } from "@/lib/utils";
import type { ApprovalQueueMetrics } from "@/lib/schemas";

function ageLabel(minutes: number): string {
  if (minutes < 60) return `${minutes}m`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m ? `${h}h ${m}m` : `${h}h`;
}

export function ApprovalQueueStrip({ metrics }: { metrics: ApprovalQueueMetrics }) {
  const oldBad = metrics.oldestMinutes >= 120;
  const tiles = [
    {
      icon: ClipboardCheck,
      label: "Approvals",
      value: String(metrics.approvals),
      tone: "text-brand-bright bg-brand-bright/12",
    },
    {
      icon: MessageCircleQuestion,
      label: "Clarifications",
      value: String(metrics.clarifications),
      tone: "text-info bg-info/12",
    },
    {
      icon: Timer,
      label: "Oldest waiting",
      value: ageLabel(metrics.oldestMinutes),
      tone: oldBad ? "text-warning bg-warning/15" : "text-success bg-success/12",
    },
  ];
  return (
    <div className="border-line-soft bg-panel-elevated grid grid-cols-3 gap-px overflow-hidden rounded-2xl border">
      {tiles.map((t, i) => (
        <div
          key={t.label}
          className={cn("flex items-center gap-3.5 px-5 py-4", i > 0 && "border-line-soft border-l")}
        >
          <span className={cn("grid size-9 shrink-0 place-items-center rounded-xl", t.tone)}>
            <t.icon className="size-4.5" aria-hidden />
          </span>
          <div className="flex min-w-0 flex-col">
            <span className="font-display text-xl leading-none font-bold tabular-nums">
              {t.value}
            </span>
            <span className="text-muted-foreground mt-1 text-[12px]">{t.label}</span>
          </div>
        </div>
      ))}
    </div>
  );
}
