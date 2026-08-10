"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRightCircle,
  Brain,
  ChevronRight,
  Download,
  ExternalLink,
  FileCode,
  FileEdit,
  FileJson,
  FileSearch,
  FileText,
  FolderTree,
  GitCommit,
  Image as ImageIcon,
  Link2,
  Loader2,
  Network,
  PanelRightClose,
  Play,
  User as UserIcon,
  UserCheck,
  Wifi,
  Wrench,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { API_BASE } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/ui/status-badge";
import { LoadingState } from "@/components/ui/loading-state";
import { ArtifactViewer } from "@/components/copilot/artifact-viewer";
import { getRun } from "@/lib/api/runs";
import { qk } from "@/lib/api/query-keys";
import { COPILOT_STAGES, ownerRoleLabel, stageLabel } from "@/lib/copilot/stages";
import type { GateState } from "@/lib/copilot/types";
import type { Artifact, ArtifactKind } from "@/lib/copilot/artifacts";
import type { CopilotActivityItem, CopilotConnState } from "@/lib/copilot/use-copilot";
import type { RunId } from "@/lib/schemas";

export interface ArtifactsPanelProps {
  runId: string;
  activeStage: string;
  gate: GateState | null;
  artifacts: Artifact[];
  openArtifactId: string | null;
  onSelectArtifact: (id: string | null) => void;
  streamingArtifactId: string | null;
  collapsed: boolean;
  onToggle: () => void;
  /** Live agent-action feed (Activity tab). */
  activity?: CopilotActivityItem[];
  /** True the instant a turn is dispatched, until stream_end. */
  working?: boolean;
  /** True when `working` and no event has landed for a while — surfaces a warning. */
  stuck?: boolean;
  /** Seconds since the last observed event — drives the stuck copy. */
  idleSeconds?: number;
  /** WS connection status — drives the "Reconnecting…" banner. */
  connectionStatus?: CopilotConnState;
  className?: string;
}

type Tab = "artifacts" | "activity" | "context";

// Resizable panel bounds. The panel is anchored right; its handle lives on the
// left edge (facing the chat). Design docs embed wide C4/ER diagrams and code, so
// the ceiling is generous (most of the screen) while the floor stays readable.
// Viewport cap enforced live so widening shrinks chat rather than breaking layout.
const MIN_WIDTH = 340;
const DEFAULT_WIDTH = 420;
const MAX_WIDTH_CAP = 1400;
const VIEWPORT_MAX_FRACTION = 0.85;
const KEY_STEP = 24;
const KEY_STEP_COARSE = 80;
const WIDTH_STORAGE_KEY = "copilot-artifacts-panel-width";

const clampWidth = (n: number, lo: number, hi: number) => Math.min(Math.max(n, lo), hi);

// Vertical split between the artifact LIST (top) and the VIEWER (bottom).
// The list height is persisted as a fraction of the split area so it survives
// panel-width and viewport changes. Floor keeps a couple of rows readable; the
// ceiling leaves the viewer usable.
const LIST_MIN_PX = 120;
const LIST_MAX_FRACTION = 0.7;
const LIST_DEFAULT_FRACTION = 0.4;
const LIST_HEIGHT_STORAGE_KEY = "copilot-artifacts-list-height";
const LIST_KEY_STEP = 24;
const LIST_KEY_STEP_COARSE = 80;

const clampNum = (n: number, lo: number, hi: number) => Math.min(Math.max(n, lo), hi);

/**
 * The right column: an `Artifacts | Activity | Context` tab strip.
 *
 *  - Artifacts — a stage-grouped list of produced artifacts (current stage
 *    expanded) beside/above the selected artifact in `ArtifactViewer`. A live
 *    streaming artifact shows selected + building. Empty state when none yet.
 *  - Activity — the live agent-action feed (tool calls, thinking, stage
 *    changes, turns) plus a working/stuck/reconnecting status line.
 *  - Context — the run's stage / approver / status / cost / id (relocated
 *    verbatim from the former `ArtifactPanel`).
 *
 * Collapsible; the parent auto-opens (uncollapses) when an artifact arrives.
 */
export function ArtifactsPanel({
  runId,
  activeStage,
  gate,
  artifacts,
  openArtifactId,
  onSelectArtifact,
  streamingArtifactId,
  collapsed,
  onToggle,
  activity = [],
  working = false,
  stuck = false,
  idleSeconds = 0,
  connectionStatus = "idle",
  className,
}: ArtifactsPanelProps) {
  const rid = runId as RunId;
  const runQ = useQuery({
    queryKey: qk.runs.detail(rid),
    queryFn: () => getRun(rid),
    enabled: !!runId,
    refetchInterval: 8_000,
  });
  const run = runQ.data;

  // While the Development stage is active, always surface a live code-tree
  // artifact so the driver can watch the repo as the agent clones/edits it —
  // even before the backend emits the authoritative `dev-code` artifact on
  // completion. De-duped by id: once the backend's `dev-code` arrives it wins.
  const effectiveArtifacts = React.useMemo<Artifact[]>(() => {
    if (activeStage !== "development" || artifacts.some((a) => a.id === "dev-code")) {
      return artifacts;
    }
    return [
      ...artifacts,
      {
        id: "dev-code",
        stage: "development",
        kind: "code-tree",
        title: "Repository code",
      },
    ];
  }, [artifacts, activeStage]);

  const [tab, setTab] = React.useState<Tab>("artifacts");

  // ── Resizable width ────────────────────────────────────────────────────────
  // SSR renders the default; the persisted value hydrates in an effect so server
  // and client markup match. Mirrors the AgentChatDrawer resize pattern.
  const [width, setWidth] = React.useState(DEFAULT_WIDTH);
  const [maxWidth, setMaxWidth] = React.useState(MAX_WIDTH_CAP);
  const [resizing, setResizing] = React.useState(false);
  const dragRef = React.useRef<{ startX: number; startW: number } | null>(null);
  const rafRef = React.useRef<number | null>(null);
  const pendingWidthRef = React.useRef<number | null>(null);

  const persistWidth = React.useCallback((next: number) => {
    try {
      window.localStorage.setItem(WIDTH_STORAGE_KEY, String(Math.round(next)));
    } catch {
      // private mode / storage disabled — width simply doesn't persist
    }
  }, []);

  React.useEffect(() => {
    const computeMax = () =>
      Math.min(MAX_WIDTH_CAP, Math.round(window.innerWidth * VIEWPORT_MAX_FRACTION));
    let saved = DEFAULT_WIDTH;
    try {
      const raw = window.localStorage.getItem(WIDTH_STORAGE_KEY);
      if (raw) saved = Number(raw) || DEFAULT_WIDTH;
    } catch {
      /* ignore */
    }
    const max = computeMax();
    setMaxWidth(max);
    setWidth(clampWidth(saved, MIN_WIDTH, max));
    const onResize = () => {
      const m = computeMax();
      setMaxWidth(m);
      setWidth((w) => clampWidth(w, MIN_WIDTH, m));
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const commitWidth = React.useCallback(
    (next: number) => {
      const clamped = clampWidth(Math.round(next), MIN_WIDTH, maxWidth);
      setWidth(clamped);
      persistWidth(clamped);
    },
    [maxWidth, persistWidth],
  );

  const onHandlePointerDown = React.useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      e.preventDefault();
      dragRef.current = { startX: e.clientX, startW: width };
      setResizing(true);
      try {
        e.currentTarget.setPointerCapture(e.pointerId);
      } catch {
        /* capture can fail if already released — drag still works */
      }
      document.body.style.userSelect = "none";
    },
    [width],
  );

  const onHandlePointerMove = React.useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      const drag = dragRef.current;
      if (!drag) return;
      // Panel is anchored right; dragging the handle left (smaller clientX) widens it.
      const next = clampWidth(drag.startW + (drag.startX - e.clientX), MIN_WIDTH, maxWidth);
      pendingWidthRef.current = next;
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      rafRef.current = requestAnimationFrame(() => setWidth(next));
    },
    [maxWidth],
  );

  const endDrag = React.useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!dragRef.current) return;
      dragRef.current = null;
      setResizing(false);
      document.body.style.userSelect = "";
      try {
        e.currentTarget.releasePointerCapture(e.pointerId);
      } catch {
        /* pointer already released */
      }
      const final = pendingWidthRef.current;
      pendingWidthRef.current = null;
      if (final !== null) {
        setWidth(final);
        persistWidth(final);
      }
    },
    [persistWidth],
  );

  const nudge = React.useCallback(
    (delta: number) => {
      setWidth((w) => {
        const next = clampWidth(w + delta, MIN_WIDTH, maxWidth);
        persistWidth(next);
        return next;
      });
    },
    [maxWidth, persistWidth],
  );

  const onHandleKeyDown = React.useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      const step = e.shiftKey ? KEY_STEP_COARSE : KEY_STEP;
      // ArrowLeft widens (matches dragging the handle left); ArrowRight narrows.
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        nudge(step);
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        nudge(-step);
      } else if (e.key === "Home") {
        e.preventDefault();
        commitWidth(maxWidth);
      } else if (e.key === "End") {
        e.preventDefault();
        commitWidth(MIN_WIDTH);
      }
    },
    [commitWidth, nudge, maxWidth],
  );

  // Any new artifact should pull focus to the Artifacts tab.
  const artifactCount = effectiveArtifacts.length;
  const prevCount = React.useRef(artifactCount);
  React.useEffect(() => {
    if (artifactCount > prevCount.current) setTab("artifacts");
    prevCount.current = artifactCount;
  }, [artifactCount]);

  if (collapsed) {
    return (
      <button
        type="button"
        onClick={onToggle}
        aria-label="Show artifacts panel"
        className={cn(
          "border-line-soft bg-panel-elevated/40 text-muted-foreground hover:text-foreground flex h-full w-10 shrink-0 flex-col items-center gap-3 border-l py-4",
          className,
        )}
      >
        <ChevronRight className="size-4 rotate-180" aria-hidden />
        <span className="[writing-mode:vertical-rl] font-mono text-[10px] uppercase tracking-[0.2em]">
          Artifacts
        </span>
        {artifactCount > 0 && (
          <span className="bg-brand-bright/15 text-brand-bright rounded-full px-1.5 py-0.5 font-mono text-[10px] font-semibold">
            {artifactCount}
          </span>
        )}
      </button>
    );
  }

  return (
    <aside
      aria-label="Run artifacts and context"
      style={{ width, maxWidth: "85vw" }}
      className={cn(
        "border-line-soft bg-panel-elevated/30 relative flex h-full min-h-0 shrink-0 flex-col border-l",
        resizing && "select-none",
        className,
      )}
    >
      {/* Drag-to-resize handle on the panel's left edge (faces the chat), mirroring
          the AgentChatDrawer. Keyboard: ←/→ resize, Shift coarse, Home/End max/min,
          double-click resets. */}
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize artifacts panel"
        aria-valuenow={Math.round(width)}
        aria-valuemin={MIN_WIDTH}
        aria-valuemax={Math.round(maxWidth)}
        tabIndex={0}
        onPointerDown={onHandlePointerDown}
        onPointerMove={onHandlePointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onKeyDown={onHandleKeyDown}
        onDoubleClick={() => commitWidth(DEFAULT_WIDTH)}
        title="Drag to resize · double-click to reset"
        className="group focus-visible:ring-ring focus-visible:ring-offset-background absolute inset-y-0 left-0 z-20 flex w-3 -translate-x-1/2 cursor-col-resize touch-none items-center justify-center focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-1"
      >
        <span
          aria-hidden
          className={cn(
            "bg-line-soft group-hover:bg-brand-bright h-full w-px transition-colors duration-150 motion-reduce:transition-none",
            resizing && "bg-brand-bright",
          )}
        />
        <span
          aria-hidden
          className={cn(
            "group-hover:bg-brand-bright absolute h-9 w-1 rounded-full bg-transparent transition-all duration-150 group-hover:w-1.5 motion-reduce:transition-none",
            resizing && "bg-brand-bright w-1.5",
          )}
        />
      </div>

      {/* Tab strip */}
      <div className="border-line-soft flex items-center justify-between border-b pl-2 pr-2">
        <div role="tablist" aria-label="Panel view" className="flex">
          <TabButton
            id="artifacts"
            active={tab === "artifacts"}
            onClick={() => setTab("artifacts")}
            count={artifactCount}
          >
            Artifacts
          </TabButton>
          <TabButton
            id="activity"
            active={tab === "activity"}
            onClick={() => setTab("activity")}
          >
            <span className="inline-flex items-center gap-1">
              Activity
              {(working || stuck) && (
                <span
                  className={cn(
                    "size-1.5 rounded-full",
                    stuck ? "bg-warning" : "bg-brand-bright animate-pulse",
                  )}
                  aria-hidden
                />
              )}
            </span>
          </TabButton>
          <TabButton id="context" active={tab === "context"} onClick={() => setTab("context")}>
            Context
          </TabButton>
        </div>
        <Button
          variant="ghost"
          size="icon"
          onClick={onToggle}
          aria-label="Collapse panel"
          className="text-muted-foreground hover:text-foreground size-7"
        >
          <PanelRightClose className="size-4" aria-hidden />
        </Button>
      </div>

      {tab === "artifacts" ? (
        <ArtifactsTab
          runId={runId}
          artifacts={effectiveArtifacts}
          activeStage={activeStage}
          openArtifactId={openArtifactId}
          onSelectArtifact={onSelectArtifact}
          streamingArtifactId={streamingArtifactId}
        />
      ) : tab === "activity" ? (
        <ActivityTab
          activity={activity}
          working={working}
          stuck={stuck}
          idleSeconds={idleSeconds}
          connectionStatus={connectionStatus}
        />
      ) : (
        <ContextTab runId={runId} run={run} isLoading={runQ.isLoading} activeStage={activeStage} gate={gate} />
      )}
    </aside>
  );
}

// ── Tab: Artifacts ────────────────────────────────────────────────────────

function ArtifactsTab({
  runId,
  artifacts,
  activeStage,
  openArtifactId,
  onSelectArtifact,
  streamingArtifactId,
}: {
  runId: string;
  artifacts: Artifact[];
  activeStage: string;
  openArtifactId: string | null;
  onSelectArtifact: (id: string | null) => void;
  streamingArtifactId: string | null;
}) {
  // Strictly the artifact matching the lifted selection — no `artifacts[0]`
  // fallback, so a null selection resolves to the empty state instead of stickily
  // re-showing the first item.
  const selected = artifacts.find((a) => a.id === openArtifactId) ?? null;

  // If the selected id no longer exists (e.g. its stage re-ran and replaced its
  // artifacts), clear the selection so the viewer doesn't show a phantom item.
  React.useEffect(() => {
    if (openArtifactId && !artifacts.some((a) => a.id === openArtifactId)) {
      onSelectArtifact(null);
    }
  }, [openArtifactId, artifacts, onSelectArtifact]);

  // Group artifacts by stage, ordered by the pipeline sequence.
  const groups = React.useMemo(() => groupByStage(artifacts), [artifacts]);

  // Which stage groups are expanded — the active stage starts open.
  const [expanded, setExpanded] = React.useState<Set<string>>(() => new Set([activeStage]));
  React.useEffect(() => {
    setExpanded((prev) => (prev.has(activeStage) ? prev : new Set(prev).add(activeStage)));
  }, [activeStage]);

  // Collapsing a group deselects the artifact if the selection lives inside it —
  // otherwise the viewer would keep showing an item whose row is now hidden.
  const toggleGroup = React.useCallback(
    (stage: string) => {
      setExpanded((prev) => {
        const next = new Set(prev);
        if (next.has(stage)) {
          next.delete(stage);
          const selectedStage = artifacts.find((a) => a.id === openArtifactId)?.stage;
          if (selectedStage === stage) onSelectArtifact(null);
        } else {
          next.add(stage);
        }
        return next;
      });
    },
    [artifacts, openArtifactId, onSelectArtifact],
  );

  // Clicking a row toggles it: re-selecting the open artifact closes it (→ empty state).
  const selectRow = React.useCallback(
    (id: string) => onSelectArtifact(id === openArtifactId ? null : id),
    [openArtifactId, onSelectArtifact],
  );

  // ── Resizable list ↕ viewer split ──────────────────────────────────────────
  // Mirrors the AgentChatDrawer pointer-drag pattern but for HEIGHT (clientY).
  // The list height is stored as a fraction of the split area's height; it's
  // resolved to px against the live container height, so it holds up as the
  // panel width (which reflows the list) and the viewport change.
  const splitRef = React.useRef<HTMLDivElement>(null);
  const [splitH, setSplitH] = React.useState(0);
  const [listFraction, setListFraction] = React.useState(LIST_DEFAULT_FRACTION);
  const [resizing, setResizing] = React.useState(false);
  const dragRef = React.useRef<{ startY: number; startH: number; areaH: number } | null>(null);
  const rafRef = React.useRef<number | null>(null);
  const pendingRef = React.useRef<number | null>(null);

  const persistFraction = React.useCallback((f: number) => {
    try {
      window.localStorage.setItem(LIST_HEIGHT_STORAGE_KEY, f.toFixed(4));
    } catch {
      // private mode / storage disabled — split simply doesn't persist
    }
  }, []);

  // Hydrate persisted fraction + track the split area's live height (ResizeObserver).
  React.useEffect(() => {
    try {
      const raw = window.localStorage.getItem(LIST_HEIGHT_STORAGE_KEY);
      const parsed = raw === null ? NaN : Number(raw);
      if (Number.isFinite(parsed)) {
        setListFraction(clampNum(parsed, 0, LIST_MAX_FRACTION));
      }
    } catch {
      /* ignore */
    }
    const el = splitRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const h = entries[0]?.contentRect.height ?? 0;
      if (h > 0) setSplitH(h);
    });
    ro.observe(el);
    setSplitH(el.getBoundingClientRect().height);
    return () => ro.disconnect();
  }, []);

  // Resolve the stored fraction to a clamped px height against the live area.
  const listHeight = React.useMemo(() => {
    if (splitH <= 0) return null;
    const max = Math.max(LIST_MIN_PX, Math.round(splitH * LIST_MAX_FRACTION));
    return clampNum(Math.round(splitH * listFraction), LIST_MIN_PX, max);
  }, [splitH, listFraction]);

  const commitFraction = React.useCallback(
    (px: number) => {
      const area = splitRef.current?.getBoundingClientRect().height ?? splitH;
      if (area <= 0) return;
      const max = Math.max(LIST_MIN_PX, Math.round(area * LIST_MAX_FRACTION));
      const clamped = clampNum(Math.round(px), LIST_MIN_PX, max);
      const f = clampNum(clamped / area, 0, LIST_MAX_FRACTION);
      setListFraction(f);
      persistFraction(f);
    },
    [splitH, persistFraction],
  );

  const onHandlePointerDown = React.useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      e.preventDefault();
      const area = splitRef.current?.getBoundingClientRect().height ?? 0;
      dragRef.current = {
        startY: e.clientY,
        startH: listHeight ?? Math.round(area * listFraction),
        areaH: area,
      };
      setResizing(true);
      try {
        e.currentTarget.setPointerCapture(e.pointerId);
      } catch {
        /* capture can fail if already released — drag still works */
      }
      document.body.style.userSelect = "none";
    },
    [listHeight, listFraction],
  );

  const onHandlePointerMove = React.useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      const drag = dragRef.current;
      if (!drag || drag.areaH <= 0) return;
      // Dragging DOWN (larger clientY) grows the list; UP shrinks it.
      const max = Math.max(LIST_MIN_PX, Math.round(drag.areaH * LIST_MAX_FRACTION));
      const next = clampNum(drag.startH + (e.clientY - drag.startY), LIST_MIN_PX, max);
      pendingRef.current = next / drag.areaH;
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      rafRef.current = requestAnimationFrame(() =>
        setListFraction(clampNum(next / drag.areaH, 0, LIST_MAX_FRACTION)),
      );
    },
    [],
  );

  const endDrag = React.useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!dragRef.current) return;
      dragRef.current = null;
      setResizing(false);
      document.body.style.userSelect = "";
      try {
        e.currentTarget.releasePointerCapture(e.pointerId);
      } catch {
        /* pointer already released */
      }
      const final = pendingRef.current;
      pendingRef.current = null;
      if (final !== null) {
        const f = clampNum(final, 0, LIST_MAX_FRACTION);
        setListFraction(f);
        persistFraction(f);
      }
    },
    [persistFraction],
  );

  const onHandleKeyDown = React.useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      const step = e.shiftKey ? LIST_KEY_STEP_COARSE : LIST_KEY_STEP;
      const current = listHeight ?? 0;
      // ArrowDown grows the list (matches dragging the handle down); ArrowUp shrinks.
      if (e.key === "ArrowDown") {
        e.preventDefault();
        commitFraction(current + step);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        commitFraction(current - step);
      }
    },
    [listHeight, commitFraction],
  );

  const resetSplit = React.useCallback(() => {
    setListFraction(LIST_DEFAULT_FRACTION);
    persistFraction(LIST_DEFAULT_FRACTION);
  }, [persistFraction]);

  if (artifacts.length === 0) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center p-8">
        <div className="max-w-[15rem] text-center">
          <span className="border-line-soft bg-panel-elevated/60 text-muted-foreground mx-auto mb-3 flex size-11 items-center justify-center rounded-full border">
            <FileText className="size-5" aria-hidden />
          </span>
          <h3 className="text-[13px] font-semibold text-foreground">No artifacts yet</h3>
          <p className="text-muted-foreground mt-1.5 text-[12px] leading-relaxed">
            When the {stageLabel(activeStage)} agent produces its document, diagrams, or code, they
            open here.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div ref={splitRef} className="flex min-h-0 flex-1 flex-col">
      {/* List */}
      <div
        style={listHeight !== null ? { height: listHeight } : undefined}
        className={cn(
          "overflow-auto px-2 py-2",
          listHeight !== null ? "shrink-0" : "max-h-[40%] shrink-0",
        )}
      >
        {groups.map(({ stage, items }) => {
          const open = expanded.has(stage);
          return (
            <div key={stage} className="mb-1">
              <button
                type="button"
                onClick={() => toggleGroup(stage)}
                aria-expanded={open}
                className="text-muted-foreground hover:text-foreground flex w-full items-center gap-1.5 rounded-[var(--radius)] px-2 py-1.5 text-left"
              >
                <ChevronRight
                  className={cn("size-3.5 transition-transform", open && "rotate-90")}
                  aria-hidden
                />
                <span className="font-mono text-[10.5px] font-semibold uppercase tracking-[0.12em]">
                  {stageLabel(stage)}
                </span>
                <span className="text-muted-foreground/70 ml-auto font-mono text-[10.5px]">
                  {items.length}
                </span>
              </button>
              {open && (
                <ul className="space-y-0.5 pl-1">
                  {items.map((a) => (
                    <li key={a.id}>
                      <ArtifactRow
                        artifact={a}
                        active={selected?.id === a.id}
                        streaming={streamingArtifactId === a.id}
                        onSelect={() => selectRow(a.id)}
                      />
                    </li>
                  ))}
                </ul>
              )}
            </div>
          );
        })}
      </div>

      {/* Drag-to-resize divider between the list (top) and viewer (bottom).
          Mirrors the panel's width handle but on the Y axis: drag ↕, ArrowUp/Down
          resize (Shift coarse), double-click resets. A subtle centered grip line
          reads as a handle. */}
      <div
        role="separator"
        aria-orientation="horizontal"
        aria-label="Resize artifact list"
        aria-valuenow={listHeight ?? undefined}
        aria-valuemin={LIST_MIN_PX}
        aria-valuemax={splitH > 0 ? Math.round(splitH * LIST_MAX_FRACTION) : undefined}
        tabIndex={0}
        onPointerDown={onHandlePointerDown}
        onPointerMove={onHandlePointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onKeyDown={onHandleKeyDown}
        onDoubleClick={resetSplit}
        title="Drag to resize · double-click to reset"
        className={cn(
          "group border-line-soft focus-visible:ring-ring focus-visible:ring-offset-background relative z-10 flex h-[7px] shrink-0 cursor-row-resize touch-none items-center justify-center border-y focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-1",
          resizing ? "bg-brand-bright/10" : "bg-line-soft/40 hover:bg-brand-bright/10",
        )}
      >
        <span
          aria-hidden
          className={cn(
            "h-0.5 w-8 rounded-full transition-all duration-150 motion-reduce:transition-none",
            resizing
              ? "bg-brand-bright w-10"
              : "bg-muted-foreground/40 group-hover:bg-brand-bright group-hover:w-10",
          )}
        />
      </div>

      {/* Viewer */}
      <div className="flex min-h-0 flex-1 flex-col overflow-auto">
        {selected ? (
          <>
            <div className="border-line-soft flex items-center gap-2 border-b px-4 py-2.5">
              <ArtifactKindIcon kind={selected.kind} className="text-muted-foreground size-3.5" />
              <span className="min-w-0 flex-1 truncate text-[12.5px] font-semibold text-foreground">
                {selected.title}
              </span>
              {streamingArtifactId === selected.id ? (
                <span className="text-brand-bright inline-flex items-center gap-1 font-mono text-[10.5px]">
                  <Loader2 className="size-3 animate-spin" aria-hidden />
                  streaming
                </span>
              ) : (
                DOCX_DOWNLOADABLE.has(selected.kind) &&
                !!selected.content?.trim() && <DownloadDocxButton artifact={selected} />
              )}
            </div>
            <ArtifactViewer
              key={selected.id}
              artifact={selected}
              runId={runId}
              streaming={streamingArtifactId === selected.id}
              className="flex-1"
            />
          </>
        ) : (
          <div className="text-muted-foreground flex flex-1 items-center justify-center text-[12.5px]">
            Select an artifact to view it.
          </div>
        )}
      </div>
    </div>
  );
}

// ── Tab: Activity ─────────────────────────────────────────────────────────

const MUTED_RING = "border-line-soft bg-panel-elevated/60 text-muted-foreground";
const MUTED_BORDER_L = "border-l-line-soft/40";

/**
 * Tailwind's scanner needs literal class names, so every tone below is a
 * fully-spelled-out string (never built via template interpolation) — the
 * `ring` triad goes on the icon circle, `borderL` is the row's subtle
 * left-edge accent.
 */
function activityTone(item: CopilotActivityItem): { icon: typeof Wrench; ring: string; borderL: string } {
  if (item.kind === "turn") {
    return {
      icon: UserIcon,
      ring: "border-info/30 bg-info/10 text-info",
      borderL: "border-l-info/40",
    };
  }
  if (item.kind === "thinking") {
    return {
      icon: Brain,
      ring: "border-brand-bright/30 bg-brand-bright/10 text-brand-bright",
      borderL: "border-l-brand-bright/40",
    };
  }
  if (item.kind === "stage") {
    return {
      icon: ArrowRightCircle,
      ring: "border-success/30 bg-success/10 text-success",
      borderL: "border-l-success/40",
    };
  }

  // kind === "tool" — categorize by the raw tool name in `label`.
  const label = item.label.toLowerCase();

  if (label === "clone_repo" || label.startsWith("prepare")) {
    return {
      icon: Download,
      ring: "border-info/30 bg-info/10 text-info",
      borderL: "border-l-info/40",
    };
  }
  if (
    label === "read_file" ||
    label.startsWith("list") ||
    label.startsWith("get") ||
    label.startsWith("search") ||
    label.startsWith("analyze") ||
    label.startsWith("semantic")
  ) {
    return { icon: FileSearch, ring: MUTED_RING, borderL: MUTED_BORDER_L };
  }
  if (
    label === "write_file" ||
    label === "edit_file" ||
    label === "create_file" ||
    label.startsWith("apply")
  ) {
    return {
      icon: FileEdit,
      ring: "border-warning/30 bg-warning/10 text-warning",
      borderL: "border-l-warning/40",
    };
  }
  if (
    label === "git_commit" ||
    label === "push_branch" ||
    label.endsWith("_branch") ||
    label === "create_pull_request" ||
    label.endsWith("_pr")
  ) {
    return {
      icon: GitCommit,
      ring: "border-success/30 bg-success/10 text-success",
      borderL: "border-l-success/40",
    };
  }
  if (
    label.startsWith("run") ||
    label.startsWith("dotnet") ||
    label.startsWith("execute") ||
    label === "semgrep" ||
    label === "trivy" ||
    label === "gitleaks"
  ) {
    return {
      icon: Play,
      ring: "border-brand-bright/30 bg-brand-bright/10 text-brand-bright",
      borderL: "border-l-brand-bright/40",
    };
  }

  return { icon: Wrench, ring: MUTED_RING, borderL: MUTED_BORDER_L };
}

function formatClock(ts: string): string {
  try {
    return new Date(ts).toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return "";
  }
}

/**
 * The live agent-action feed: every `tool.call` / `agent.thinking` /
 * `stage.changed` / turn boundary the hook has observed, newest-last, each
 * with a spinner while `status === "running"`. A status line up top mirrors
 * the old "Agent Tracker" affordance — working / stuck / reconnecting / idle.
 */
function ActivityTab({
  activity,
  working,
  stuck,
  idleSeconds,
  connectionStatus,
}: {
  activity: CopilotActivityItem[];
  working: boolean;
  stuck: boolean;
  idleSeconds: number;
  connectionStatus: CopilotConnState;
}) {
  const listRef = React.useRef<HTMLDivElement>(null);
  React.useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [activity.length]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* Status line */}
      <div className="border-line-soft space-y-2 border-b px-3.5 py-3">
        {connectionStatus === "reconnecting" && (
          <div className="border-warning/35 bg-warning/[0.06] text-warning flex items-center gap-2 rounded-[var(--radius)] border px-2.5 py-1.5 text-[11.5px]">
            <Wifi className="size-3.5 animate-pulse" aria-hidden />
            Reconnecting…
          </div>
        )}
        {stuck ? (
          <div className="text-warning flex items-center gap-1.5 text-[12px] font-medium">
            <AlertTriangle className="size-3.5" aria-hidden />
            No response for {idleSeconds}s — the agent may be stuck or disconnected
          </div>
        ) : working ? (
          <div className="text-brand-bright flex items-center gap-1.5 text-[12px] font-medium">
            <Loader2 className="size-3.5 animate-spin" aria-hidden />
            Working…
          </div>
        ) : (
          <div className="text-muted-foreground flex items-center gap-1.5 text-[12px] font-medium">
            <span className="bg-muted-foreground/50 size-1.5 rounded-full" aria-hidden />
            Idle
          </div>
        )}
      </div>

      {/* Feed */}
      {activity.length === 0 ? (
        <div className="flex min-h-0 flex-1 items-center justify-center p-8">
          <div className="max-w-[15rem] text-center">
            <span className="border-line-soft bg-panel-elevated/60 text-muted-foreground mx-auto mb-3 flex size-11 items-center justify-center rounded-full border">
              <ArrowRightCircle className="size-5" aria-hidden />
            </span>
            <h3 className="text-[13px] font-semibold text-foreground">No activity yet</h3>
            <p className="text-muted-foreground mt-1.5 text-[12px] leading-relaxed">
              Tool calls, thinking, and stage changes appear here as the agent works.
            </p>
          </div>
        </div>
      ) : (
        <div ref={listRef} className="min-h-0 flex-1 space-y-1 overflow-auto px-3 py-2.5">
          {activity.map((a) => {
            const running = a.status === "running";
            const tone = activityTone(a);
            const Icon = tone.icon;
            return (
              <div
                key={a.id}
                className={cn(
                  "border-line-soft bg-panel-elevated/30 flex items-start gap-2 rounded-[var(--radius)] border px-2.5 py-1.5",
                  !running && ["border-l-2", tone.borderL],
                )}
              >
                <span
                  className={cn(
                    "mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full border",
                    running
                      ? "border-brand-bright/30 bg-brand-bright/10 text-brand-bright"
                      : tone.ring,
                  )}
                >
                  {running ? (
                    <Loader2 className="size-3 animate-spin" aria-hidden />
                  ) : (
                    <Icon className="size-3" aria-hidden />
                  )}
                </span>
                <span className="min-w-0 flex-1 truncate text-[12px] text-foreground/90">
                  {a.label}
                </span>
                <span className="text-muted-foreground/70 shrink-0 font-mono text-[10px]">
                  {formatClock(a.ts)}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// Text artifacts we can render into a Word document (they carry inline markdown).
const DOCX_DOWNLOADABLE: ReadonlySet<ArtifactKind> = new Set<ArtifactKind>([
  "markdown",
  "openapi",
  "code",
  "mermaid",
]);

/** POST the artifact's markdown to the BFF and trigger a .docx download. */
async function downloadArtifactDocx(title: string, markdown: string): Promise<void> {
  const res = await fetch(`${API_BASE}/artifacts/export-docx`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, markdown }),
  });
  if (!res.ok) throw new Error(`export failed (${res.status})`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${title}.docx`.replace(/[^A-Za-z0-9._ -]+/g, "");
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function DownloadDocxButton({ artifact }: { artifact: Artifact }) {
  const [state, setState] = React.useState<"idle" | "busy" | "error">("idle");
  const onClick = React.useCallback(
    async (e: React.MouseEvent) => {
      e.stopPropagation(); // don't toggle row selection
      if (state === "busy") return;
      setState("busy");
      try {
        await downloadArtifactDocx(artifact.title, artifact.content ?? "");
        setState("idle");
      } catch {
        setState("error");
        setTimeout(() => setState("idle"), 2500);
      }
    },
    [artifact.title, artifact.content, state],
  );
  return (
    <button
      type="button"
      onClick={onClick}
      title={state === "error" ? "Download failed — try again" : "Download as Word (.docx)"}
      aria-label={`Download ${artifact.title} as a Word document`}
      className={cn(
        "shrink-0 rounded-md p-1 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        state === "error"
          ? "text-destructive"
          : "text-muted-foreground/70 hover:text-brand-bright hover:bg-brand-bright/10",
      )}
    >
      {state === "busy" ? (
        <Loader2 className="size-3.5 animate-spin" aria-hidden />
      ) : (
        <Download className="size-3.5" aria-hidden />
      )}
    </button>
  );
}

function ArtifactRow({
  artifact,
  active,
  streaming,
  onSelect,
}: {
  artifact: Artifact;
  active: boolean;
  streaming: boolean;
  onSelect: () => void;
}) {
  const canDownload =
    !streaming && DOCX_DOWNLOADABLE.has(artifact.kind) && !!artifact.content?.trim();
  return (
    <div
      className={cn(
        "flex items-center gap-1 rounded-[var(--radius)] border pr-1.5 transition-colors",
        active
          ? "border-line-soft bg-panel-elevated/70"
          : "border-transparent hover:border-line-soft hover:bg-panel-elevated/40",
      )}
    >
      <button
        type="button"
        onClick={onSelect}
        aria-current={active}
        className="flex min-w-0 flex-1 items-center gap-2.5 rounded-[var(--radius)] px-2.5 py-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1"
      >
        <span
          className={cn(
            "flex size-7 shrink-0 items-center justify-center rounded-[var(--radius)] border",
            active
              ? "border-brand-bright/30 bg-brand-bright/10 text-brand-bright"
              : "border-line-soft bg-panel-elevated/60 text-muted-foreground",
          )}
        >
          <ArtifactKindIcon kind={artifact.kind} className="size-3.5" />
        </span>
        <span className="min-w-0 flex-1 truncate text-[12.5px] font-medium text-foreground">
          {artifact.title}
        </span>
      </button>
      {streaming ? (
        <Loader2 className="text-brand-bright mr-1 size-3.5 shrink-0 animate-spin" aria-hidden />
      ) : (
        <>
          <span className="text-muted-foreground/70 shrink-0 font-mono text-[10px] uppercase">
            {artifact.kind}
          </span>
          {canDownload && <DownloadDocxButton artifact={artifact} />}
        </>
      )}
    </div>
  );
}

const KIND_ICON: Record<ArtifactKind, typeof FileText> = {
  markdown: FileText,
  mermaid: Network,
  openapi: FileJson,
  code: FileCode,
  image: ImageIcon,
  download: FileText,
  "code-tree": FolderTree,
  "file-tree": FolderTree,
  link: Link2,
};

function ArtifactKindIcon({ kind, className }: { kind: ArtifactKind; className?: string }) {
  const Icon = KIND_ICON[kind] ?? FileText;
  return <Icon className={className} aria-hidden />;
}

function groupByStage(artifacts: Artifact[]): Array<{ stage: string; items: Artifact[] }> {
  const byStage = new Map<string, Artifact[]>();
  for (const a of artifacts) {
    const arr = byStage.get(a.stage) ?? [];
    arr.push(a);
    byStage.set(a.stage, arr);
  }
  const order = COPILOT_STAGES.map((s) => s.id);
  return Array.from(byStage.entries())
    .sort(([a], [b]) => {
      const ia = order.indexOf(a);
      const ib = order.indexOf(b);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    })
    .map(([stage, items]) => ({ stage, items }));
}

function TabButton({
  id,
  active,
  onClick,
  count,
  children,
}: {
  id: Tab;
  active: boolean;
  onClick: () => void;
  count?: number;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      role="tab"
      id={`tab-${id}`}
      aria-selected={active}
      onClick={onClick}
      className={cn(
        "relative flex items-center gap-1.5 px-3 py-3 font-mono text-[10.5px] font-semibold uppercase tracking-[0.14em] transition-colors",
        active ? "text-brand-bright" : "text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
      {count !== undefined && count > 0 && (
        <span
          className={cn(
            "rounded-full px-1.5 py-0.5 text-[9.5px] leading-none",
            active ? "bg-brand-bright/15 text-brand-bright" : "bg-muted text-muted-foreground",
          )}
        >
          {count}
        </span>
      )}
      {active && <span className="bg-brand-bright absolute inset-x-2 bottom-0 h-0.5 rounded-full" />}
    </button>
  );
}

// ── Tab: Context (relocated verbatim from the former ArtifactPanel) ─────────

function ContextTab({
  runId,
  run,
  isLoading,
  activeStage,
  gate,
}: {
  runId: string;
  run: Awaited<ReturnType<typeof getRun>> | undefined;
  isLoading: boolean;
  activeStage: string;
  gate: GateState | null;
}) {
  void runId;
  const stage = COPILOT_STAGES.find((s) => s.id === activeStage);
  const owner = gate ? ownerRoleLabel(gate.owner_role) : (stage?.ownerRole ?? "Product Manager");

  return (
    <div className="min-h-0 flex-1 overflow-auto">
      {/* Active stage artifact */}
      <section className="border-line-soft space-y-3 border-b px-4 py-4">
        <h3 className="text-muted-foreground text-[11px] font-semibold uppercase tracking-wider">
          Current stage
        </h3>
        <div className="border-line-soft bg-panel-elevated/40 flex items-start gap-3 rounded-[var(--radius)] border p-3">
          <span className="border-brand-bright/30 bg-brand-bright/10 text-brand-bright mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-[var(--radius)] border">
            <FileText className="size-4" aria-hidden />
          </span>
          <div className="min-w-0">
            <p className="text-[13px] font-semibold text-foreground">{stageLabel(activeStage)}</p>
            <p className="text-muted-foreground text-[11.5px] leading-snug">
              {ARTIFACT_HINT[activeStage] ?? "The stage agent's produced artifact appears here."}
            </p>
          </div>
        </div>
        {run && (
          <Link
            href={`/runs/${run.id}`}
            className="text-info inline-flex items-center gap-1.5 text-[12px] hover:underline"
          >
            <ExternalLink className="size-3.5" aria-hidden />
            Open full run view
          </Link>
        )}
      </section>

      {/* Who needs to approve */}
      <section className="border-line-soft space-y-2.5 border-b px-4 py-4">
        <h3 className="text-muted-foreground text-[11px] font-semibold uppercase tracking-wider">
          Who approves
        </h3>
        <div
          className={cn(
            "flex items-center gap-2.5 rounded-[var(--radius)] border px-3 py-2.5",
            gate?.status === "awaiting_gate"
              ? "border-warning/35 bg-warning/[0.06]"
              : "border-line-soft bg-panel-elevated/40",
          )}
        >
          <span
            className={cn(
              "flex size-7 shrink-0 items-center justify-center rounded-full border",
              gate?.status === "awaiting_gate"
                ? "border-warning/40 bg-warning/10 text-warning"
                : "border-line-soft text-muted-foreground",
            )}
          >
            <UserCheck className="size-3.5" aria-hidden />
          </span>
          <div className="min-w-0">
            <p className="text-[12.5px] font-medium text-foreground">{owner}</p>
            <p className="text-muted-foreground text-[11px]">
              {stage?.mandatory
                ? "Mandatory gate — never auto-approved"
                : stage?.auto
                  ? "Auto-approved on completion"
                  : "Approval-required gate"}
            </p>
          </div>
        </div>
      </section>

      {/* Run metadata */}
      <section className="space-y-3 px-4 py-4">
        <h3 className="text-muted-foreground text-[11px] font-semibold uppercase tracking-wider">
          Run
        </h3>
        {isLoading ? (
          <LoadingState variant="list" rows={2} />
        ) : run ? (
          <dl className="space-y-2 text-[12px]">
            <Row label="Status">
              <StatusBadge status={run.status} />
            </Row>
            <Row label="Cost">
              <span className="font-mono">${run.cost.usd.toFixed(run.cost.usd < 0.01 ? 4 : 3)}</span>
            </Row>
            <Row label="Tokens">
              <span className="font-mono">
                {(run.cost.inputTokens + run.cost.outputTokens).toLocaleString()}
              </span>
            </Row>
            <Row label="Run id">
              <span className="text-muted-foreground truncate font-mono text-[11px]">{run.id}</span>
            </Row>
          </dl>
        ) : (
          <p className="text-muted-foreground text-[12px]">Run metadata unavailable.</p>
        )}
      </section>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 text-right">{children}</dd>
    </div>
  );
}

const ARTIFACT_HINT: Record<string, string> = {
  requirements: "Baselined stories + acceptance criteria (BRD).",
  design: "HLD, LLD, C4, API contracts, DB schema, ADRs (SDD).",
  development: "Implementation diff + pull request.",
  code_review: "Review findings + merge recommendation.",
  security: "SCA / SAST / secrets findings + SBOM.",
  testing: "Generated tests, run results, coverage.",
  deployment: "Deploy package, readiness, runbooks.",
  documentation: "Doc set, changelog, RTM, compliance.",
};
