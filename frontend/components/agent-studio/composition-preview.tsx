"use client";

import * as React from "react";
import { AlertTriangle, Layers, Lock } from "lucide-react";

import { cn } from "@/lib/utils";
import { Skeleton } from "@/components/ui/skeleton";
import type { ProfileLayer, ProfilePreview } from "@/lib/schemas/agent-profiles";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";

/** Human labels for a layer's provenance. */
const SOURCE_LABEL: Record<string, string> = {
  vendor: "Vendor base prompt",
  org: "Organization defaults",
  workspace: `${BUSINESS_UNIT_LABEL} instructions`,
  project: "Project instructions",
  user: "Personal instructions",
  draft: "Your draft",
};

export interface CompositionPreviewProps {
  preview?: ProfilePreview;
  isLoading: boolean;
  isFetching?: boolean;
}

/**
 * Teaches the layering model: shows the resolved prompt stack outermost →
 * innermost as the server composed it, with the locked vendor base prompt in
 * the middle (its text is never sent to the client). Soft lint warnings from
 * the same preview call surface below.
 */
export function CompositionPreview({
  preview,
  isLoading,
  isFetching,
}: CompositionPreviewProps) {
  return (
    <section aria-labelledby="composition-heading" className="space-y-3">
      <div className="flex items-center gap-2">
        <Layers className="text-muted-foreground size-3.5" aria-hidden />
        <h3
          id="composition-heading"
          className="text-xs font-semibold uppercase tracking-wider"
        >
          Composition preview
        </h3>
        {isFetching && !isLoading && (
          <span className="text-muted-foreground text-[10px]">· updating…</span>
        )}
      </div>

      <p className="text-muted-foreground text-xs">
        How your instructions layer around the locked base prompt at run time.
      </p>

      {isLoading ? (
        <div className="space-y-2" aria-hidden>
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </div>
      ) : !preview || preview.layers.length === 0 ? (
        <p className="text-muted-foreground border-line-soft rounded-lg border border-dashed px-3 py-6 text-center text-xs">
          The preview appears once there&apos;s content to resolve.
        </p>
      ) : (
        <ol className="space-y-2">
          {preview.layers.map((layer, idx) => (
            <LayerRow key={`${layer.source}-${idx}`} layer={layer} />
          ))}
        </ol>
      )}

      {preview && preview.warnings.length > 0 && (
        <ul className="space-y-1.5 pt-1">
          {preview.warnings.map((w, idx) => (
            <li
              key={`${w.code}-${idx}`}
              className="text-warning flex items-start gap-1.5 text-xs"
            >
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
              <span className="text-foreground/90">{w.message}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function LayerRow({ layer }: { layer: ProfileLayer }) {
  const label = SOURCE_LABEL[layer.source] ?? layer.name;

  if (layer.locked) {
    return (
      <li className="border-line-soft bg-muted/40 flex items-center gap-2.5 rounded-lg border border-dashed px-3 py-2.5">
        <Lock className="text-muted-foreground size-3.5 shrink-0" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="text-muted-foreground text-xs font-medium">{label}</p>
          <p className="text-muted-foreground/70 text-[11px]">
            Locked · content hidden
          </p>
        </div>
      </li>
    );
  }

  const empty = !layer.content || layer.content.trim().length === 0;

  return (
    <li
      className={cn(
        "border-line-soft rounded-lg border px-3 py-2.5",
        empty && "opacity-60",
      )}
    >
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs font-medium">{label}</p>
        <span className="text-muted-foreground font-mono text-[10px] tabular-nums">
          {layer.chars.toLocaleString()} chars
        </span>
      </div>
      {!empty && (
        <p className="text-muted-foreground mt-1 line-clamp-3 whitespace-pre-wrap text-[11px] leading-snug">
          {layer.content}
        </p>
      )}
    </li>
  );
}
