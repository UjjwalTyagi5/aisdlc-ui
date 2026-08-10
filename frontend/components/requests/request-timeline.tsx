"use client";

import * as React from "react";
import { format } from "date-fns";
import {
  ArrowUpCircle,
  CheckCircle2,
  CircleDot,
  FilePlus2,
  MessageSquare,
  Send,
  UserCheck,
  XCircle,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { ROLE_META, type PlatformRole } from "@/lib/roles";
import type { RequestEventKind, RequestTimelineEvent } from "@/lib/schemas/governance-approval";

/**
 * Icon and tint per event kind.
 *
 * Approved/rejected are the only two that take a status colour, and they take
 * the platform's own success/destructive tokens rather than chart hues —
 * these mean a state, which is exactly what status colour is reserved for.
 * Everything else stays muted so the decision is the thing the eye lands on in
 * a trail that may be a dozen rows long.
 */
const EVENT_META: Record<
  RequestEventKind,
  { icon: React.ComponentType<{ className?: string }>; tint: string; verb: string }
> = {
  created: { icon: FilePlus2, tint: "text-muted-foreground", verb: "created the request" },
  submitted: { icon: Send, tint: "text-muted-foreground", verb: "submitted it" },
  assigned: { icon: UserCheck, tint: "text-muted-foreground", verb: "routed it" },
  commented: { icon: MessageSquare, tint: "text-muted-foreground", verb: "commented" },
  approved: { icon: CheckCircle2, tint: "text-success", verb: "approved it" },
  rejected: { icon: XCircle, tint: "text-destructive", verb: "rejected it" },
  escalated: { icon: ArrowUpCircle, tint: "text-warning", verb: "escalated it" },
  cancelled: { icon: CircleDot, tint: "text-muted-foreground", verb: "withdrew it" },
};

function roleLabel(role: string | null | undefined): string | null {
  if (!role) return null;
  return ROLE_META[role as PlatformRole]?.label ?? role;
}

/**
 * The request's audit trail, oldest first.
 *
 * Chronological rather than newest-first: this is a story about how a decision
 * was reached, and a reader following "who had it, and what did they do"
 * needs it in the order it happened. Newest-first suits a feed you dip into;
 * this is read end to end or not at all.
 *
 * Append-only upstream — nothing here can edit or hide an entry, which is the
 * only property that makes it worth calling a trail (PRD FR-05).
 */
export function RequestTimeline({
  events,
  className,
}: {
  events: RequestTimelineEvent[];
  className?: string;
}) {
  if (events.length === 0) {
    return (
      <p className={cn("text-muted-foreground text-[12.5px]", className)}>
        No activity recorded yet.
      </p>
    );
  }

  return (
    <ol className={cn("relative space-y-0", className)}>
      {events.map((e, i) => {
        const meta = EVENT_META[e.kind];
        const Icon = meta.icon;
        const last = i === events.length - 1;
        const actorRole = roleLabel(e.actorRole);
        const toRole = roleLabel(e.toRole);

        return (
          <li key={e.id} className="relative flex gap-3 pb-4 last:pb-0">
            {/* The spine. Stops at the last node rather than trailing into
                empty space below it. */}
            {!last && (
              <span
                className="bg-line-soft absolute top-6 bottom-0 left-[11px] w-px"
                aria-hidden
              />
            )}
            <span
              className={cn(
                "border-line-soft bg-panel-elevated relative z-10 grid size-6 shrink-0 place-items-center rounded-full border",
                meta.tint,
              )}
            >
              <Icon className="size-3.5" aria-hidden />
            </span>

            <div className="min-w-0 flex-1 pt-0.5">
              <p className="text-[12.5px] leading-snug">
                <span className="font-medium">{e.actor}</span>
                {actorRole && (
                  <span className="text-muted-foreground"> ({actorRole})</span>
                )}{" "}
                <span className="text-muted-foreground">{meta.verb}</span>
                {toRole && (
                  <>
                    <span className="text-muted-foreground"> to the </span>
                    <span className="font-medium">{toRole}</span>
                  </>
                )}
              </p>
              {e.note && (
                <p className="border-line-soft text-muted-foreground mt-1 border-l-2 pl-2 text-[12px] italic">
                  {e.note}
                </p>
              )}
              <p className="text-muted-foreground mt-0.5 font-mono text-[10.5px]">
                {format(new Date(e.at), "d MMM yyyy, HH:mm")}
              </p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
