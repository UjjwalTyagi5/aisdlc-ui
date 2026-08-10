import * as React from "react";
import { Clock } from "lucide-react";

import { Badge } from "@/components/ui/badge";

/**
 * Neutral stub badge used by the approval-card (Plan-08) to signal that
 * the full HITL approve→resume action is deferred to milestone-5.
 * Renders a neutral secondary badge so it reads as informational, not
 * a call to action. Uses Plan-01 tokens only.
 */
export function HitlDeferredBadge() {
  return (
    <Badge
      variant="secondary"
      className="inline-flex items-center gap-1.5 font-mono text-[10.5px] uppercase tracking-wider text-muted-foreground"
      title="Durable HITL approval signals are wired in milestone-5"
    >
      <Clock className="size-3" aria-hidden />
      HITL not yet wired
    </Badge>
  );
}
