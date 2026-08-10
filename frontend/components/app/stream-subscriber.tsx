"use client";

import * as React from "react";
import { useQueryClient } from "@tanstack/react-query";

import { cn } from "@/lib/utils";
import { qk } from "@/lib/api/query-keys";
import { StreamEvent } from "@/lib/schemas";
import { useSse } from "@/lib/stream/use-sse";
import { useUiStore } from "@/stores/ui-store";

/**
 * Workspace-wide SSE subscriber. Mounted once at the (app) layout.
 *  - Drives the connection-state pill.
 *  - Bumps the unread-notifications count on `hitl.pending`.
 *  - Invalidates query keys when artifacts / runs change so any open page
 *    refetches without manual reloads.
 *
 * Disabled in Storybook + tests via `NEXT_PUBLIC_DISABLE_STREAMS=1`.
 */
export function StreamSubscriber() {
  const queryClient = useQueryClient();
  const setConnectionState = useUiStore((s) => s.setConnectionState);
  const bumpUnread = useUiStore((s) => s.bumpUnread);

  const enabled = process.env.NEXT_PUBLIC_DISABLE_STREAMS !== "1";

  const handleEvent = React.useCallback(
    (raw: unknown) => {
      const parsed = StreamEvent.safeParse(raw);
      if (!parsed.success) return;
      const ev = parsed.data;
      switch (ev.type) {
        case "artifact.updated":
          // Wide invalidation — every page that lists artifacts refetches.
          // Cheap because TanStack only refetches *active* queries.
          queryClient.invalidateQueries({ queryKey: qk.artifacts.all() });
          queryClient.invalidateQueries({ queryKey: qk.runs.all() });
          break;
        case "run.completed":
        case "run.started":
          queryClient.invalidateQueries({ queryKey: qk.runs.all() });
          break;
        case "hitl.pending":
          queryClient.invalidateQueries({ queryKey: qk.runs.all() });
          bumpUnread();
          break;
        case "cost.update":
          // Light-touch — only refresh runs since cost lives there
          queryClient.invalidateQueries({ queryKey: qk.runs.all() });
          break;
        default:
          // step.progress / step.output.delta / guardrail.hit are per-run
          // and consumed by `<RunDetailDrawer />` directly via useRunStream
          // (Chunk 16 follow-up). Ignore at the workspace level.
          break;
      }
    },
    [queryClient, bumpUnread],
  );

  useSse({
    url: enabled ? "/api/stream" : null,
    onEvent: handleEvent,
    onState: setConnectionState,
    maxAttempts: 10,
  });

  return null;
}

// ── Per-run stream feed (northstar live stream panel) ─────────────────────

/**
 * A single stream row in the elevated northstar style:
 * mono timestamp + brand/info/success dot + body text.
 */
export interface StreamRowData {
  id: string;
  at: string;
  /** Dot color tone: "brand" | "info" | "success" | "warning" | "danger" */
  tone: "brand" | "info" | "success" | "warning" | "danger";
  body: React.ReactNode;
}

const TONE_DOT: Record<StreamRowData["tone"], string> = {
  brand: "bg-brand-bright",
  info: "bg-info",
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-destructive",
};

export function StreamRow({ row }: { row: StreamRowData }) {
  return (
    <div className="flex animate-[rise_0.5s_ease_both] gap-2.5 border-b border-line-soft px-4 py-2.5 text-xs last:border-b-0">
      <span className="shrink-0 pt-0.5 font-mono text-[10.5px] text-muted-foreground">
        {new Date(row.at).toLocaleTimeString(undefined, {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        })}
      </span>
      <span
        className={cn("mt-1.5 size-1.5 shrink-0 rounded-full", TONE_DOT[row.tone])}
        aria-hidden
      />
      <span className="min-w-0 flex-1 text-muted-foreground">{row.body}</span>
    </div>
  );
}

/**
 * Per-run SSE feed panel. Subscribes to `/api/runs/[id]/stream` when enabled.
 * Accumulates up to `maxRows` rows for display in the drawer.
 */
export interface RunStreamFeedProps {
  runId: string;
  maxRows?: number;
  className?: string;
}

export function RunStreamFeed({ runId, maxRows = 8, className }: RunStreamFeedProps) {
  const enabled = process.env.NEXT_PUBLIC_DISABLE_STREAMS !== "1";
  const [rows, setRows] = React.useState<StreamRowData[]>([]);
  const [feedState, setFeedState] = React.useState<"idle" | "connecting" | "connected" | "reconnecting" | "offline">("idle");

  const handleEvent = React.useCallback(
    (raw: unknown) => {
      const parsed = StreamEvent.safeParse(raw);
      if (!parsed.success) return;
      const ev = parsed.data;

      let tone: StreamRowData["tone"] = "info";
      let body: React.ReactNode = null;

      switch (ev.type) {
        case "step.progress":
          tone = "brand";
          body = (
            <>
              <strong className="font-semibold text-foreground">{ev.title}</strong>
              {" "}
              <span>{ev.status}</span>
            </>
          );
          break;
        case "step.output.delta":
          tone = "info";
          body = (
            <>
              <strong className="font-semibold text-foreground">output</strong>
              {" "}
              <span className="truncate opacity-70">{ev.delta.slice(0, 80)}</span>
            </>
          );
          break;
        case "artifact.updated":
          tone = "success";
          body = (
            <>
              <strong className="font-semibold text-foreground">artifact</strong>
              {" "}
              <span>updated</span>
            </>
          );
          break;
        case "guardrail.hit":
          tone = ev.blocked ? "danger" : "warning";
          body = (
            <>
              <strong className="font-semibold text-foreground">guardrail</strong>
              {" "}
              <span>{ev.kind}: {ev.message}</span>
            </>
          );
          break;
        case "run.completed":
          tone = ev.status === "failed" || ev.status === "rejected" ? "danger" : "success";
          body = (
            <>
              <strong className="font-semibold text-foreground">run</strong>
              {" "}
              <span>completed · {ev.status}</span>
            </>
          );
          break;
        case "cost.update":
          tone = "info";
          body = <span className="font-mono">cost update</span>;
          break;
        case "hitl.pending":
          tone = "warning";
          body = (
            <>
              <strong className="font-semibold text-foreground">approval gate</strong>
              {" "}
              <span>{ev.title}</span>
            </>
          );
          break;
        default:
          return;
      }

      const newRow: StreamRowData = {
        id: `${ev.type}-${ev.at}-${Math.random()}`,
        at: ev.at,
        tone,
        body,
      };

      setRows((prev) => [newRow, ...prev].slice(0, maxRows));
    },
    [maxRows],
  );

  useSse({
    url: enabled ? `/api/runs/${encodeURIComponent(runId)}/stream` : null,
    onEvent: handleEvent,
    onState: setFeedState,
    maxAttempts: 5,
  });

  const isLive = feedState === "connected";

  return (
    <div className={cn("flex flex-col", className)}>
      <div className="flex items-center gap-2 border-b border-line-soft px-4 py-2.5">
        {/* pulse-red live dot */}
        {isLive && (
          <span
            className="size-[7px] animate-[pulse-red_1.5s_infinite] rounded-full bg-destructive"
            aria-hidden
          />
        )}
        <span className="font-display text-[13px] font-bold">Live stream</span>
        <span className="ml-auto font-mono text-[10.5px] text-muted-foreground">
          SSE · /api/runs/stream
        </span>
      </div>

      {rows.length === 0 ? (
        <div className="px-4 py-4 text-center font-mono text-[11px] text-muted-foreground">
          {enabled
            ? feedState === "idle" || feedState === "connecting"
              ? "Connecting to stream…"
              : "Waiting for events…"
            : "Streams disabled (NEXT_PUBLIC_DISABLE_STREAMS=1)"}
        </div>
      ) : (
        <div>
          {rows.map((row) => (
            <StreamRow key={row.id} row={row} />
          ))}
        </div>
      )}
    </div>
  );
}
