"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { Bot, Download, ScrollText, User } from "lucide-react";
import { formatDistanceToNow } from "date-fns";

import { PageTitle } from "@/components/app/page-title";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { LoadingState } from "@/components/ui/loading-state";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ActivityTabs } from "@/components/app/activity-tabs";
import { RestrictedAccess } from "@/components/auth/restricted-access";
import { ScopeChip } from "@/components/app/scope-indicator";
import { useAccessScope } from "@/hooks/use-access-scope";
import { ApiErrorState } from "@/components/feedback/api-error-state";
import { useSession } from "@/hooks/use-session";
import { hasPermission } from "@/lib/auth/permissions";
import { listAuditEvents } from "@/lib/api/audit";
import { qk } from "@/lib/api/query-keys";
import { downloadCsv, downloadJson } from "@/lib/export";
import type { AuditAction, AuditEvent } from "@/lib/schemas";

const PAGE_SIZE = 50;

const ACTION_VALUES: AuditAction[] = [
  "project.created",
  "project.archived",
  "run.started",
  "run.completed",
  "run.failed",
  "run.approved",
  "run.rejected",
  "artifact.created",
  "artifact.updated",
  "connector.installed",
  "connector.revoked",
  "user.invited",
  "user.removed",
  "settings.updated",
];

const ACTION_TONE: Partial<Record<AuditAction, string>> = {
  "run.failed": "text-destructive",
  "run.rejected": "text-destructive",
  "run.approved": "text-success",
  "run.completed": "text-success",
  "connector.installed": "text-success",
  "connector.revoked": "text-destructive",
};

/** Dot color per action tone — maps to inline style-free token classes. */
const ACTION_DOT: Partial<Record<AuditAction, string>> = {
  "run.failed": "bg-destructive",
  "run.rejected": "bg-destructive",
  "run.approved": "bg-success",
  "run.completed": "bg-success",
  "connector.installed": "bg-success",
  "connector.revoked": "bg-destructive",
};

export default function AuditPage() {
  const session = useSession({ required: true });
  // Phase 6: gate on audit:view (matrix) instead of legacy role === "admin".
  // security_auditor and admin both hold audit:view; delivery_lead and below do not.
  if (!hasPermission(session, "audit:view")) {
    return (
      <RestrictedAccess description="The audit log requires the audit:view permission. Ask your admin for access." />
    );
  }
  return <AuditPageInner />;
}

function AuditPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { scope, level, isOrgWide } = useAccessScope();

  const actionFilter = (searchParams.get("action") ?? "all") as "all" | AuditAction;
  const actorFilter = searchParams.get("actor") ?? "all";
  const search = searchParams.get("q") ?? "";
  const page = Number(searchParams.get("page") ?? "1");

  const updateParams = React.useCallback(
    (patch: Record<string, string | number | undefined>) => {
      const next = new URLSearchParams(searchParams);
      for (const [k, v] of Object.entries(patch)) {
        if (v === undefined || v === "" || v === "all" || String(v) === "1") {
          next.delete(k);
        } else {
          next.set(k, String(v));
        }
      }
      router.replace(`/audit?${next.toString()}`);
    },
    [router, searchParams],
  );

  // listAuditEvents query — preserved from original (BFF-backed)
  const auditQ = useQuery({
    queryKey: qk.audit.list({ action: actionFilter, page }),
    queryFn: () =>
      listAuditEvents({
        action: actionFilter === "all" ? undefined : actionFilter,
        page,
        pageSize: PAGE_SIZE,
      }),
    placeholderData: (prev) => prev,
  });

  const items = React.useMemo(() => {
    const base = auditQ.data?.items ?? [];
    let next = base;
    if (actorFilter !== "all") {
      next = next.filter((e) =>
        actorFilter === "agent"
          ? e.actor.id === "agent"
          : actorFilter === "system"
            ? e.actor.id === "system"
            : e.actor.id === actorFilter,
      );
    }
    if (search.trim()) {
      const q = search.toLowerCase();
      next = next.filter(
        (e) =>
          e.actor.name.toLowerCase().includes(q) ||
          e.action.toLowerCase().includes(q) ||
          e.resource.type.toLowerCase().includes(q) ||
          e.resource.id.toLowerCase().includes(q) ||
          (e.resource.name ?? "").toLowerCase().includes(q),
      );
    }
    return next;
  }, [auditQ.data, actorFilter, search]);

  const [selected, setSelected] = React.useState<AuditEvent | null>(null);

  const exportCsv = () => {
    downloadCsv(
      `audit-${new Date().toISOString().slice(0, 10)}.csv`,
      items.map((e) => ({
        id: e.id,
        at: e.at,
        actor_id: e.actor.id,
        actor_name: e.actor.name,
        action: e.action,
        resource_type: e.resource.type,
        resource_id: e.resource.id,
        resource_name: e.resource.name ?? "",
        project_id: e.projectId ?? "",
        ip: e.ip ?? "",
        detail: e.detail ? JSON.stringify(e.detail) : "",
      })),
      [
        { key: "id", header: "event_id" },
        { key: "at", header: "at" },
        { key: "actor_id", header: "actor_id" },
        { key: "actor_name", header: "actor_name" },
        { key: "action", header: "action" },
        { key: "resource_type", header: "resource_type" },
        { key: "resource_id", header: "resource_id" },
        { key: "resource_name", header: "resource_name" },
        { key: "project_id", header: "project_id" },
        { key: "ip", header: "ip" },
        { key: "detail", header: "detail" },
      ],
    );
    toast.success(`Exported ${items.length} events`);
  };

  const exportJson = () => {
    downloadJson(`audit-${new Date().toISOString().slice(0, 10)}.json`, items);
    toast.success(`Exported ${items.length} events`);
  };

  const pagination = auditQ.data?.pagination;
  const totalPages = pagination
    ? Math.max(1, Math.ceil(pagination.total / pagination.pageSize))
    : 1;

  return (
    <div className="w-full space-y-6 p-4 md:px-10 md:py-8">
      {/* Editorial page header — elevated audit surface */}
      <header
        className="flex flex-col items-start justify-between gap-4 sm:flex-row sm:items-end"
        style={{
          animationName: "rise",
          animationDuration: "0.6s",
          animationTimingFunction: "cubic-bezier(0.2, 0.7, 0.2, 1)",
          animationFillMode: "both",
        }}
      >
        <div>
          <PageTitle>Activity</PageTitle>

          <div className="flex flex-wrap items-center gap-2">
            {scope !== null && (
              <ScopeChip kind={isOrgWide ? "organization" : level} size="sm" />
            )}
            {/* The count survives the prose around it: it is the state of the
                list, not a description of the page. The chip beside it carries
                what the sentence used to — for a scoped viewer the trail is
                filtered to their projects (app/api/audit/route.ts), and
                overstating its completeness in an audit context is exactly the
                wrong error to make. */}
            {pagination && (
              <span className="text-muted-foreground font-mono text-[11.5px]">
                {pagination.total} events · page {pagination.page} of {totalPages}
              </span>
            )}
          </div>
        </div>
        {/* Export actions */}
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={exportCsv}
            disabled={items.length === 0}
            className="border-line-soft"
          >
            <Download className="size-4" aria-hidden />
            CSV
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={exportJson}
            disabled={items.length === 0}
            className="border-line-soft"
          >
            <Download className="size-4" aria-hidden />
            JSON
          </Button>
        </div>
      </header>

      <ActivityTabs />

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2">
        <Input
          value={search}
          onChange={(e) => updateParams({ q: e.target.value || undefined, page: undefined })}
          placeholder="Search actor, action, resource…"
          className="border-line-soft h-9 w-64"
          aria-label="Search audit events"
        />
        <Select
          value={actionFilter}
          onValueChange={(v) => updateParams({ action: v, page: undefined })}
        >
          <SelectTrigger className="border-line-soft h-9 w-56" aria-label="Filter by action">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All actions</SelectItem>
            {ACTION_VALUES.map((a) => (
              <SelectItem key={a} value={a} className="font-mono text-xs">
                {a}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={actorFilter}
          onValueChange={(v) => updateParams({ actor: v, page: undefined })}
        >
          <SelectTrigger className="border-line-soft h-9 w-40" aria-label="Filter by actor">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Any actor</SelectItem>
            <SelectItem value="agent">Agent</SelectItem>
            <SelectItem value="system">System</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Body */}
      {auditQ.isError ? (
        <ApiErrorState
          title="Couldn't load audit events"
          error={
            auditQ.error && "code" in auditQ.error && "message" in auditQ.error
              ? (auditQ.error as { code: string; message: string; requestId?: string })
              : undefined
          }
          description={
            !(auditQ.error && "code" in auditQ.error)
              ? auditQ.error instanceof Error
                ? auditQ.error.message
                : "Unknown error."
              : undefined
          }
          onRetry={() => auditQ.refetch()}
        />
      ) : auditQ.isLoading ? (
        <LoadingState variant="table" rows={8} />
      ) : items.length === 0 ? (
        <EmptyState
          icon={ScrollText}
          title="No events match"
          description="Clear a filter or widen the search window."
        />
      ) : (
        /* Elevated audit timeline — stream-row style from northstar */
        <div
          className="border-line-soft bg-panel-elevated overflow-hidden rounded-xl border shadow-[0_1px_0_oklch(1_0_0_/_0.04)_inset,0_8px_20px_-8px_oklch(0_0_0_/_0.35)]"
          style={{
            animationName: "rise",
            animationDuration: "0.5s",
            animationTimingFunction: "cubic-bezier(0.2, 0.7, 0.2, 1)",
            animationFillMode: "both",
            animationDelay: "0.05s",
          }}
        >
          {/* Timeline header */}
          <div className="border-line-soft flex items-center gap-2 border-b px-5 py-3.5">
            <span className="font-display text-[13.5px] font-bold tracking-[-0.01em]">Events</span>
            <span className="text-muted-foreground font-mono text-[10.5px]">
              {items.length} shown
            </span>
          </div>

          {/* Event rows */}
          <ol aria-label="Audit events timeline">
            {items.map((e) => (
              <AuditEventRow key={e.id} event={e} onClick={() => setSelected(e)} />
            ))}
          </ol>
        </div>
      )}

      {pagination && pagination.total > pagination.pageSize && (
        <nav
          aria-label="Pagination"
          className="border-line-soft text-muted-foreground flex items-center justify-between border-t pt-4 text-sm"
        >
          <span className="font-mono text-xs">
            Showing {(pagination.page - 1) * pagination.pageSize + 1}–
            {Math.min(pagination.page * pagination.pageSize, pagination.total)} of{" "}
            {pagination.total}
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={pagination.page <= 1}
              onClick={() => updateParams({ page: pagination.page - 1 })}
              className="border-line-soft"
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={pagination.page >= totalPages}
              onClick={() => updateParams({ page: pagination.page + 1 })}
              className="border-line-soft"
            >
              Next
            </Button>
          </div>
        </nav>
      )}

      {/* Detail drawer — opens on row click */}
      {selected && <AuditDetail event={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}

// ───────── Elevated audit event row (northstar stream-row style) ─────────

function AuditEventRow({ event, onClick }: { event: AuditEvent; onClick: () => void }) {
  const dotClass = ACTION_DOT[event.action] ?? "bg-muted-foreground";
  const toneClass = ACTION_TONE[event.action] ?? "text-foreground";
  const relativeTime = formatDistanceToNow(new Date(event.at), { addSuffix: true });
  const isoTime = new Date(event.at).toISOString().replace("T", " ").slice(0, 19);

  return (
    <li>
      <button
        type="button"
        className="group border-line-soft hover:bg-surface-1 focus-visible:ring-ring flex w-full items-start gap-3 border-b px-5 py-3 text-left transition-colors last:border-b-0 focus-visible:ring-2 focus-visible:outline-none focus-visible:ring-inset"
        onClick={onClick}
        aria-label={`View details for ${event.id}`}
      >
        {/* Mono timestamp */}
        <span
          className="text-muted-foreground w-[7ch] shrink-0 pt-0.5 font-mono text-[10.5px] tabular-nums"
          title={isoTime}
        >
          {relativeTime.replace(" ago", "").replace("about ", "~")}
        </span>

        {/* Status dot */}
        <span className={cn("mt-1.5 size-[6px] shrink-0 rounded-full", dotClass)} aria-hidden />

        {/* Content */}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            {/* Actor icon + name */}
            <span className="inline-flex items-center gap-1 text-sm font-semibold">
              {event.actor.id === "agent" ? (
                <Bot className="text-muted-foreground size-3.5" aria-hidden />
              ) : event.actor.id === "system" ? (
                <ScrollText className="text-muted-foreground size-3.5" aria-hidden />
              ) : (
                <User className="text-muted-foreground size-3.5" aria-hidden />
              )}
              {event.actor.name}
            </span>

            {/* Action badge — elevated font-mono tone */}
            <Badge variant="outline" className={cn("font-mono text-[10px]", toneClass)}>
              {event.action}
            </Badge>

            {/* Resource */}
            <span className="text-muted-foreground font-mono text-[11px]">
              <span className="text-foreground font-semibold">{event.resource.type}</span>{" "}
              {event.resource.id}
              {event.resource.name ? ` · ${event.resource.name}` : ""}
            </span>
          </div>
        </div>

        {/* Project — right-aligned mono */}
        {event.projectId && (
          <span className="text-muted-foreground shrink-0 font-mono text-[10.5px]">
            {event.projectId}
          </span>
        )}
      </button>
    </li>
  );
}

// ───────── Detail modal (unchanged logic, elevated chrome) ─────────

function AuditDetail({ event, onClose }: { event: AuditEvent; onClose: () => void }) {
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Audit event details"
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 p-4 sm:items-center"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="bg-panel-elevated border-line-soft w-full max-w-lg overflow-hidden rounded-xl border shadow-[0_24px_60px_-12px_oklch(0_0_0_/_0.7)]">
        <header className="border-line-soft flex items-start justify-between gap-3 border-b px-5 py-4">
          <div>
            <h2 className="font-display text-base font-bold tracking-[-0.01em]">{event.action}</h2>
            <p className="text-muted-foreground font-mono text-[11px]">{event.id}</p>
          </div>
          <Button variant="ghost" size="sm" onClick={onClose} className="shrink-0">
            Close
          </Button>
        </header>
        <dl className="grid gap-2 p-5 text-sm">
          <DetailRow label="When" value={new Date(event.at).toLocaleString()} />
          <DetailRow label="Actor" value={`${event.actor.name} (${event.actor.id})`} />
          <DetailRow
            label="Resource"
            value={`${event.resource.type} · ${event.resource.id}${
              event.resource.name ? ` · ${event.resource.name}` : ""
            }`}
          />
          {event.projectId && <DetailRow label="Project" value={event.projectId} />}
          {event.ip && <DetailRow label="IP" value={event.ip} />}
          {event.detail && (
            <div className="mt-2">
              <dt className="text-muted-foreground mb-1 font-mono text-[11px] tracking-widest uppercase">
                detail
              </dt>
              <pre className="border-line-soft bg-surface-2 overflow-auto rounded-lg border p-3 font-mono text-[11px]">
                {JSON.stringify(event.detail, null, 2)}
              </pre>
            </div>
          )}
        </dl>
      </div>
    </div>
  );
}

function DetailRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline gap-3">
      <dt className="text-muted-foreground w-20 shrink-0 font-mono text-[11px] tracking-widest uppercase">
        {label}
      </dt>
      <dd className="min-w-0 flex-1 font-mono text-[12px]">{value}</dd>
    </div>
  );
}
