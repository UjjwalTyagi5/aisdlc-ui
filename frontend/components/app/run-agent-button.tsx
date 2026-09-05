"use client";

import Link from "next/link";
import { Play } from "lucide-react";

import { Button } from "@/components/ui/button";
import { canUseOrchestrator } from "@/lib/orchestrator/access";
import type { PlatformRole } from "@/lib/roles";

export function RunAgentButton({
  projectId,
  role,
}: {
  projectId: string;
  role: PlatformRole | null | undefined;
}) {
  // Hidden rather than disabled: a disabled control invites a request for access
  // that this button is not the right place to make. Delivery roles start from the
  // agent tiles on Overview, which carry their own access affordance.
  if (!canUseOrchestrator(role)) return null;

  return (
    <Button asChild size="sm">
      <Link href={`/orchestrator?project=${encodeURIComponent(projectId)}`}>
        <Play className="size-3.5" aria-hidden />
        Run agent
      </Link>
    </Button>
  );
}
