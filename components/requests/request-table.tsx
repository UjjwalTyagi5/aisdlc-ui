"use client";

import * as React from "react";
import { formatDistanceToNow } from "date-fns";
import { Search } from "lucide-react";

import { cn } from "@/lib/utils";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  RequestPriorityBadge,
  RequestStatusBadge,
  requestStatusLabel,
} from "@/components/requests/request-status-badge";
import { ROLE_META, type PlatformRole } from "@/lib/roles";
import {
  GovernanceApprovalStatus,
  REQUEST_TYPE_LABEL,
  RequestPriority,
} from "@/lib/schemas/governance-approval";
import type { GovernanceApproval } from "@/lib/schemas";
import { Inbox } from "lucide-react";

const ALL = "all";

/**
 * The request list, with the three filters that actually narrow it.
 *
 * Filters are DERIVED from the rows on screen, not enumerated from the schema:
 * offering "Project archive" in a type dropdown when no such request exists
 * invites a selection that empties the table for no reason the reader can see.
 * The status filter is the exception — its full set is the lifecycle, and
 * showing which states are absent is itself information.
 */
export function RequestTable({
  requests,
  onOpen,
  emptyTitle,
  emptyDescription,
  className,
}: {
  requests: GovernanceApproval[];
  onOpen: (request: GovernanceApproval) => void;
  emptyTitle: string;
  emptyDescription: string;
  className?: string;
}) {
  const [query, setQuery] = React.useState("");
  const [type, setType] = React.useState<string>(ALL);
  const [status, setStatus] = React.useState<string>(ALL);
  const [priority, setPriority] = React.useState<string>(ALL);

  const typesPresent = React.useMemo(
    () => [...new Set(requests.map((r) => r.type))],
    [requests],
  );

  const filtered = React.useMemo(() => {
    const q = query.trim().toLowerCase();
    return requests.filter((r) => {
      if (type !== ALL && r.type !== type) return false;
      if (status !== ALL && r.status !== status) return false;
      if (priority !== ALL && r.priority !== priority) return false;
      if (!q) return true;
      // Searched across everything a person would recognise the row by —
      // title, unit, project and requester. Matching the title alone fails the
      // common case of "what did Marcus raise for Payments".
      return (
        r.title.toLowerCase().includes(q) ||
        r.summary.toLowerCase().includes(q) ||
        r.workspaceName.toLowerCase().includes(q) ||
        (r.projectName ?? "").toLowerCase().includes(q) ||
        r.requestedBy.toLowerCase().includes(q)
      );
    });
  }, [requests, query, type, status, priority]);

  return (
    <div className={cn("space-y-3", className)}>
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[200px] flex-1">
          <Search
            className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2"
            aria-hidden
          />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search requests…"
            aria-label="Search requests"
            className="border-line-soft h-9 pl-8 text-[12.5px]"
          />
        </div>

        <Select value={type} onValueChange={setType}>
          <SelectTrigger aria-label="Filter by type" className="border-line-soft h-9 w-[10.5rem] text-[12px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All types</SelectItem>
            {typesPresent.map((t) => (
              <SelectItem key={t} value={t}>
                {REQUEST_TYPE_LABEL[t]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select value={status} onValueChange={setStatus}>
          <SelectTrigger aria-label="Filter by status" className="border-line-soft h-9 w-[9.5rem] text-[12px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All statuses</SelectItem>
            {GovernanceApprovalStatus.options.map((s) => (
              <SelectItem key={s} value={s}>
                {requestStatusLabel(s)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select value={priority} onValueChange={setPriority}>
          <SelectTrigger aria-label="Filter by priority" className="border-line-soft h-9 w-[8.5rem] text-[12px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All priorities</SelectItem>
            {RequestPriority.options.map((p) => (
              <SelectItem key={p} value={p} className="capitalize">
                {p}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {filtered.length === 0 ? (
        requests.length === 0 ? (
          <EmptyState icon={Inbox} title={emptyTitle} description={emptyDescription} />
        ) : (
          <p className="text-muted-foreground py-8 text-center text-[12.5px]">
            No request matches these filters.
          </p>
        )
      ) : (
        <ul className="border-line-soft divide-line-soft bg-panel-elevated divide-y overflow-hidden rounded-xl border">
          {filtered.map((r) => {
            const approver = r.currentApproverRole as PlatformRole | null;
            return (
              <li key={r.id}>
                {/* A row is one button, not a row of them: the whole thing
                    opens the detail, which is where every action lives. */}
                <button
                  type="button"
                  onClick={() => onOpen(r)}
                  className="hover:bg-surface-1 focus-visible:ring-ring flex w-full items-start gap-3 px-4 py-3 text-left transition-colors focus-visible:ring-2 focus-visible:outline-none"
                >
                  <span className="min-w-0 flex-1 space-y-1">
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="truncate text-[13px] font-medium">{r.title}</span>
                      <RequestPriorityBadge priority={r.priority} />
                    </span>
                    <span className="text-muted-foreground flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11.5px]">
                      <span className="font-mono text-[10px] tracking-[0.08em] uppercase">
                        {REQUEST_TYPE_LABEL[r.type]}
                      </span>
                      <span aria-hidden>·</span>
                      <span>{r.workspaceName}</span>
                      {r.projectName && (
                        <>
                          <span aria-hidden>·</span>
                          <span>{r.projectName}</span>
                        </>
                      )}
                      <span aria-hidden>·</span>
                      <span>{r.requestedBy}</span>
                      <span aria-hidden>·</span>
                      <span>
                        {formatDistanceToNow(new Date(r.requestedAt), { addSuffix: true })}
                      </span>
                    </span>
                  </span>

                  <span className="flex shrink-0 flex-col items-end gap-1">
                    <RequestStatusBadge status={r.status} />
                    {approver && (
                      <span className="text-muted-foreground hidden text-[11px] sm:block">
                        {ROLE_META[approver].label}
                      </span>
                    )}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
