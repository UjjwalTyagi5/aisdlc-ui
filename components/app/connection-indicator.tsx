"use client";

import * as React from "react";
import { Loader2, Radio, Wifi, WifiOff } from "lucide-react";

import { cn } from "@/lib/utils";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useUiStore } from "@/stores/ui-store";
import type { SseState } from "@/lib/stream/use-sse";

const CONFIG: Record<
  SseState,
  {
    label: string;
    dotClass: string;
    pillClass: string;
    icon: React.ComponentType<{ className?: string }>;
    description: string;
  }
> = {
  idle: {
    label: "Idle",
    dotClass: "bg-muted-foreground/40",
    pillClass: "text-muted-foreground bg-muted",
    icon: Radio,
    description: "Not listening.",
  },
  connecting: {
    label: "Connecting",
    dotClass: "bg-info animate-spin",
    pillClass: "text-info bg-info/10",
    icon: Loader2,
    description: `Opening the ${BUSINESS_UNIT_LABEL.toLowerCase()} event stream…`,
  },
  connected: {
    label: "Live",
    // pulse keyframe from Plan-01 globals.css (reduced-motion-safe)
    dotClass: "bg-success animate-[pulse_2s_ease-in-out_infinite]",
    pillClass: "text-success bg-success/10",
    icon: Wifi,
    description: "Live updates streaming from the platform.",
  },
  reconnecting: {
    label: "Reconnecting",
    dotClass: "bg-warning",
    pillClass: "text-warning bg-warning/15",
    icon: Loader2,
    description: "Connection dropped — retrying with backoff.",
  },
  offline: {
    label: "Offline",
    dotClass: "bg-destructive",
    pillClass: "text-destructive bg-destructive/10",
    icon: WifiOff,
    description: "Too many retries. Live updates paused.",
  },
};

export function ConnectionIndicator({ className }: { className?: string }) {
  const state = useUiStore((s) => s.connectionState);
  const cfg = CONFIG[state];
  const Icon = cfg.icon;
  const spinning = state === "connecting" || state === "reconnecting";

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          role="status"
          aria-live="polite"
          aria-label={`Stream ${cfg.label.toLowerCase()}`}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider",
            cfg.pillClass,
            className,
          )}
          data-state={state}
        >
          {/* Elevated pulse dot */}
          <span
            className={cn("size-[7px] shrink-0 rounded-full", cfg.dotClass)}
            aria-hidden
          />
          <Icon className={cn("size-3", spinning && "animate-spin")} aria-hidden />
          <span className="hidden md:inline">{cfg.label}</span>
        </span>
      </TooltipTrigger>
      <TooltipContent>{cfg.description}</TooltipContent>
    </Tooltip>
  );
}
