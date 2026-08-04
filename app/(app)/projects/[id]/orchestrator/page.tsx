"use client";

import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Sparkles, Workflow } from "lucide-react";

import { OrchestratorCockpit } from "@/components/orchestrator/cockpit";

/**
 * `/projects/[id]/orchestrator` — the **per-project** Orchestrator.
 *
 * The same cockpit as the global `/orchestrator`, with the project fixed to
 * this route: no project picker, and the session rail lists only this
 * project's runs. Everything else — the model picker, the auto-sequencing run,
 * the gate controls, the pipeline rail — is identical, because it is literally
 * the same component (`components/orchestrator/cockpit.tsx`).
 *
 * PRD NOTE — §34.11 describes the Orchestrator as "a conversation partner, not
 * an automatic sequencer… nothing auto-advances". This page auto-advances by
 * default, which is a deliberate product decision taken by the user and not an
 * oversight. The PRD's constraint is preserved where it is load-bearing rather
 * than stylistic: **mandatory gates are never auto-approved** (PRD §13 makes
 * them unwaivable), and the Auto-advance switch turns the sequencer off
 * entirely, which restores the documented stage-by-stage behaviour.
 */
export default function ProjectOrchestratorPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;

  return (
    <div className="w-full px-4 pb-6 md:px-10 md:pb-8">
      <div className="flex flex-wrap items-center justify-between gap-3 pt-4">
        <div className="flex items-center gap-2">
          <span className="bg-primary text-primary-foreground grid size-7 place-items-center rounded-md">
            <Sparkles className="size-4" aria-hidden />
          </span>
          <div>
            <h2 className="font-display text-[15px] font-semibold tracking-tight">
              Orchestrator
            </h2>
            <p className="text-muted-foreground text-[12px]">
              Runs this project&apos;s agent roster in hand-off order.
            </p>
          </div>
        </div>

        <Link
          href={`/orchestrator?project=${encodeURIComponent(id)}`}
          className="border-line-soft bg-surface-1 hover:border-primary/40 inline-flex shrink-0 items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-[12px] transition-colors"
        >
          <Workflow className="size-3.5" aria-hidden />
          Open in the global Orchestrator
        </Link>
      </div>

      <OrchestratorCockpit lockedProjectId={id} variant="embedded" />
    </div>
  );
}
