"use client";

import * as React from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { TrendingDown, TrendingUp } from "lucide-react";

import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { CoverageFile } from "@/lib/schemas";

export interface CoverageHeatmapProps {
  files: readonly CoverageFile[];
  onSelect?: (file: CoverageFile) => void;
  className?: string;
}

type SortKey = "percent-asc" | "percent-desc" | "path" | "delta";

function tone(pct: number): string {
  if (pct >= 90) return "bg-success/15 border-success/30";
  if (pct >= 75) return "bg-info/15 border-info/30";
  if (pct >= 50) return "bg-warning/20 border-warning/40";
  return "bg-destructive/15 border-destructive/30";
}

function barTone(pct: number): string {
  if (pct >= 90) return "bg-success";
  if (pct >= 75) return "bg-info";
  if (pct >= 50) return "bg-warning";
  return "bg-destructive";
}

/**
 * Virtualized file-coverage list — scales smoothly to 1000+ files.
 * Sorted controls let you surface weakest coverage quickly.
 */
export function CoverageHeatmap({ files, onSelect, className }: CoverageHeatmapProps) {
  const [filter, setFilter] = React.useState("");
  const [sort, setSort] = React.useState<SortKey>("percent-asc");

  const processed = React.useMemo(() => {
    const q = filter.trim().toLowerCase();
    let list = files.filter((f) => !q || f.path.toLowerCase().includes(q));
    list = [...list].sort((a, b) => {
      if (sort === "percent-asc") return a.percent - b.percent;
      if (sort === "percent-desc") return b.percent - a.percent;
      if (sort === "delta") return (a.delta ?? 0) - (b.delta ?? 0);
      return a.path.localeCompare(b.path);
    });
    return list;
  }, [files, filter, sort]);

  const totals = React.useMemo(() => {
    const lines = files.reduce((s, f) => s + f.lines, 0);
    const covered = files.reduce((s, f) => s + f.covered, 0);
    return {
      lines,
      covered,
      percent: lines === 0 ? 0 : (covered / lines) * 100,
    };
  }, [files]);

  const parentRef = React.useRef<HTMLDivElement>(null);
  const virt = useVirtualizer({
    count: processed.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 40,
    overscan: 8,
  });

  return (
    <section className={cn("space-y-3", className)} aria-label="Coverage heatmap">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-baseline gap-3">
          <div>
            <p className="text-muted-foreground text-xs uppercase tracking-wider">
              Total coverage
            </p>
            <p className="font-mono text-xl font-semibold">{totals.percent.toFixed(1)}%</p>
          </div>
          <p className="text-muted-foreground text-xs">
            {totals.covered.toLocaleString()} / {totals.lines.toLocaleString()} lines in{" "}
            {files.length} files
          </p>
        </div>
        <div className="flex gap-2">
          <Input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter paths…"
            className="h-8 w-48"
            aria-label="Filter by path"
          />
          <Select value={sort} onValueChange={(v) => setSort(v as SortKey)}>
            <SelectTrigger className="h-8 w-44" aria-label="Sort">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="percent-asc">Worst coverage first</SelectItem>
              <SelectItem value="percent-desc">Best coverage first</SelectItem>
              <SelectItem value="path">Path A→Z</SelectItem>
              <SelectItem value="delta">Biggest drop</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      <div
        ref={parentRef}
        className="bg-card h-[420px] overflow-auto rounded-md border"
        role="list"
        aria-label="Coverage per file"
      >
        {processed.length === 0 ? (
          <p className="text-muted-foreground p-6 text-center text-sm">
            No files match.
          </p>
        ) : (
          <div
            style={{ height: virt.getTotalSize(), position: "relative", width: "100%" }}
          >
            {virt.getVirtualItems().map((v) => {
              const f = processed[v.index]!;
              return (
                <button
                  key={f.path}
                  type="button"
                  onClick={() => onSelect?.(f)}
                  className={cn(
                    "hover:bg-accent absolute left-0 top-0 flex w-full items-center gap-3 border-b px-3 text-left transition-colors",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  )}
                  style={{ height: v.size, transform: `translateY(${v.start}px)` }}
                  role="listitem"
                >
                  <span className="flex-1 truncate font-mono text-xs" title={f.path}>
                    {f.path}
                  </span>
                  <span className="flex w-56 items-center gap-2">
                    <div
                      className={cn(
                        "h-1.5 flex-1 overflow-hidden rounded-full border",
                        tone(f.percent),
                      )}
                    >
                      <div
                        className={cn("h-full", barTone(f.percent))}
                        style={{ width: `${Math.min(100, Math.max(0, f.percent))}%` }}
                        aria-hidden
                      />
                    </div>
                    <span className="w-12 text-right font-mono text-xs tabular-nums">
                      {f.percent.toFixed(1)}%
                    </span>
                  </span>
                  <span className="w-14 text-right font-mono text-[11px] tabular-nums">
                    {f.covered}/{f.lines}
                  </span>
                  <span className="w-14 text-right">
                    {f.delta !== undefined && f.delta !== 0 ? (
                      <span
                        className={cn(
                          "inline-flex items-center gap-0.5 font-mono text-[11px]",
                          f.delta > 0 ? "text-success" : "text-destructive",
                        )}
                      >
                        {f.delta > 0 ? (
                          <TrendingUp className="size-3" aria-hidden />
                        ) : (
                          <TrendingDown className="size-3" aria-hidden />
                        )}
                        {f.delta > 0 ? "+" : ""}
                        {f.delta.toFixed(1)}
                      </span>
                    ) : (
                      <span className="text-muted-foreground font-mono text-[11px]">—</span>
                    )}
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </section>
  );
}
