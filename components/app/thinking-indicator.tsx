"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Live "working" indicator: three bouncing dots + an elapsed-seconds counter that
 * ticks every second. Mounts when an agent turn starts streaming and unmounts when
 * it stops, so the timer reflects how long the current turn has been working — even
 * during long tool calls when no text streams (so it never looks frozen).
 *
 * Shared by the standalone agent chat drawer and the Orchestrator Copilot thread so
 * both surfaces read identically (the Claude-Code "✻ Working… (Ns)" affordance).
 */
export function ThinkingIndicator({
  label = "Thinking",
  className,
}: {
  label?: string;
  className?: string;
}) {
  const [elapsed, setElapsed] = React.useState(0);
  React.useEffect(() => {
    const start = Date.now();
    const id = setInterval(
      () => setElapsed(Math.floor((Date.now() - start) / 1000)),
      1000,
    );
    return () => clearInterval(id);
  }, []);
  return (
    <span
      className={cn(
        "text-muted-foreground inline-flex items-center gap-1.5 py-0.5 text-xs",
        className,
      )}
      aria-live="polite"
      aria-label={`Agent is working, ${elapsed} seconds elapsed`}
    >
      <span className="inline-flex items-center gap-1">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="bg-muted-foreground/60 size-1.5 animate-bounce rounded-full motion-reduce:animate-none"
            style={{ animationDelay: `${i * 160}ms` }}
          />
        ))}
      </span>
      <span className="tabular-nums">
        {label}… {elapsed}s
      </span>
    </span>
  );
}
