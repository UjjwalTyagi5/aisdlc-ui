"use client";

import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Sparkles, Workflow } from "lucide-react";

import { OrchestratorCockpit } from "@/components/orchestrator/cockpit";
import { useSession } from "@/hooks/use-session";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import { canUseOrchestrator } from "@/lib/orchestrator/access";
import { OutOfScope } from "@/components/auth/scope-empty-state";

/**
 * `/projects/[id]/orchestrator` — the **per-project** Orchestrator.
 *
 * The same cockpit as the global `/orchestrator`, with the project fixed to
 * this route: no project picker, and the session rail lists only this
 * project's runs. Everything else is identical, because it is literally the
 * same component (`components/orchestrator/cockpit.tsx`).
 *
 * A conversation partner, not a sequencer (PRD §34.11): any of the project's
 * agents can pick up work at any time, based on what the conversation asks
 * for. There is no fixed hand-off order, no auto-advance, and no gates or
 * sign-off — Project Admin only (`lib/orchestrator/access.ts`), since driving
 * it reaches every agent on the project at once.
 */
export default function ProjectOrchestratorPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const session = useSession({ required: true });
  const role = effectivePlatformRole(session);
  const allowed = canUseOrchestrator(role);

  if (!allowed) {
    return <OutOfScope kind="resource" backHref={`/projects/${id}`} backLabel="This project" />;
  }

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
              Any of this project&apos;s agents, chosen from the conversation — no fixed order.
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
