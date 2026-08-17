"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { ClipboardList, Download } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Progress } from "@/components/ui/progress";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";

import { getRunAudit, type RunAuditEvent } from "@/lib/api/audit";
import { requestEvidence, pollEvidenceStatus } from "@/lib/api/evidence";
import { ApiRequestError } from "@/lib/api/client";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";

import { AuditEventRow } from "./audit-event-row";

// ── Evidence export state machine ─────────────────────────────────────────

type ExportState = "idle" | "generating" | "ready" | "error";

// ── Filter state ──────────────────────────────────────────────────────────

interface Filters {
  agent: string;
  event_type: string;
  actor: string;
  since: string;
  until: string;
}

const INITIAL_FILTERS: Filters = {
  agent: "",
  event_type: "",
  actor: "",
  since: "",
  until: "",
};

// ── Component ─────────────────────────────────────────────────────────────

export interface AuditTabProps {
  runId: string;
}

/**
 * AuditTab — filterable, cursor-paginated audit event list + evidence export.
 * Implements UI-SPEC Contracts 2-5 (event list, evidence export, empty/error states).
 */
export function AuditTab({ runId }: AuditTabProps) {
  const [filters, setFilters] = React.useState<Filters>(INITIAL_FILTERS);
  const [debouncedActor, setDebouncedActor] = React.useState("");
  const [cursor, setCursor] = React.useState<string | undefined>(undefined);
  const [allEvents, setAllEvents] = React.useState<RunAuditEvent[]>([]);

  // Evidence export state
  const [exportState, setExportState] = React.useState<ExportState>("idle");
  const [jobId, setJobId] = React.useState<string | null>(null);
  const [downloadUrl, setDownloadUrl] = React.useState<string | null>(null);
  const [_exportError, setExportError] = React.useState<string | null>(null);

  // ── Debounce actor input (300ms) ──────────────────────────────────────
  React.useEffect(() => {
    const t = setTimeout(() => setDebouncedActor(filters.actor), 300);
    return () => clearTimeout(t);
  }, [filters.actor]);

  // Reset accumulated events when filters change
  const queryParams = React.useMemo(
    () => ({
      agent: filters.agent || undefined,
      event_type: filters.event_type || undefined,
      actor: debouncedActor || undefined,
      since: filters.since || undefined,
      until: filters.until || undefined,
      cursor: undefined, // first page on filter change
    }),
    [filters.agent, filters.event_type, debouncedActor, filters.since, filters.until],
  );

  // Reset when filters change
  React.useEffect(() => {
    setCursor(undefined);
    setAllEvents([]);
  }, [queryParams]);

  // ── Audit events query ────────────────────────────────────────────────
  const auditQ = useQuery({
    queryKey: ["runs", "detail", runId, "audit", queryParams, cursor],
    queryFn: () => getRunAudit(runId, { ...queryParams, cursor }),
    staleTime: 30_000,
  });

  // Append new page results to accumulated list
  React.useEffect(() => {
    if (auditQ.data?.items) {
      if (!cursor) {
        // First page (or filter reset): replace
        setAllEvents(auditQ.data.items);
      } else {
        // Subsequent pages: append
        setAllEvents((prev) => {
          const existingIds = new Set(prev.map((e) => e.id));
          const newItems = auditQ.data!.items.filter((e) => !existingIds.has(e.id));
          return [...prev, ...newItems];
        });
      }
    }
    // cursor intentionally omitted: we only want to run when new data arrives
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auditQ.data]);

  const nextCursor = auditQ.data?.nextCursor ?? null;
  const hasMore = !!nextCursor;

  // ── Evidence export polling ────────────────────────────────────────────
  const statusQ = useQuery({
    queryKey: ["evidence-status", runId, jobId],
    queryFn: () => pollEvidenceStatus(runId, jobId!),
    enabled: exportState === "generating" && !!jobId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === "ready" || status === "failed") return false;
      return 2000;
    },
  });

  // React to status query transitions
  React.useEffect(() => {
    if (!statusQ.data) return;
    if (statusQ.data.status === "ready" && statusQ.data.download_url) {
      setDownloadUrl(statusQ.data.download_url);
      setExportState("ready");
    } else if (statusQ.data.status === "failed") {
      setExportState("error");
      setExportError("Evidence ZIP generation failed on the server.");
    }
  }, [statusQ.data]);

  // React to status query error
  React.useEffect(() => {
    if (statusQ.error) {
      setExportState("error");
      setExportError(
        statusQ.error instanceof Error ? statusQ.error.message : "Failed to poll evidence status.",
      );
    }
  }, [statusQ.error]);

  // Auto-download when ready + reset after 5s
  React.useEffect(() => {
    if (exportState === "ready" && downloadUrl) {
      window.open(downloadUrl, "_blank");
      const t = setTimeout(() => {
        setExportState("idle");
        setJobId(null);
        setDownloadUrl(null);
      }, 5000);
      return () => clearTimeout(t);
    }
  }, [exportState, downloadUrl]);

  // ── Handlers ──────────────────────────────────────────────────────────

  const handleExport = React.useCallback(async () => {
    if (exportState !== "idle" && exportState !== "error") return;
    setExportError(null);
    setExportState("generating");
    try {
      const { job_id } = await requestEvidence(runId);
      setJobId(job_id);
    } catch (err) {
      setExportState("error");
      setExportError(err instanceof Error ? err.message : "Failed to start evidence export.");
    }
  }, [exportState, runId]);

  const handleDownload = React.useCallback(() => {
    if (downloadUrl) {
      window.open(downloadUrl, "_blank");
    }
  }, [downloadUrl]);

  const handleLoadMore = React.useCallback(() => {
    if (nextCursor) {
      setCursor(nextCursor);
    }
  }, [nextCursor]);

  const handleRetryAudit = React.useCallback(() => {
    void auditQ.refetch();
  }, [auditQ]);

  // ── Render helpers ─────────────────────────────────────────────────────

  const is403 = auditQ.error instanceof ApiRequestError && auditQ.error.status === 403;

  const renderEventList = () => {
    if (auditQ.isLoading) {
      return (
        <ul role="list" className="divide-line-soft divide-y">
          {[0, 1, 2].map((i) => (
            <li key={i} className="flex items-center gap-3 px-4 py-3">
              <Skeleton className="h-[18px] w-[100px] shrink-0 rounded" />
              <Skeleton className="h-[18px] flex-1 rounded" />
              <Skeleton className="h-[18px] w-[80px] shrink-0 rounded" />
            </li>
          ))}
        </ul>
      );
    }

    if (auditQ.error) {
      if (is403) {
        return (
          <ErrorState
            title="Could not load audit trail"
            description={`You don't have permission to view the audit trail for this run. Contact your ${BUSINESS_UNIT_LABEL.toLowerCase()} admin.`}
            variant="card"
          />
        );
      }
      return (
        <ErrorState
          title="Could not load audit trail"
          description="The audit log failed to load. Check your connection and try again."
          detail={auditQ.error instanceof Error ? auditQ.error.message : undefined}
          onRetry={handleRetryAudit}
          retryLabel="Reload audit trail"
          variant="card"
        />
      );
    }

    if (allEvents.length === 0) {
      return (
        <EmptyState
          icon={ClipboardList}
          title="No audit events yet"
          description="Events appear here once the run starts emitting. Reload after the run advances."
          variant="card"
        />
      );
    }

    return (
      <>
        <ul role="list" className="divide-line-soft divide-y">
          {allEvents.map((event) => (
            <AuditEventRow key={event.id} event={event} />
          ))}
        </ul>

        {/* Load more button */}
        {hasMore && (
          <div className="flex justify-center px-4 py-3">
            <Button variant="ghost" size="sm" onClick={handleLoadMore} disabled={auditQ.isFetching}>
              {auditQ.isFetching ? "Loading…" : "Load more events"}
            </Button>
          </div>
        )}
      </>
    );
  };

  // ── Evidence export button ─────────────────────────────────────────────

  const renderExportButton = () => {
    switch (exportState) {
      case "generating":
        return (
          <Button variant="outline" size="sm" disabled>
            Generating…
          </Button>
        );
      case "ready":
        return (
          <Button variant="default" size="sm" onClick={handleDownload}>
            <Download className="size-3.5" aria-hidden />
            Download ZIP
          </Button>
        );
      case "error":
        return (
          <Button
            variant="destructive"
            size="sm"
            onClick={handleExport}
            className="border-destructive text-destructive hover:bg-destructive/10 border bg-transparent"
          >
            Export failed — retry
          </Button>
        );
      default:
        return (
          <Button variant="default" size="sm" onClick={handleExport}>
            Export Evidence
          </Button>
        );
    }
  };

  // ── Main render ────────────────────────────────────────────────────────

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {/* ── Filter bar + Export Evidence button ── */}
      <div className="border-line-soft bg-panel-elevated/30 flex flex-wrap items-center gap-2 border-b px-4 py-3">
        {/* Agent select */}
        <Select
          value={filters.agent}
          onValueChange={(v) => setFilters((f) => ({ ...f, agent: v === "_all" ? "" : v }))}
        >
          <SelectTrigger className="h-8 w-[130px] text-xs">
            <SelectValue placeholder="All agents" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="_all">All agents</SelectItem>
            <SelectItem value="requirements">requirements</SelectItem>
            <SelectItem value="design">design</SelectItem>
            <SelectItem value="development">development</SelectItem>
            <SelectItem value="testing">testing</SelectItem>
          </SelectContent>
        </Select>

        {/* Event type select */}
        <Select
          value={filters.event_type}
          onValueChange={(v) => setFilters((f) => ({ ...f, event_type: v === "_all" ? "" : v }))}
        >
          <SelectTrigger className="h-8 w-[150px] text-xs">
            <SelectValue placeholder="All types" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="_all">All types</SelectItem>
            <SelectItem value="tool_call_start">tool_call_start</SelectItem>
            <SelectItem value="tool_call_end">tool_call_end</SelectItem>
            <SelectItem value="model_call">model_call</SelectItem>
            <SelectItem value="artifact_write">artifact_write</SelectItem>
            <SelectItem value="connector_call">connector_call</SelectItem>
            <SelectItem value="hitl_approval">hitl_approval</SelectItem>
            <SelectItem value="hitl_rejection">hitl_rejection</SelectItem>
          </SelectContent>
        </Select>

        {/* Actor free-text input — 300ms debounced */}
        <Input
          value={filters.actor}
          onChange={(e) => setFilters((f) => ({ ...f, actor: e.target.value }))}
          placeholder="Filter by actor…"
          className="h-8 w-40 text-xs"
        />

        {/* Since datetime */}
        <Input
          type="datetime-local"
          value={filters.since}
          onChange={(e) => setFilters((f) => ({ ...f, since: e.target.value }))}
          className="h-8 w-[178px] text-xs"
        />

        {/* Until datetime */}
        <Input
          type="datetime-local"
          value={filters.until}
          onChange={(e) => setFilters((f) => ({ ...f, until: e.target.value }))}
          className="h-8 w-[178px] text-xs"
        />

        {/* Spacer */}
        <div className="flex-1" />

        {/* Export Evidence button (top-right, primary CTA) */}
        {renderExportButton()}
      </div>

      {/* ── Generating progress bar (indeterminate) ── */}
      {exportState === "generating" && (
        <Progress
          aria-label="Generating evidence export…"
          className="h-1 animate-pulse rounded-none"
        />
      )}

      {/* ── Scrollable event list ── */}
      <ScrollArea className="flex-1">
        <div className="py-2">{renderEventList()}</div>
      </ScrollArea>
    </div>
  );
}
