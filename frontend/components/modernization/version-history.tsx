"use client";

import * as React from "react";
import { History } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { ArtifactVersion } from "@/lib/api/artifact-versions";
import { cn } from "@/lib/utils";

/**
 * The left rail of a Track 3 page: every brief (or assessment) the agent has recorded,
 * newest first. Each record is a frozen, numbered version — v2 does not change when v3
 * is recorded — so opening an old one shows exactly what it said.
 */
export const VERSION_STATUS: Record<
  ArtifactVersion["status"],
  { label: string; variant: "secondary" | "success" | "outline" | "danger" }
> = {
  draft: { label: "Draft", variant: "secondary" },
  published: { label: "Approved", variant: "success" },
  superseded: { label: "Superseded", variant: "outline" },
  rejected: { label: "Rejected", variant: "danger" },
};

export function VersionHistory({
  title,
  noun,
  versions,
  isLoading,
  isError,
  onRetry,
  selected,
  onSelect,
}: {
  title: string;
  noun: string;
  versions: ArtifactVersion[] | undefined;
  isLoading: boolean;
  isError: boolean;
  onRetry: () => void;
  selected: number | null;
  onSelect: (version: number | null) => void;
}) {
  return (
    <nav aria-label={title} className="flex min-h-0 flex-col gap-2">
      <div className="flex items-center justify-between">
        <h2 className="flex items-center gap-1.5 text-sm font-medium">
          <History className="text-muted-foreground size-4" aria-hidden />
          {title}
        </h2>
        {selected !== null && (
          <button
            type="button"
            className="text-muted-foreground hover:text-foreground text-xs underline-offset-2 hover:underline"
            onClick={() => onSelect(null)}
          >
            How it works
          </button>
        )}
      </div>
      {isLoading ? (
        <p className="text-muted-foreground text-xs">Loading…</p>
      ) : isError ? (
        <p className="text-muted-foreground text-xs">
          Could not load the list.{" "}
          <button type="button" className="underline" onClick={onRetry}>
            Retry
          </button>
        </p>
      ) : !versions?.length ? (
        <p className="text-muted-foreground rounded-lg border border-dashed p-3 text-xs">
          No {noun}s yet. Each time the agent records a {noun} it appears here as a new version.
        </p>
      ) : (
        <ul className="space-y-1">
          {versions.map((v) => {
            const meta = VERSION_STATUS[v.status];
            const active = v.version === selected;
            return (
              <li key={v.id}>
                <button
                  type="button"
                  aria-current={active ? "true" : undefined}
                  onClick={() => onSelect(v.version)}
                  className={cn(
                    "hover:bg-muted flex w-full items-center justify-between gap-2 rounded-md border px-3 py-2 text-left",
                    active ? "border-primary bg-muted" : "border-transparent",
                  )}
                >
                  <span className="min-w-0">
                    <span className="block text-sm font-medium capitalize">
                      {noun} v{v.version}
                    </span>
                    <span className="text-muted-foreground block truncate text-[11px]">
                      {v.createdAt ? new Date(v.createdAt).toLocaleString() : "—"}
                    </span>
                  </span>
                  <Badge variant={meta.variant} className="shrink-0">
                    {meta.label}
                  </Badge>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </nav>
  );
}
