"use client";

import * as React from "react";
import { AlertTriangle, Clock } from "lucide-react";

import { cn } from "@/lib/utils";

export interface SlaCountdownProps {
  /**
   * ISO-8601 deadline timestamp. The countdown ends (and enters breached state)
   * when wall-clock time passes this value.
   */
  deadline: string;
  className?: string;
}

/**
 * Thin SLA countdown used on approval-gate phases (D-04).
 * Renders MM:SS when < 1 h remaining, HH:MM when < 24 h, otherwise "Xd Yh".
 * Enters a breached/warning state (PwC orange) once the deadline is in the past.
 * Follows the milestone-4 design system: uses existing tokens (brand-bright,
 * warning, muted), no new packages, "use client" to run the interval client-side.
 */
export function SlaCountdown({ deadline, className }: SlaCountdownProps) {
  const deadlineMs = React.useMemo(() => new Date(deadline).getTime(), [deadline]);

  const [remaining, setRemaining] = React.useState<number>(() =>
    deadlineMs - Date.now(),
  );

  React.useEffect(() => {
    // Update immediately on mount then every second
    const tick = () => setRemaining(deadlineMs - Date.now());
    tick();
    const id = setInterval(tick, 1_000);
    return () => clearInterval(id);
  }, [deadlineMs]);

  const breached = remaining <= 0;

  const label = React.useMemo(() => {
    if (breached) return "SLA breached";

    const totalSeconds = Math.floor(remaining / 1_000);
    const days = Math.floor(totalSeconds / 86_400);
    const hours = Math.floor((totalSeconds % 86_400) / 3_600);
    const minutes = Math.floor((totalSeconds % 3_600) / 60);
    const seconds = totalSeconds % 60;

    if (days > 0) {
      return `${days}d ${hours}h`;
    }
    if (hours > 0) {
      const hh = String(hours).padStart(2, "0");
      const mm = String(minutes).padStart(2, "0");
      return `${hh}:${mm}`;
    }
    const mm = String(minutes).padStart(2, "0");
    const ss = String(seconds).padStart(2, "0");
    return `${mm}:${ss}`;
  }, [remaining, breached]);

  return (
    <div
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-mono text-[11px] font-semibold uppercase tracking-wider transition-colors",
        breached
          ? "border-warning/50 bg-warning/10 text-warning"
          : remaining < 3_600_000
          ? "border-warning/30 bg-warning/5 text-foreground"
          : "border-line-soft bg-muted/60 text-muted-foreground",
        className,
      )}
      role="timer"
      aria-label={breached ? "SLA deadline has passed" : `SLA deadline in ${label}`}
      aria-live="polite"
    >
      {breached ? (
        <AlertTriangle className="size-3 text-warning" aria-hidden />
      ) : (
        <Clock className="size-3" aria-hidden />
      )}
      <span>{label}</span>
    </div>
  );
}
