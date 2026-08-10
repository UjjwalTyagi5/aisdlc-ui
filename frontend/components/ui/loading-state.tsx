import * as React from "react";

import { cn } from "@/lib/utils";
import { Skeleton } from "@/components/ui/skeleton";

export interface LoadingStateProps {
  /** "list" mimics a stack of artifact rows, "card" a single detail surface, "table" a data grid. */
  variant?: "list" | "card" | "table";
  rows?: number;
  className?: string;
  label?: string;
}

export function LoadingState({
  variant = "card",
  rows = 5,
  className,
  label = "Loading",
}: LoadingStateProps) {
  return (
    <div
      role="status"
      aria-live="polite"
      aria-busy="true"
      className={cn("w-full", className)}
    >
      <span className="sr-only">{label}…</span>
      {variant === "list" && (
        <div className="space-y-2">
          {Array.from({ length: rows }).map((_, i) => (
            <div key={i} className="flex items-center gap-3 rounded-md border p-3">
              <Skeleton className="size-8 rounded-full" />
              <div className="flex-1 space-y-2">
                <Skeleton className="h-3 w-1/3" />
                <Skeleton className="h-3 w-2/3" />
              </div>
              <Skeleton className="h-6 w-16 rounded-full" />
            </div>
          ))}
        </div>
      )}

      {variant === "card" && (
        <div className="space-y-3 rounded-lg border p-6">
          <Skeleton className="h-5 w-1/3" />
          <Skeleton className="h-4 w-2/3" />
          <div className="mt-4 grid grid-cols-3 gap-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-16" />
            ))}
          </div>
        </div>
      )}

      {variant === "table" && (
        <div className="overflow-hidden rounded-md border">
          <div className="bg-muted/50 grid grid-cols-4 gap-4 border-b p-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-3" />
            ))}
          </div>
          <div className="divide-y">
            {Array.from({ length: rows }).map((_, r) => (
              <div key={r} className="grid grid-cols-4 gap-4 p-3">
                {Array.from({ length: 4 }).map((_, c) => (
                  <Skeleton key={c} className="h-3" />
                ))}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
