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

/** A stored artifact's id is a UUID. A SYNTHESISED one is not.
 *
 *  `story_artifacts_from_run` materialises the Requirements list straight out of a run's
 *  `requirements_payload` — there is no structured-story table — giving ids shaped
 *  `{run_id}:story:{source_key}`. Those name no row, so every write route answers 404
 *  for them. Offering a delete button on one is offering an action that cannot work.
 *
 *  Testing the id shape rather than `type === "story"` means the check corrects itself:
 *  if stories ever get real rows, they become deletable without touching this.
 */
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isStoredArtifact(id: string): boolean {
  return UUID_RE.test(id);
}

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

/** The board's own work-item type — "Epic", "Bug", "User Story" — or null.
 *
 * Lives on the story BODY, not on `artifact.type`, which is "story" for every row a
 * board pull produces. `ingest_board` fetches every work item on the board, so one
 * project's "stories" were an Epic and three Tasks about configuring the board itself;
 * this is the field that tells them apart.
 */
function workItemTypeOf(a: Artifact): string | null {
  const body = a.body as { workItemType?: unknown } | null | undefined;
  const t = typeof body?.workItemType === "string" ? body.workItemType.trim() : "";
  return t || null;
}

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
  /** What these rows ARE, for the search placeholder — "stories" on Requirements,
   *  "artifacts" elsewhere. The box read "Filter artifacts…" above a list headed
   *  "Stories (15)", which asks the reader to work out that the two are the same
   *  thing. Plural, lowercase. */
  noun?: string;
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
  noun = "artifacts",
  emptyTitle = "No artifacts yet",
  emptyDescription = "Artifacts appear here once the agent runs.",
  className,
}: ArtifactListProps) {
  const [search, setSearch] = React.useState("");
  const [typeFilter, setTypeFilter] = React.useState<"all" | ArtifactType>("all");
  const [statusFilter, setStatusFilter] = React.useState<"all" | Status>("all");
  const [sortKey, setSortKey] = React.useState<SortKey>("recent");
  const [workItemFilter, setWorkItemFilter] = React.useState<string>("all");

  // WHAT IS ACTUALLY IN THE LIST, which is what the dropdowns should offer. Derived
  // from `items` rather than from the filtered result: narrowing to one status must not
  // then remove every other option and strand the person on it.
  const presentTypes = React.useMemo(
    () => Array.from(new Set((items ?? []).map((a) => a.type))).sort(),
    [items],
  );
  const presentStatuses = React.useMemo(
    () => Array.from(new Set((items ?? []).map((a) => a.status))).sort(),
    [items],
  );
  // THE BOARD'S OWN TYPE — Epic, Task, Bug, User Story — which is the one thing that
  // actually varies across a pulled list. `a.type` is "story" for every row here, so
  // the type filter above could never separate them; `ingest_board` pulls EVERY work
  // item, and telling an Epic from a Bug is the distinction people want.
  const presentWorkItems = React.useMemo(
    () =>
      Array.from(
        new Set(
          (items ?? [])
            .map((a) => workItemTypeOf(a))
            .filter((t): t is string => Boolean(t)),
        ),
      ).sort(),
    [items],
  );

  const filtered = React.useMemo(() => {
    if (!items) return [];
    let next = items.slice();
    if (search.trim()) {
      const q = search.toLowerCase();
      next = next.filter((a) => a.title.toLowerCase().includes(q));
    }
    if (typeFilter !== "all") next = next.filter((a) => a.type === typeFilter);
    if (workItemFilter !== "all") {
      next = next.filter((a) => workItemTypeOf(a) === workItemFilter);
    }
    if (statusFilter !== "all") next = next.filter((a) => a.status === statusFilter);
    next.sort((a, b) => {
      if (sortKey === "title") return a.title.localeCompare(b.title);
      if (sortKey === "status") return a.status.localeCompare(b.status);
      return b.updatedAt.localeCompare(a.updatedAt);
    });
    return next;
  }, [items, search, typeFilter, statusFilter, workItemFilter, sortKey]);

  const selectedRowRef = React.useRef<HTMLLIElement | null>(null);
  React.useEffect(() => {
    // `nearest` rather than `center`: clicking a row that is already fully visible must
    // not jerk the list to re-centre it under the pointer.
    selectedRowRef.current?.scrollIntoView({ block: "nearest" });
  }, [selectedId]);

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
      {/* Filter toolbar.
          SEARCH GETS ITS OWN ROW. It was `flex-1` beside two w-32 selects and the sort
          button, and this list lives in a ~390px sidebar — the fixed widths ate the
          row and collapsed the input to a ~30px sliver with no visible placeholder. It
          looked like a stray empty box, so the search may as well not have existed. */}
      <div className="flex flex-col gap-2">
        <Input
          placeholder={`Filter ${noun}…`}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="h-8 w-full font-sans text-sm"
          aria-label={`Filter ${noun}`}
        />
        <div className="flex gap-2">
          {/* ONLY WHEN THERE IS A CHOICE TO MAKE. This offered every type the platform
              knows about regardless of what was in the list, so on Requirements — where
              all fifteen rows are stories — nine of the ten options could only empty the
              screen. A filter whose options mostly produce "no results" teaches people
              to stop touching filters. */}
          {presentTypes.length > 1 && (
            <Select value={typeFilter} onValueChange={(v) => setTypeFilter(v as typeof typeFilter)}>
              <SelectTrigger className="h-8 w-32 font-sans text-xs" aria-label="Filter by type">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All types</SelectItem>
                {presentTypes.map((t) => (
                  <SelectItem key={t} value={t}>
                    {t.replace(/_/g, " ")}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          {presentStatuses.length > 1 && (
            <Select
              value={statusFilter}
              onValueChange={(v) => setStatusFilter(v as typeof statusFilter)}
            >
              <SelectTrigger className="h-8 w-32 font-sans text-xs" aria-label="Filter by status">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Any status</SelectItem>
                {presentStatuses.map((s) => (
                  <SelectItem key={s} value={s}>
                    {s.replace(/_/g, " ")}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          {presentWorkItems.length > 1 && (
            <Select value={workItemFilter} onValueChange={setWorkItemFilter}>
              <SelectTrigger
                className="h-8 w-32 font-sans text-xs"
                aria-label="Filter by work item type"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All work items</SelectItem>
                {presentWorkItems.map((t) => (
                  <SelectItem key={t} value={t}>
                    {t}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
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

      {/* WHAT THE CIRCLES ARE FOR. They set the agent's SCOPE, which is a different
          question from which row is open — clicking a story to read it must not quietly
          change what the agent operates on. Unlabelled, though, a circle beside a
          highlighted row simply reads as a selection radio that has stopped working,
          which is exactly how it was reported. The only explanation was an aria-label
          no sighted reader ever sees. */}
      {onToggleSelect && (
        <p className="text-muted-foreground text-xs">
          {selectedIds && selectedIds.size > 0
            ? `${selectedIds.size} ${
                selectedIds.size === 1 ? noun.replace(/s$/, "") : noun
              } scoped to the agent.`
            : `Tick a circle to scope the agent to that ${noun.replace(
                /s$/,
                "",
              )}. Opening one only shows it.`}
        </p>
      )}

      {isLoading && <LoadingState variant="list" rows={5} />}

      {!isLoading && filtered.length === 0 && (
        <EmptyState title={emptyTitle} description={emptyDescription} variant="plain" />
      )}

      {!isLoading && filtered.length > 0 && (
        <ul
          // CAPPED AND SCROLLED, like the Documents panel above it. Fifteen stories ran
          // past the fold and took the rest of the column with them; the list should be
          // a fixed share of the sidebar however many the board returns.
          className="max-h-[26rem] overflow-y-auto focus-visible:outline-none"
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
              <li
                key={a.id}
                role="option"
                aria-selected={active}
                // SCROLLED INTO VIEW, because this list now has its own scrollbar. A
                // selected row below the fold is invisible however it is styled, and
                // arrow-key navigation walks straight off the bottom without it.
                ref={active ? selectedRowRef : undefined}
                className="flex items-center gap-2"
              >
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
                    // SELECTION MUST NOT LOOK LIKE HOVER. `active` used `bg-surface-1`
                    // — the very token the hover rule above sets — so the row you had
                    // opened was styled identically to whichever row the pointer
                    // happened to be over, and on a list of fifteen there was nothing
                    // to say which one the detail pane belonged to. The brand border
                    // and heavier surface are reserved for selection alone.
                    active
                      ? "border-[oklch(var(--brand-bright))] bg-surface-2 shadow-sm"
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

                {onDelete && isStoredArtifact(a.id) && (
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
