"use client";

import * as React from "react";
import {
  ArrowDownUp,
  Boxes,
  Download,
  FileCode,
  FileDiff,
  FileJson,
  FileText,
  GitPullRequest,
  Image as ImageIcon,
  Network,
  Presentation,
  Rocket,
  TestTubes,
  Trash2,
  Workflow,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Checkbox } from "@/components/ui/checkbox";
import { EmptyState } from "@/components/ui/empty-state";
import { LoadingState } from "@/components/ui/loading-state";
import { StatusBadge } from "@/components/ui/status-badge";
import type { Artifact, ArtifactType, Status } from "@/lib/schemas";

const TYPE_ICON: Record<ArtifactType, LucideIcon> = {
  story: FileText,
  acceptance_criteria: FileText,
  hld: Network,
  lld: Network,
  c4_diagram: Network,
  openapi_spec: FileJson,
  db_schema: FileCode,
  adr: FileText,
  pr: GitPullRequest,
  test_set: TestTubes,
  coverage_report: TestTubes,
  review_comment: FileDiff,
  pipeline: Workflow,
  iac_diff: Boxes,
  deploy_plan: Rocket,
  document: FileText,
  presentation: Presentation,
  diagram: ImageIcon,
};

/** Artifact types that have a generated blob available for download. */
const BLOB_TYPES = new Set<ArtifactType>([
  "adr", "hld", "lld", "story", "acceptance_criteria",
  "document", "presentation", "diagram",
]);

/** Phase badge tone — maps to semantic tokens (info / brand-bright / success). */
const PHASE_BADGE_CLASS: Record<string, string> = {
  requirements: "text-[oklch(var(--brand-bright))] bg-[oklch(0.65_0.18_40_/_0.14)]",
  design: "text-info bg-info/10",
  development: "text-success bg-success/10",
  review: "text-warning bg-warning/10",
  security: "text-destructive bg-destructive/10",
  testing: "text-info bg-info/10",
  deployment: "text-muted-foreground bg-muted",
  documentation: "text-info bg-info/10",
};

/** Immutable toggle for the multi-select story scope set. */
export function toggleSelection(current: Set<string>, id: string): Set<string> {
  const next = new Set(current);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  return next;
}

export interface ArtifactListProps {
  items: readonly Artifact[] | null;
  selectedId?: string;
  onSelect?: (artifact: Artifact) => void;
  /** Multi-select set for chat scope. Independent of `selectedId` (detail pane). */
  selectedIds?: Set<string>;
  /** When provided, each row renders a round selector checkbox. */
  onToggleSelect?: (artifact: Artifact) => void;
  /** When provided, each row renders a delete button. Omit it to hide deletion
   *  entirely — the caller gates on the `artifact:delete` permission, not this
   *  component, so an unprivileged user never renders a control they cannot use. */
  onDelete?: (artifact: Artifact) => void;
  /** Id currently being deleted — its row shows a pending state and stops accepting
   *  clicks, so an impatient second click cannot fire a second DELETE. */
  deletingId?: string | null;
  isLoading?: boolean;
  emptyTitle?: string;
  emptyDescription?: React.ReactNode;
  className?: string;
}

type SortKey = "recent" | "title" | "status";

export function ArtifactList({
  items,
  selectedId,
  onSelect,
  selectedIds,
  onToggleSelect,
  onDelete,
  deletingId,
  isLoading,
  emptyTitle = "No artifacts yet",
  emptyDescription = "Artifacts appear here once the agent runs.",
  className,
}: ArtifactListProps) {
  const [search, setSearch] = React.useState("");
  const [typeFilter, setTypeFilter] = React.useState<"all" | ArtifactType>("all");
  const [statusFilter, setStatusFilter] = React.useState<"all" | Status>("all");
  const [sortKey, setSortKey] = React.useState<SortKey>("recent");

  const filtered = React.useMemo(() => {
    if (!items) return [];
    let next = items.slice();
    if (search.trim()) {
      const q = search.toLowerCase();
      next = next.filter((a) => a.title.toLowerCase().includes(q));
    }
    if (typeFilter !== "all") next = next.filter((a) => a.type === typeFilter);
    if (statusFilter !== "all") next = next.filter((a) => a.status === statusFilter);
    next.sort((a, b) => {
      if (sortKey === "title") return a.title.localeCompare(b.title);
      if (sortKey === "status") return a.status.localeCompare(b.status);
      return b.updatedAt.localeCompare(a.updatedAt);
    });
    return next;
  }, [items, search, typeFilter, statusFilter, sortKey]);

  const onKeyDown = (e: React.KeyboardEvent<HTMLUListElement>) => {
    if (!onSelect || filtered.length === 0) return;
    const idx = filtered.findIndex((a) => a.id === selectedId);
    if (e.key === "ArrowDown") {
      e.preventDefault();
      onSelect(filtered[Math.min(filtered.length - 1, idx + 1)]!);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      onSelect(filtered[Math.max(0, idx - 1)]!);
    }
  };

  return (
    <div className={cn("flex min-h-0 flex-col gap-3", className)}>
      {/* Filter toolbar */}
      <div className="flex flex-col gap-2 sm:flex-row">
        <Input
          placeholder="Filter artifacts…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="h-8 flex-1 font-sans text-sm"
          aria-label="Filter artifacts"
        />
        <div className="flex gap-2">
          <Select value={typeFilter} onValueChange={(v) => setTypeFilter(v as typeof typeFilter)}>
            <SelectTrigger className="h-8 w-32 font-sans text-xs" aria-label="Filter by type">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All types</SelectItem>
              {(Object.keys(TYPE_ICON) as ArtifactType[]).map((t) => (
                <SelectItem key={t} value={t}>
                  {t.replace(/_/g, " ")}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={statusFilter}
            onValueChange={(v) => setStatusFilter(v as typeof statusFilter)}
          >
            <SelectTrigger className="h-8 w-32 font-sans text-xs" aria-label="Filter by status">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Any status</SelectItem>
              {(
                [
                  "draft",
                  "queued",
                  "running",
                  "awaiting_approval",
                  "approved",
                  "rejected",
                  "failed",
                  "merged",
                ] as const
              ).map((s) => (
                <SelectItem key={s} value={s}>
                  {s.replace(/_/g, " ")}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={sortKey} onValueChange={(v) => setSortKey(v as SortKey)}>
            <SelectTrigger
              className="h-8 w-12 px-2"
              aria-label="Sort"
              title={`Sort: ${sortKey}`}
            >
              <ArrowDownUp className="size-3.5" aria-hidden />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="recent">Most recent</SelectItem>
              <SelectItem value="title">Title</SelectItem>
              <SelectItem value="status">Status</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      {isLoading && <LoadingState variant="list" rows={5} />}

      {!isLoading && filtered.length === 0 && (
        <EmptyState title={emptyTitle} description={emptyDescription} variant="plain" />
      )}

      {!isLoading && filtered.length > 0 && (
        <ul
          className="focus-visible:outline-none"
          onKeyDown={onKeyDown}
          tabIndex={0}
          role="listbox"
          aria-label="Artifacts"
        >
          {filtered.map((a) => {
            const Icon = TYPE_ICON[a.type];
            const active = selectedId === a.id;
            const phaseBadgeClass = PHASE_BADGE_CLASS[a.phase] ?? "text-muted-foreground bg-muted";
            const isBlob = BLOB_TYPES.has(a.type) && !!a.downloadUrl;
            return (
              <li key={a.id} role="option" aria-selected={active} className="flex items-center gap-2">
                {onToggleSelect && (
                  <Checkbox
                    className="size-4 shrink-0 rounded-full"
                    checked={selectedIds?.has(a.id) ?? false}
                    onCheckedChange={() => onToggleSelect(a)}
                    aria-label={`Select ${a.title} for the agent`}
                  />
                )}
                <button
                  type="button"
                  onClick={() => onSelect?.(a)}
                  className={cn(
                    "group flex min-w-0 flex-1 items-center gap-3 rounded-md border px-3 py-2.5 text-left transition-colors",
                    "hover:bg-surface-1 hover:text-foreground",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1",
                    active
                      ? "bg-surface-1 border-line-soft"
                      : "border-transparent hover:border-line-soft",
                    "mb-1",
                  )}
                >
                  {/* Icon container — 32 × 32 rounded tile */}
                  <span
                    className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted/60"
                    aria-hidden
                  >
                    <Icon className="text-muted-foreground size-4" />
                  </span>

                  {/* Title + meta */}
                  <div className="flex min-w-0 flex-1 flex-col">
                    <span className="font-display truncate text-sm font-semibold leading-snug">
                      {a.title}
                    </span>
                    <span className="text-muted-foreground mt-0.5 truncate font-mono text-[11px] leading-tight">
                      {a.type.replace(/_/g, " ")} · v{a.version}
                    </span>
                  </div>

                  {/* Right side: phase badge + status + optional download */}
                  <div className="flex shrink-0 items-center gap-2">
                    <span
                      className={cn(
                        "rounded-full px-2 py-0.5 font-mono text-[10.5px] font-semibold uppercase leading-none",
                        phaseBadgeClass,
                      )}
                    >
                      {a.phase}
                    </span>
                    <StatusBadge status={a.status} iconOnly />
                    {isBlob && (
                      <a
                        href={a.downloadUrl ?? undefined}
                        download
                        target="_blank"
                        rel="noopener noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="text-muted-foreground hover:text-foreground opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
                        aria-label={`Download ${a.title}`}
                        tabIndex={-1}
                      >
                        <Download className="size-3.5" aria-hidden />
                      </a>
                    )}
                  </div>
                </button>

                {onDelete && (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      onDelete(a);
                    }}
                    disabled={deletingId === a.id}
                    className={cn(
                      "text-muted-foreground hover:text-destructive shrink-0 rounded-md p-1.5",
                      "transition-colors focus-visible:outline-none focus-visible:ring-2",
                      "focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-40",
                    )}
                    // The label names the artifact: a screen-reader user hears which
                    // one they are about to destroy, not just "Delete".
                    aria-label={`Delete ${a.title}`}
                    title="Delete artifact"
                  >
                    <Trash2 className="size-3.5" aria-hidden />
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
