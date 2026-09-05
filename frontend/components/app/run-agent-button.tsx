"use client";

import Link from "next/link";
import { Play } from "lucide-react";

import { Button } from "@/components/ui/button";
import { canUseOrchestrator } from "@/lib/orchestrator/access";
import type { PlatformRole } from "@/lib/roles";

export function RunAgentButton({
  projectId,
  role,
  disabled,
}: {
  projectId: string;
  role: PlatformRole | null | undefined;
  /** Set while the project's own governance state blocks agent runs
   * (pending approval / rejected) — renders a disabled button instead of a
   * live link rather than hiding the control outright. */
  disabled?: boolean;
}) {
  // Hidden rather than disabled: a disabled control invites a request for access
  // that this button is not the right place to make. Delivery roles start from the
  // agent tiles on Overview, which carry their own access affordance.
  if (!canUseOrchestrator(role)) return null;

  if (disabled) {
    return (
      <Button size="sm" disabled>
        <Play className="size-3.5" aria-hidden />
        Run agent
      </Button>
    );
  }

  return (
    <Button asChild size="sm">
      <Link href={`/orchestrator?project=${encodeURIComponent(projectId)}`}>
        <Play className="size-3.5" aria-hidden />
        Run agent
      </Link>
    </Button>
  );
}
