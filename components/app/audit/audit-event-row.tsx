"use client";

import * as React from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { formatDistanceToNow } from "date-fns";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import type { RunAuditEvent } from "@/lib/api/audit";

// ── Semantic color mapping per UI-SPEC §Color ──────────────────────────────

type BadgeClass = string;

const EVENT_TYPE_BADGE: Record<string, BadgeClass> = {
  artifact_write: "border-success/30 bg-success/10 text-success",
  hitl_approval: "border-success/30 bg-success/10 text-success",
  tool_call_start: "border-warning/30 bg-warning/10 text-warning",
  tool_call_end: "border-warning/30 bg-warning/10 text-warning",
  model_call: "border-warning/30 bg-warning/10 text-warning",
  connector_call: "border-info/30 bg-info/10 text-info",
  hitl_rejection: "border-destructive/30 bg-destructive/10 text-destructive",
};

function getEventBadgeClass(eventType: string): BadgeClass {
  return (
    EVENT_TYPE_BADGE[eventType] ??
    "border-muted-foreground/30 bg-muted text-muted-foreground"
  );
}

export interface AuditEventRowProps {
  event: RunAuditEvent;
}

/**
 * A single row in the audit event list.
 * Layout per UI-SPEC Contract 2: EventTypeBadge | actor_id | timestamp | chevron
 * Second line: payload snippet (font-mono 11px, truncated).
 * Click-to-expand shows full JSON payload in a <pre> block.
 */
export function AuditEventRow({ event }: AuditEventRowProps) {
  const [expanded, setExpanded] = React.useState(false);

  const relativeTime = React.useMemo(
    () =>
      formatDistanceToNow(new Date(event.created_at), { addSuffix: true }),
    [event.created_at],
  );

  const payloadSnippet = React.useMemo(() => {
    if (!event.payload) return null;
    return JSON.stringify(event.payload).slice(0, 120);
  }, [event.payload]);

  const payloadFull = React.useMemo(() => {
    if (!event.payload) return null;
    return JSON.stringify(event.payload, null, 2);
  }, [event.payload]);

  return (
    <li role="listitem" className="border-b border-line-soft last:border-0">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className={cn(
          "w-full text-left transition-colors hover:bg-surface-1",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
        )}
      >
        {/* ── Primary row: badge | actor | timestamp | chevron ── */}
        <div className="flex min-h-[44px] items-center gap-3 py-3 px-4">
          {/* EventTypeBadge — 100px fixed, semantic colors */}
          <Badge
            variant="outline"
            aria-label={event.event_type}
            className={cn(
              "w-[100px] shrink-0 truncate text-xs font-semibold",
              getEventBadgeClass(event.event_type),
            )}
          >
            {event.event_type}
          </Badge>

          {/* actor_id — truncated */}
          <span className="max-w-[160px] flex-1 truncate text-sm text-foreground">
            {event.actor_id ?? "system"}
          </span>

          {/* relative timestamp */}
          <span className="w-[100px] shrink-0 text-right font-mono text-xs text-muted-foreground tabular-nums">
            {relativeTime}
          </span>

          {/* expand chevron */}
          <span className="ml-1 shrink-0 text-muted-foreground" aria-hidden>
            {expanded ? (
              <ChevronDown className="size-4" />
            ) : (
              <ChevronRight className="size-4" />
            )}
          </span>
        </div>

        {/* ── Payload snippet (second line) ── */}
        {payloadSnippet && (
          <div className="px-4 pb-2 font-mono text-[11px] text-muted-foreground truncate">
            {payloadSnippet}
          </div>
        )}
      </button>

      {/* ── Expanded payload block ── */}
      {expanded && payloadFull && (
        <pre className="mx-4 mb-3 overflow-x-auto rounded bg-muted p-3 font-mono text-[11px] text-muted-foreground whitespace-pre-wrap">
          {payloadFull}
        </pre>
      )}
    </li>
  );
}
