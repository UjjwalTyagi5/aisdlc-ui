"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Bot, ScrollText, User } from "lucide-react";
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
import { RestrictedAccess } from "@/components/auth/restricted-access";
import { ApiErrorState } from "@/components/feedback/api-error-state";
import { useSession } from "@/hooks/use-session";
import { hasPermission } from "@/lib/auth/permissions";
import { listOrgAuditEvents } from "@/lib/api/audit";
import { listWorkspaces } from "@/lib/api/workspaces";
import { qk } from "@/lib/api/query-keys";
import type { AuditAction, AuditEvent } from "@/lib/schemas";
import { BUSINESS_UNIT_LABEL, BUSINESS_UNIT_LABEL_PLURAL } from "@/lib/scope";

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

export default function OrgAuditPage() {
  const session = useSession({ required: true });
  if (!hasPermission(session, "admin:*")) {
    return (
      <RestrictedAccess description="The organisation audit log requires admin access." />
    );
  }
  return <OrgAuditPageInner />;
}

function OrgAuditPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const workspaceFilter = searchParams.get("workspace") ?? "all";
  const actionFilter = (searchParams.get("action") ?? "all") as "all" | AuditAction;
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
      router.replace(`/admin/audit?${next.toString()}`);
    },
    [router, searchParams],
  );

  const wsQ = useQuery({
    queryKey: qk.workspaces.list(),
    queryFn: listWorkspaces,
    staleTime: 120_000,
  });

  const auditQ = useQuery({
    queryKey: ["audit", "org", { workspace: workspaceFilter, action: actionFilter, page }],
    queryFn: () =>
      listOrgAuditEvents({
        workspaceId: workspaceFilter === "all" ? undefined : workspaceFilter,
        action: actionFilter === "all" ? undefined : actionFilter,
        page,
        pageSize: PAGE_SIZE,
      }),
    placeholderData: (prev) => prev,
  });

  const items = React.useMemo(() => {
    const base = auditQ.data?.items ?? [];
    if (!search.trim()) return base;
    const q = search.toLowerCase();
    return base.filter(
      (e) =>
        e.action.toLowerCase().includes(q) ||
        e.actor.name.toLowerCase().includes(q) ||
        JSON.stringify(e.detail ?? {}).toLowerCase().includes(q),
    );
  }, [auditQ.data, search]);

  const total = auditQ.data?.pagination.total ?? 0;
  const totalPages = Math.ceil(total / PAGE_SIZE) || 1;

  const workspaces = wsQ.data?.filter((w) => w.status === "active") ?? [];

  return (
    <div className="w-full space-y-6 p-4 md:px-10 md:py-8">
      <PageTitle>Audit log</PageTitle>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        {/* Business Unit picker */}
        <Select
          value={workspaceFilter}
          onValueChange={(v) => updateParams({ workspace: v, page: undefined })}
        >
          <SelectTrigger className="border-line-soft h-8 w-52 font-mono text-[12px]">
            <SelectValue placeholder={`All ${BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}`} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}</SelectItem>
            {workspaces.map((ws) => (
              <SelectItem key={ws.id} value={ws.id}>
                {ws.displayName}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {/* Action filter */}
        <Select
          value={actionFilter}
          onValueChange={(v) => updateParams({ action: v, page: undefined })}
        >
          <SelectTrigger className="border-line-soft h-8 w-44 font-mono text-[12px]">
            <SelectValue placeholder="All actions" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All actions</SelectItem>
            {ACTION_VALUES.map((a) => (
              <SelectItem key={a} value={a}>
                {a}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {/* Search */}
        <Input
          value={search}
          onChange={(e) => updateParams({ q: e.target.value, page: undefined })}
          placeholder="Search events…"
          className="border-line-soft h-8 w-60 font-mono text-[12px]"
        />

        {(workspaceFilter !== "all" || actionFilter !== "all" || search) && (
          <Button
            variant="ghost"
            size="sm"
            className="text-muted-foreground h-8 font-mono text-[12px]"
            onClick={() => router.replace("/admin/audit")}
          >
            Clear filters
          </Button>
        )}
      </div>

      {/* Event list */}
      {auditQ.isLoading ? (
        <LoadingState variant="list" rows={8} />
      ) : auditQ.isError ? (
        <ApiErrorState
          title="Couldn't load audit events"
          onRetry={() => auditQ.refetch()}
        />
      ) : items.length === 0 ? (
        <EmptyState
          icon={ScrollText}
          title="No audit events"
          description={
            workspaceFilter !== "all"
              ? `No events recorded for this ${BUSINESS_UNIT_LABEL.toLowerCase()} yet.`
              : "No events have been recorded yet."
          }
        />
      ) : (
        <div className="border-line-soft bg-panel-elevated overflow-hidden rounded-2xl border">
          <ul className="divide-line-soft divide-y">
            {items.map((event) => (
              <AuditRow
                key={event.id}
                event={event}
                workspaces={workspaces}
              />
            ))}
          </ul>
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <span className="text-muted-foreground font-mono text-[12px]">
            {total} total events
          </span>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page <= 1}
              onClick={() => updateParams({ page: page - 1 })}
              className="border-line-soft h-7 font-mono text-[12px]"
            >
              Prev
            </Button>
            <span className="text-muted-foreground font-mono text-[12px]">
              {page} / {totalPages}
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={page >= totalPages}
              onClick={() => updateParams({ page: page + 1 })}
              className="border-line-soft h-7 font-mono text-[12px]"
            >
              Next
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

function AuditRow({
  event,
  workspaces,
}: {
  event: AuditEvent;
  workspaces: Array<{ id: string; displayName: string }>;
}) {
  const tone = ACTION_TONE[event.action as AuditAction] ?? "text-foreground";
  const wsId = (event.detail as Record<string, unknown> | undefined)?.workspace_id as string | undefined;
  const wsName = wsId
    ? (workspaces.find((w) => w.id === wsId)?.displayName ?? wsId.slice(0, 8))
    : null;
  const isHuman = event.actor.id !== "system" && event.actor.id !== "agent";

  return (
    <li className="flex items-start gap-4 px-5 py-3.5">
      {/* Actor avatar */}
      <div className="border-line-soft mt-0.5 grid size-7 shrink-0 place-items-center rounded-full border">
        {isHuman ? (
          <User className="text-muted-foreground size-3.5" aria-hidden />
        ) : (
          <Bot className="text-muted-foreground size-3.5" aria-hidden />
        )}
      </div>

      {/* Content */}
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className={cn("font-mono text-[12.5px] font-semibold", tone)}>
            {event.action}
          </span>
          {wsName && (
            <Badge variant="outline" className="border-line-soft font-mono text-[10px]">
              {wsName}
            </Badge>
          )}
          <span className="text-muted-foreground font-mono text-[11px]">
            by {event.actor.name}
          </span>
        </div>
        <p className="text-muted-foreground mt-0.5 font-mono text-[11px]">
          {formatDistanceToNow(new Date(event.at), { addSuffix: true })}
        </p>
      </div>
    </li>
  );
}
