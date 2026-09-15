"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  Download,
  FileText,
  FolderOpen,
  Loader2,
  RefreshCw,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { PHASE_ALL, PHASE_LABEL } from "@/lib/agents";
import { listArtifacts } from "@/lib/api/artifacts";
import { qk } from "@/lib/api/query-keys";
import type { Artifact, Phase, ProjectId } from "@/lib/schemas";
import { isStoredArtifact } from "@/components/app/artifact-list";

/**
 * The PROJECT's approved artifacts — the third tab of the Orchestrator's right panel.
 *
 * NOT THE DELIVERABLES TAB, though the two look alike. Deliverables are what THIS
 * RUN's agents produced, kept in `orchestrator_deliverables` with no approval concept.
 * Artifacts are the documents any agent produced on its own page (or a person
 * uploaded) that went through approval into the project's record — the `artifacts`
 * table, the same list the standalone agents show under Documents, and the same
 * list the Orchestrator is told about on every turn (`orchestrator2/project_documents.py`).
 *
 * The document artifact system and the Orchestrator were built side by side, so a
 * BRD approved on the Requirements page was invisible from the Orchestrator of the
 * same project — on screen and in the model's context. This tab is the on-screen half.
 *
 * APPROVED ONLY. Pending and rejected rows exist in the same list the API returns, and
 * the standalone pages show them because that is where approval happens. Here they
 * would read as "the project has this", which is exactly what approval decides.
 */

/** The heading a document is filed under: the producing agent, or project-wide. */
const PROJECT_WIDE = "project";

export function isApprovedProjectArtifact(a: Artifact): boolean {
  // `isStoredArtifact` is the id-shape test the Documents panel uses: a board story
  // materialised from a run payload has an id like `{run}:story:{key}`, names no
  // row, and is "approved" by construction rather than by anyone.
  return a.status === "approved" && isStoredArtifact(a.id);
}

function groupKey(a: Artifact): string {
  return a.scope === "project" || !a.stage ? PROJECT_WIDE : a.stage;
}

function groupLabel(key: string, sample: Artifact | undefined): string {
  if (key === PROJECT_WIDE) return "Project-wide";
  // `phase` is the platform's spelling (`review`), `stage` the backend's
  // (`code_review`); `PHASE_LABEL` is keyed by the former and is the one answer per
  // agent across the app — which is how `plan` reads as "Project Manager" here.
  const phase = sample?.phase as Phase | undefined;
  if (phase && phase in PHASE_LABEL) return PHASE_LABEL[phase];
  return key;
}

function groupOrder(key: string, sample: Artifact | undefined): number {
  if (key === PROJECT_WIDE) return PHASE_ALL.length + 1;
  const idx = PHASE_ALL.indexOf((sample?.phase ?? "") as Phase);
  return idx < 0 ? PHASE_ALL.length : idx;
}

export function groupApprovedArtifacts(
  artifacts: readonly Artifact[],
): Array<{ key: string; label: string; items: Artifact[] }> {
  const byKey = new Map<string, Artifact[]>();
  for (const a of artifacts) {
    if (!isApprovedProjectArtifact(a)) continue;
    const key = groupKey(a);
    const arr = byKey.get(key) ?? [];
    arr.push(a);
    byKey.set(key, arr);
  }
  return Array.from(byKey.entries())
    .map(([key, items]) => ({
      key,
      label: groupLabel(key, items[0]),
      order: groupOrder(key, items[0]),
      // Newest approval first, so a re-approved document reads as current.
      items: [...items].sort((a, b) =>
        (b.approvedAt ?? b.createdAt ?? "").localeCompare(a.approvedAt ?? a.createdAt ?? ""),
      ),
    }))
    .sort((a, b) => a.order - b.order || a.label.localeCompare(b.label))
    .map(({ key, label, items }) => ({ key, label, items }));
}

/**
 * The query the tab and its badge share. Keyed by `qk.artifacts.forProject` so the
 * approve/reject/delete mutations on the standalone pages — which invalidate that
 * prefix — refresh this list too, and refetched on focus because approval happens on
 * another page and the person comes back here expecting to see it.
 */
export function useProjectArtifacts(projectId: string | null | undefined) {
  return useQuery({
    queryKey: qk.artifacts.forProject((projectId ?? "") as ProjectId),
    queryFn: () => listArtifacts(projectId as ProjectId),
    enabled: !!projectId,
    refetchOnWindowFocus: true,
    staleTime: 30_000,
  });
}

function sizeLabel(a: Artifact): string | null {
  const body = a.body as { sizeBytes?: unknown } | null | undefined;
  const n = typeof body?.sizeBytes === "number" ? body.sizeBytes : null;
  if (n === null || n < 0) return null;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function approvedOn(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return null;
  return at.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function ProjectArtifactsTab({ projectId }: { projectId: string }) {
  const q = useProjectArtifacts(projectId);
  const groups = React.useMemo(() => groupApprovedArtifacts(q.data ?? []), [q.data]);

  // Which groups are open — all of them, to begin with. A collapsed group here hides
  // approved work, and there is nothing to draw focus to the way the active stage
  // does on the Deliverables tab.
  const [collapsed, setCollapsed] = React.useState<Set<string>>(() => new Set());
  const toggle = (key: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const header = (
    <div className="border-line-soft flex items-center justify-between gap-2 border-b px-3.5 py-2.5">
      <p className="text-muted-foreground min-w-0 text-[11.5px] leading-snug">
        Approved into this project&apos;s record, by any agent. The Orchestrator reads
        this list on every turn.
      </p>
      <button
        type="button"
        onClick={() => void q.refetch()}
        disabled={q.isFetching}
        aria-label="Refresh artifacts"
        title="Refresh"
        className="text-muted-foreground hover:text-foreground hover:bg-panel-elevated/60 shrink-0 rounded-md p-1 transition-colors disabled:opacity-60"
      >
        <RefreshCw className={cn("size-3.5", q.isFetching && "animate-spin")} aria-hidden />
      </button>
    </div>
  );

  if (q.isLoading) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        {header}
        <div className="text-muted-foreground flex flex-1 items-center justify-center gap-2 text-[12.5px]">
          <Loader2 className="size-4 animate-spin" aria-hidden />
          Loading artifacts…
        </div>
      </div>
    );
  }

  if (q.isError) {
    // NOT the empty state. "No approved artifacts" about a project whose list could
    // not be fetched is the same lie the backend refuses to tell the agent.
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        {header}
        <div className="flex min-h-0 flex-1 items-center justify-center p-8">
          <div className="max-w-[16rem] text-center">
            <span className="border-warning/30 bg-warning/10 text-warning mx-auto mb-3 flex size-11 items-center justify-center rounded-full border">
              <AlertTriangle className="size-5" aria-hidden />
            </span>
            <h3 className="text-[13px] font-semibold text-foreground">
              The artifact list could not be read
            </h3>
            <p className="text-muted-foreground mt-1.5 text-[12px] leading-relaxed">
              {q.error instanceof Error ? q.error.message : "Try refreshing."}
            </p>
          </div>
        </div>
      </div>
    );
  }

  if (groups.length === 0) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        {header}
        <div className="flex min-h-0 flex-1 items-center justify-center p-8">
          <div className="max-w-[16rem] text-center">
            <span className="border-line-soft bg-panel-elevated/60 text-muted-foreground mx-auto mb-3 flex size-11 items-center justify-center rounded-full border">
              <FolderOpen className="size-5" aria-hidden />
            </span>
            <h3 className="text-[13px] font-semibold text-foreground">No approved artifacts yet</h3>
            <p className="text-muted-foreground mt-1.5 text-[12px] leading-relaxed">
              A document an agent produces on its own page, or one you upload there,
              appears here once it has been approved.
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {header}
      <div className="min-h-0 flex-1 overflow-auto px-2 py-2">
        {groups.map(({ key, label, items }) => {
          const open = !collapsed.has(key);
          return (
            <div key={key} className="mb-1" data-testid={`project-artifact-group-${key}`}>
              <button
                type="button"
                onClick={() => toggle(key)}
                aria-expanded={open}
                className="text-muted-foreground hover:text-foreground flex w-full items-center gap-1.5 rounded-[var(--radius)] px-2 py-1.5 text-left"
              >
                <span className="font-mono text-[10.5px] font-semibold uppercase tracking-[0.12em]">
                  {label}
                </span>
                <span className="text-muted-foreground/70 ml-auto font-mono text-[10.5px]">
                  {items.length}
                </span>
              </button>
              {open && (
                <ul className="space-y-0.5 pl-1">
                  {items.map((a) => (
                    <li key={a.id}>
                      <ProjectArtifactRow artifact={a} />
                    </li>
                  ))}
                </ul>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ProjectArtifactRow({ artifact }: { artifact: Artifact }) {
  const size = sizeLabel(artifact);
  const on = approvedOn(artifact.approvedAt);
  return (
    <div
      data-testid={`project-artifact-${artifact.id}`}
      className="border-line-soft hover:bg-panel-elevated/40 flex items-start gap-2.5 rounded-[var(--radius)] border border-transparent px-2.5 py-2 transition-colors hover:border-line-soft"
    >
      <span className="border-line-soft bg-panel-elevated/60 text-muted-foreground mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-[var(--radius)] border">
        <FileText className="size-3.5" aria-hidden />
      </span>
      <div className="min-w-0 flex-1">
        <div className="truncate text-[12.5px] font-medium text-foreground" title={artifact.title}>
          {artifact.title}
        </div>
        <div className="text-muted-foreground mt-0.5 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[11px]">
          <span className="text-success inline-flex items-center gap-1">
            <CheckCircle2 className="size-3" aria-hidden />
            Approved
          </span>
          {artifact.approvedBy && (
            <span className="truncate">
              by {artifact.approvedBy}
              {on ? ` · ${on}` : ""}
            </span>
          )}
          {!artifact.approvedBy && on && <span>{on}</span>}
          {size && <span className="font-mono text-[10.5px]">{size}</span>}
        </div>
      </div>
      {artifact.downloadUrl ? (
        <a
          href={artifact.downloadUrl}
          download
          aria-label={`Download ${artifact.title}`}
          title="Download"
          className="text-muted-foreground/70 hover:text-brand-bright hover:bg-brand-bright/10 mt-0.5 shrink-0 rounded-md p-1 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Download className="size-3.5" aria-hidden />
        </a>
      ) : (
        // Approved but no bytes to fetch — the upload failed. Say so rather than hide
        // the row: the record still lists it, and a missing icon reads as a glitch.
        <span
          className="text-muted-foreground/60 mt-0.5 shrink-0 font-mono text-[10px]"
          title="The file for this artifact is not stored"
        >
          not stored
        </span>
      )}
    </div>
  );
}
