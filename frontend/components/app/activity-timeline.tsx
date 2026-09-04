"use client";

import * as React from "react";
import {
  Brain,
  Check,
  ChevronDown,
  ChevronRight,
  Code2,
  FilePenLine,
  Shield,
  UserCheck,
  Workflow,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { CostBadge } from "@/components/ui/cost-badge";
import { StatusBadge } from "@/components/ui/status-badge";
import type { Step, StepKind } from "@/lib/schemas";

const KIND_ICON: Record<StepKind, LucideIcon> = {
  plan: Workflow,
  tool_call: Code2,
  llm_call: Brain,
  guardrail_check: Shield,
  artifact_write: FilePenLine,
  hitl_wait: UserCheck,
};

const KIND_LABEL: Record<StepKind, string> = {
  plan: "Project Manager",
  tool_call: "Tool call",
  llm_call: "LLM call",
  guardrail_check: "Guardrail",
  artifact_write: "Artifact",
  hitl_wait: "HITL gate",
};

export interface ActivityTimelineProps {
  steps: readonly Step[];
  /** Group by `runId` adds collapsible run headers; omit for a flat list. */
  groupBy?: "run" | "none";
  className?: string;
  /** Called when a row is clicked — useful for opening a Run detail drawer. */
  onStepSelect?: (step: Step) => void;
}

export function ActivityTimeline({
  steps,
  groupBy = "none",
  className,
  onStepSelect,
}: ActivityTimelineProps) {
  if (steps.length === 0) {
    return (
      <div className="py-8 text-center font-mono text-[12px] text-muted-foreground">
        No activity yet.
      </div>
    );
  }

  if (groupBy === "none") {
    return (
      <ol
        className={cn(
          "divide-y divide-line-soft space-y-0",
          className,
        )}
        aria-label="Activity timeline"
      >
        {steps.map((s) => (
          <StepRow key={s.id} step={s} onSelect={onStepSelect} />
        ))}
      </ol>
    );
  }

  // Group by run
  const groups = new Map<string, Step[]>();
  for (const s of steps) {
    const arr = groups.get(s.runId) ?? [];
    arr.push(s);
    groups.set(s.runId, arr);
  }

  return (
    <div className={cn("space-y-5", className)}>
      {Array.from(groups.entries()).map(([runId, rows]) => (
        <RunGroup key={runId} runId={runId} steps={rows} onSelect={onStepSelect} />
      ))}
    </div>
  );
}

function RunGroup({
  runId,
  steps,
  onSelect,
}: {
  runId: string;
  steps: Step[];
  onSelect?: (s: Step) => void;
}) {
  const [open, setOpen] = React.useState(true);
  return (
    <section>
      {/* Run group header — font-display section label */}
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-surface-1"
        aria-expanded={open}
      >
        {open ? (
          <ChevronDown className="size-3.5 text-muted-foreground" aria-hidden />
        ) : (
          <ChevronRight className="size-3.5 text-muted-foreground" aria-hidden />
        )}
        <span className="font-display text-sm font-bold tracking-[-0.01em]">
          Run
        </span>
        <span className="font-mono text-[11px] text-brand-bright">{runId}</span>
        <span className="font-mono text-[11px] text-muted-foreground">
          · {steps.length} step{steps.length === 1 ? "" : "s"}
        </span>
      </button>
      {open && (
        <ol className="mt-1 divide-y divide-line-soft border-l border-line-soft pl-3">
          {steps.map((s) => (
            <StepRow key={s.id} step={s} onSelect={onSelect} />
          ))}
        </ol>
      )}
    </section>
  );
}

function StepRow({ step, onSelect }: { step: Step; onSelect?: (s: Step) => void }) {
  const Icon = KIND_ICON[step.kind];
  // Mono timestamp — ISO precise format per the northstar stream-row style
  const time = new Date(step.startedAt).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  const body = (
    <div
      className={cn(
        "group flex items-start gap-3 px-2 py-2.5",
        onSelect && "cursor-pointer rounded-md transition-colors hover:bg-surface-1",
      )}
    >
      {/* Kind icon in a circular halo */}
      <div className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-full border border-line-soft bg-surface-2 text-muted-foreground">
        {step.status === "approved" ? (
          <Check className="size-3 text-success" />
        ) : (
          <Icon className="size-3" />
        )}
      </div>

      {/* Content */}
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-semibold">{step.title}</span>
          <span className="shrink-0 font-mono text-[10.5px] text-muted-foreground">
            {KIND_LABEL[step.kind]}
          </span>
        </div>
        {step.summary && (
          <p className="truncate font-mono text-[11px] text-muted-foreground">{step.summary}</p>
        )}
        {step.error && (
          <p className="truncate font-mono text-[11px] text-destructive">
            {step.error.code}: {step.error.message}
          </p>
        )}
      </div>

      {/* Right — mono timestamp + cost + status */}
      <div className="flex shrink-0 flex-col items-end gap-1">
        <span className="font-mono text-[10.5px] tabular-nums text-muted-foreground">{time}</span>
        <div className="flex items-center gap-1">
          {step.cost && (
            <CostBadge
              usd={step.cost.usd}
              tokens={step.cost.inputTokens + step.cost.outputTokens}
            />
          )}
          <StatusBadge status={step.status} iconOnly />
        </div>
      </div>
    </div>
  );

  return (
    <li>
      {onSelect ? (
        <button
          type="button"
          onClick={() => onSelect(step)}
          className="w-full text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1"
        >
          {body}
        </button>
      ) : (
        body
      )}
    </li>
  );
}
