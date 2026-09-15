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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { SortableHead, type SortDir } from "@/components/ui/sortable-header";
import { ActivityTabs } from "@/components/app/activity-tabs";
import { RestrictedAccess } from "@/components/auth/restricted-access";
import { ScopeChip } from "@/components/app/scope-indicator";
import { useAccessScope } from "@/hooks/use-access-scope";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { ApiErrorState } from "@/components/feedback/api-error-state";
import { useSession } from "@/hooks/use-session";
import { hasPermission } from "@/lib/auth/permissions";
import { exportAuditEvents, listAuditEvents } from "@/lib/api/audit";
import { qk } from "@/lib/api/query-keys";
import { downloadCsv, downloadJson } from "@/lib/export";
import type { AuditAction, AuditEvent } from "@/lib/schemas";

const PAGE_SIZE = 50;

/**
 * Changing a filter restarts the walk. A cursor is a position in ONE result set, so
 * carrying it across a filter change would anchor the new query to a row that may not
 * be in it — landing the reader mid-way through results they have never seen, or on an
 * empty page that looks like "no matches".
 */
const RESET_WALK = { cursor: undefined, dir: undefined, step: undefined } as const;

/**
 * The first segment of a UUID — enough to recognise a repeat or paste into a search,
 * without spending a line of the row on it.
 *
 * Only ever shown when the server could not name the thing: a seeded id like
 * `demo-dev` is already legible and is left alone.
 */
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function isUuid(id: string): boolean {
  return UUID_RE.test(id);
}

function shortId(id: string): string {
  return isUuid(id) ? `${id.slice(0, 8)}…` : id;
}

/**
 * The actions the filter OFFERS are derived from the actions present, not from a
 * fixed list.
 *
 * This was fifteen hardcoded values taken from the spec register, and on a live
 * database not one of them occurs: the backend emits `rbac.role.granted`,
 * `rbac.role.revoked`, `access.denied`, `artifact_upload`, `artifact_approve`,
 * `agent_profile.published`. So every option in the dropdown filtered to nothing,
 * and every action you could actually see was unfilterable — the control was
 * exactly inverted.
 *
 * Derived from the loaded page, which is the honest scope: it can only offer what
 * it has seen. A currently-selected action is always included so a deep link or a
 * filter that now matches nothing stays selectable rather than silently resetting.
 */
function actionOptions(
  events: { action: string }[],
  selected: string,
): string[] {
  const seen = new Set(events.map((e) => e.action));
  if (selected !== "all") seen.add(selected);
  return [...seen].sort();
}

// Keyed by string, not AuditAction: the backend's action vocabulary is open (see
// lib/schemas/audit.ts), so these maps colour the ones they know and fall back for
// the rest. Typing them to the closed enum would not stop an unknown action
// arriving — it would only stop this file compiling once the schema stopped lying.
const ACTION_TONE: Record<string, string> = {
  "run.failed": "text-destructive",
  "run.rejected": "text-destructive",
  "run.approved": "text-success",
  "run.completed": "text-success",
  "connector.installed": "text-success",
  "connector.revoked": "text-destructive",
};

/** Dot color per action tone — maps to inline style-free token classes. */
const ACTION_DOT: Record<string, string> = {
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
  const cursor = searchParams.get("cursor") ?? undefined;
  const direction = (searchParams.get("dir") === "prev" ? "prev" : "next") as "next" | "prev";
  /**
   * DISPLAY ONLY, and it has to be — a cursor knows where it is in the data, not how
   * many pages you walked to get there. It counts the reader's steps so the footer can
   * say "page 3" instead of nothing; it is never sent to the server, and a filter
   * change resets it because the walk starts over.
   */
  const step = Number(searchParams.get("step") ?? "1");
  /**
   * Sort runs on the SERVER, because the page is one cursor-walked slice of the trail.
   * Sorting `items` here would reorder the fifty rows already fetched and present it
   * as 133 rows sorted — the same lie the search box told before it moved server-side.
   */
  const sortDir: SortDir = searchParams.get("sort") === "asc" ? "asc" : "desc";

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
  // EVERY FILTER GOES TO THE SERVER. Two of them used to be applied to the rows
  // already on screen: the page fetched 50, then narrowed those. On 132 events over
  // three pages that meant the actor dropdown and the search box could only ever see
  // a third of the trail — and reported what they found as "32 shown", which reads
  // like an answer rather than a third of one. On a trail worth auditing it would be
  // a rounding error of one.
  //
  // `q` is debounced because it fires per keystroke; the rest change on a click.
  const debouncedSearch = useDebouncedValue(search, 300);
  const auditQ = useQuery({
    queryKey: qk.audit.list({
      action: actionFilter,
      actor: actorFilter,
      q: debouncedSearch,
      cursor: cursor ?? null,
      direction,
      sort: sortDir,
    }),
    queryFn: () =>
      listAuditEvents({
        action: actionFilter === "all" ? undefined : actionFilter,
        actor: actorFilter === "all" ? undefined : actorFilter,
        q: debouncedSearch || undefined,
        cursor,
        direction,
        sort: sortDir === "asc" ? "oldest" : "newest",
        pageSize: PAGE_SIZE,
      }),
    // Keeps the current page on screen while the next one loads, so typing dims the
    // table rather than collapsing it to a spinner on every keystroke.
    placeholderData: (prev) => prev,
  });

  // The server already applied every filter — these ARE the matching rows.
  const items = auditQ.data?.items ?? [];

  const [selected, setSelected] = React.useState<AuditEvent | null>(null);

  const [exporting, setExporting] = React.useState(false);

  /**
   * The rows for an export, FROM THE SERVER — the whole filtered trail, not the page.
   *
   * Two things were wrong with building this here. It exported `items`, which is one
   * page: "Export" on a four-thousand-event trail produced fifty rows in a file named
   * after the audit log. And PRD §34.9 makes exporting an audited event, which a
   * browser-side export cannot honestly be — the record has to be written by the thing
   * that hands over the data, or it is a courtesy rather than a control.
   */
  const fetchExport = async (fmt: "csv" | "json") => {
    setExporting(true);
    try {
      return await exportAuditEvents({
        fmt,
        actor: actorFilter === "all" ? undefined : actorFilter,
        action: actionFilter === "all" ? undefined : actionFilter,
        q: debouncedSearch || undefined,
      });
    } catch {
      toast.error("Couldn't export — the trail was not read.");
      return null;
    } finally {
      setExporting(false);
    }
  };

  const exportCsv = async () => {
    const rows = await fetchExport("csv");
    if (!rows) return;
    downloadCsv(
      `audit-${new Date().toISOString().slice(0, 10)}.csv`,
      rows.map((e) => ({
        id: e.id,
        at: e.at,
        actor_id: e.actor.id,
        actor_name: e.actor.name,
        action: e.action,
        resource_type: e.resource.type,
        resource_id: e.resource.id,
        resource_name: e.resource.name ?? "",
        scope_kind: e.scope?.kind ?? "",
        scope_id: e.scope?.id ?? "",
        scope_name: e.scope?.name ?? "",
        project_id: e.projectId ?? "",
        project_name: e.projectName ?? "",
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
        { key: "scope_kind", header: "scope_kind" },
        { key: "scope_id", header: "scope_id" },
        { key: "scope_name", header: "scope_name" },
        { key: "project_id", header: "project_id" },
        { key: "project_name", header: "project_name" },
        { key: "ip", header: "ip" },
        { key: "detail", header: "detail" },
      ],
    );
    toast.success(`Exported ${rows.length} events`);
  };

  const exportJson = async () => {
    const rows = await fetchExport("json");
    if (!rows) return;
    downloadJson(`audit-${new Date().toISOString().slice(0, 10)}.json`, rows);
    toast.success(`Exported ${rows.length} events`);
  };

  const total = auditQ.data?.total ?? 0;
  const nextCursor = auditQ.data?.nextCursor ?? null;
  const prevCursor = auditQ.data?.prevCursor ?? null;

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
            {auditQ.data && (
              <span className="text-muted-foreground font-mono text-[11.5px]">
                {total} events · page {step}
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
            disabled={exporting || items.length === 0}
            className="border-line-soft"
          >
            <Download className="size-4" aria-hidden />
            CSV
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={exportJson}
            disabled={exporting || items.length === 0}
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
          onChange={(e) => updateParams({ q: e.target.value || undefined, ...RESET_WALK })}
          placeholder="Search actor, action, resource…"
          className="border-line-soft h-9 w-64"
          aria-label="Search audit events"
        />
        <Select
          value={actionFilter}
          onValueChange={(v) => updateParams({ action: v, ...RESET_WALK })}
        >
          <SelectTrigger className="border-line-soft h-9 w-56" aria-label="Filter by action">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All actions</SelectItem>
            {actionOptions(auditQ.data?.items ?? [], actionFilter).map((a) => (
              <SelectItem key={a} value={a} className="font-mono text-xs">
                {a}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={actorFilter}
          onValueChange={(v) => updateParams({ actor: v, ...RESET_WALK })}
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
              {auditQ.data ? `${items.length} of ${total} matching` : `${items.length} shown`}
            </span>
          </div>

          {/* THE COLUMNS ARE PRD §34.9's FIELDS, in its order: actor, action, scope,
              when, before/after. A stream of prose rows could show the same values
              and could not be scanned down — "every grant in this unit last week" is
              a column comparison, and a comparison needs a column.

              `Change` is the before/after pair. It is empty on every row today
              because NO WRITER RECORDS ONE (shared/authz/audit.py stores the new
              role and not the old), and it is here rather than hidden so the gap is
              visible in the product instead of only in the spec. */}
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  {/* THE ONLY SORTABLE COLUMN, and deliberately. Actor and Scope are
                      resolved after the query returns and Change is derived from the
                      payload, so ordering by them means ordering by a raw id the
                      reader never sees — or sorting one page and calling the trail
                      sorted. A header that cannot honestly sort does not offer to. */}
                  <SortableHead
                    label="Date & time"
                    className="w-[11rem]"
                    active
                    dir={sortDir}
                    onSort={(next) =>
                      updateParams({ sort: next === "asc" ? "asc" : undefined, ...RESET_WALK })
                    }
                  />
                  <TableHead className="w-[15rem]">Actor</TableHead>
                  <TableHead className="w-[12rem]">Action</TableHead>
                  <TableHead className="w-[14rem]">Scope</TableHead>
                  <TableHead>Resource</TableHead>
                  <TableHead className="w-[15rem]">Change</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((e) => (
                  <AuditEventRow key={e.id} event={e} onClick={() => setSelected(e)} />
                ))}
              </TableBody>
            </Table>
          </div>
        </div>
      )}

      {(nextCursor || prevCursor) && (
        <nav
          aria-label="Pagination"
          className="border-line-soft text-muted-foreground flex items-center justify-between border-t pt-4 text-sm"
        >
          {/* No "showing 51–100 of 4,312". That range is arithmetic on an offset, and
              there is no offset any more — the walk knows which rows it is on, not how
              many sit above them. The count of matches is the number that was doing the
              work anyway, and it is still exact. */}
          <span className="font-mono text-xs">
            {items.length} of {total} matching
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={!prevCursor}
              onClick={() =>
                updateParams({ cursor: prevCursor!, dir: "prev", step: Math.max(1, step - 1) })
              }
              className="border-line-soft"
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!nextCursor}
              onClick={() =>
                updateParams({ cursor: nextCursor!, dir: "next", step: step + 1 })
              }
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

// ───────── One audit row, as a table row ─────────

/** PRD §34.9's Scope kinds, in the words the rest of the product uses. */
const SCOPE_LABEL: Record<string, string> = {
  organization: "Organization",
  business_unit: "Business unit",
  workspace: "Business unit",
  project: "Project",
};

/**
 * The before/after pair for one event, or null when the writer recorded none.
 *
 * NOTHING RECORDS ONE TODAY. `shared/authz/audit.py` stores the role that was
 * granted and not the role it replaced, and no other writer carries a prior state
 * either — so this returns null on all 132 events currently in the trail. It reads
 * the pair rather than assuming its absence because the column becomes correct the
 * moment a writer starts filling it in, with no second change here.
 */
function changePair(detail: Record<string, unknown> | null | undefined) {
  if (!detail) return null;
  const before = detail["before"] ?? detail["old_value"] ?? detail["previous"];
  const after = detail["after"] ?? detail["new_value"];
  if (before === undefined && after === undefined) return null;
  const render = (v: unknown) =>
    v === undefined || v === null ? "—" : typeof v === "string" ? v : JSON.stringify(v);
  return { before: render(before), after: render(after) };
}

/**
 * An id the server could not put a name to.
 *
 * THIS IS NOT A RESOLUTION FAILURE, and it must not look like one. The trail
 * outlives what it references: `fa5e4ce1-…` is a project that eight role grants were
 * recorded against and that exists in no table today, and the record still has to
 * say what happened. Rendering the bare id there read as "the name did not load",
 * which is the one thing it does not mean.
 *
 * The id stays visible — it is the only handle left on a thing that is gone, and it
 * is what a reader would paste into a search or a ticket.
 */
function Unresolved({ id, kind }: { id: string; kind: string }) {
  return (
    <span
      title={`This ${kind} no longer exists in this organization. The audit trail outlives what it references — id ${id}`}
    >
      <span className="text-muted-foreground italic">not found</span>{" "}
      <span className="text-muted-foreground/70 font-mono text-[10.5px]">{shortId(id)}</span>
    </span>
  );
}

function AuditEventRow({ event, onClick }: { event: AuditEvent; onClick: () => void }) {
  const dotClass = ACTION_DOT[event.action] ?? "bg-muted-foreground";
  const toneClass = ACTION_TONE[event.action] ?? "text-foreground";
  const at = new Date(event.at);
  const relativeTime = formatDistanceToNow(at, { addSuffix: true });
  const isoTime = at.toISOString().replace("T", " ").slice(0, 19);
  // `YYYY-MM-DD HH:mm:ss` in the reader's own zone. Fixed-width by construction, so
  // the column stays aligned and two rows a second apart are visibly two rows a
  // second apart — which `toLocaleString()` would not guarantee across locales.
  const pad = (n: number) => String(n).padStart(2, "0");
  const localStamp =
    `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())} ` +
    `${pad(at.getHours())}:${pad(at.getMinutes())}:${pad(at.getSeconds())}`;
  const change = changePair(event.detail);
  const scope = event.scope;

  return (
    <TableRow className="cursor-pointer" onClick={onClick}>
      {/* THE TIMESTAMP LEADS, not "1 day".
          A relative age is how you notice something; a date and time is what you act
          on. Thirty rows all reading "1 day" cannot be ordered, cross-referenced with
          an incident ticket, or quoted in a report — and an audit trail exists to be
          quoted. The relative age moves to the tooltip, where it costs nothing.

          Local time, not UTC: it is read by people who were in the room. The ISO/UTC
          form is in the detail panel and both exports, which is what a regulator gets. */}
      <TableCell className="text-muted-foreground align-top font-mono text-[11px] whitespace-nowrap tabular-nums">
        <span title={`${relativeTime} · ${isoTime}Z`}>{localStamp}</span>
      </TableCell>

      {/* Actor — REACHABLE WITHOUT A MOUSE. A <tr> is not focusable and carries no
          role, so a bare row handler leaves the detail modal openable by click only
          (the same lesson as the traces table). The actor is the button. */}
      <TableCell className="align-top">
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onClick();
          }}
          className="focus-visible:ring-ring inline-flex max-w-full items-center gap-1.5 rounded-sm text-left text-[12.5px] font-semibold focus-visible:ring-2 focus-visible:outline-none"
        >
          <span className={cn("size-[6px] shrink-0 rounded-full", dotClass)} aria-hidden />
          {event.actor.id === "agent" ? (
            <Bot className="text-muted-foreground size-3.5 shrink-0" aria-hidden />
          ) : event.actor.id === "system" ? (
            <ScrollText className="text-muted-foreground size-3.5 shrink-0" aria-hidden />
          ) : (
            <User className="text-muted-foreground size-3.5 shrink-0" aria-hidden />
          )}
          <span className="truncate" title={event.actor.id}>
            {event.actor.name}
          </span>
        </button>
      </TableCell>

      {/* Action */}
      <TableCell className="align-top">
        <Badge variant="outline" className={cn("font-mono text-[10px]", toneClass)}>
          {event.action}
        </Badge>
      </TableCell>

      {/* Scope — the governance ladder, not the object. */}
      <TableCell className="align-top text-[12px]">
        {scope ? (
          <span title={scope.id}>
            <span className="text-muted-foreground">
              {SCOPE_LABEL[scope.kind] ?? scope.kind}
            </span>{" "}
            {scope.name ? (
              <span className="font-medium">{scope.name}</span>
            ) : (
              <Unresolved id={scope.id} kind={(SCOPE_LABEL[scope.kind] ?? scope.kind).toLowerCase()} />
            )}
          </span>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </TableCell>

      {/* Resource — the specific object the action landed on. */}
      <TableCell className="align-top font-mono text-[11px]">
        <span className="text-muted-foreground">{event.resource.type}</span>{" "}
        {event.resource.name ? (
          <span className="text-foreground" title={event.resource.id}>
            {event.resource.name}
          </span>
        ) : isUuid(event.resource.id) ? (
          <Unresolved id={event.resource.id} kind={event.resource.type.replace(/_/g, " ")} />
        ) : (
          // A seeded id like `demo-ba-30503` is not a UUID and carries its own
          // meaning — replacing it with "not found" would throw information away.
          <span className="text-foreground">{event.resource.id}</span>
        )}
      </TableCell>

      {/* Change — see changePair: empty until a writer records one. */}
      <TableCell className="align-top font-mono text-[11px]">
        {change ? (
          <span className="inline-flex flex-wrap items-center gap-1">
            {/* "none" is a state, not a thing struck out — an appointment did not
                delete anything, and a strikethrough there reads as if it had. */}
            <span
              className={cn(
                "text-muted-foreground",
                change.before !== "none" && "line-through",
              )}
            >
              {change.before}
            </span>
            <span aria-hidden>→</span>
            <span className="text-foreground">{change.after}</span>
          </span>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </TableCell>
    </TableRow>
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
          {event.scope && (
            <DetailRow
              label="Scope"
              value={`${SCOPE_LABEL[event.scope.kind] ?? event.scope.kind} · ${
                event.scope.name ? `${event.scope.name} (${event.scope.id})` : event.scope.id
              }`}
            />
          )}
          <DetailRow
            label="Resource"
            value={`${event.resource.type} · ${
              event.resource.name ? `${event.resource.name} (${event.resource.id})` : event.resource.id
            }`}
          />
          {event.projectId && (
            <DetailRow
              label="Project"
              value={
                event.projectName ? `${event.projectName} (${event.projectId})` : event.projectId
              }
            />
          )}
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
