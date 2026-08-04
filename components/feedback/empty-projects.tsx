import * as React from "react";
import Link from "next/link";
import { FolderPlus } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

export interface EmptyProjectsProps {
  /** When true, a search/filter is active — adjusts the copy. */
  isFiltered?: boolean;
  /** Called when the primary "Create project" CTA is clicked. */
  onCreateProject?: () => void;
  /** Whether the user has permission to create projects. */
  canCreate?: boolean;
  className?: string;
}

/**
 * Elevated empty state for the projects list. Shown on the
 * `isSuccess && items.length === 0` branch. Uses Plan-01 tokens only.
 * CTA wires to the existing create-project flow.
 */
export function EmptyProjects({
  isFiltered = false,
  onCreateProject,
  canCreate = true,
  className,
}: EmptyProjectsProps) {
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "flex flex-col items-center gap-5 rounded-xl border border-dashed border-line-soft bg-surface-1 px-6 py-14 text-center",
        className,
      )}
    >
      {/* Icon halo — muted brand accent */}
      <div className="grid size-14 place-items-center rounded-full border border-brand-bright/20 bg-brand-bright/8 text-brand-bright">
        <FolderPlus className="size-6" aria-hidden />
      </div>

      {/* Editorial heading */}
      <div className="space-y-2">
        <h3 className="font-display text-xl font-bold tracking-[-0.02em]">
          {isFiltered ? "No projects match" : "No projects yet"}
        </h3>
        <p className="mx-auto max-w-sm text-sm text-muted-foreground">
          {isFiltered
            ? "Try clearing filters or widening the search — or create a new project from scratch."
            : "Create your first project to start running agents across the delivery pipeline."}
        </p>
      </div>

      {/* Actions */}
      {!isFiltered && (
        <div className="flex flex-wrap items-center justify-center gap-2">
          {canCreate ? (
            <Button
              onClick={onCreateProject}
              className="bg-gradient-to-br from-brand-gradient-from to-brand-gradient-to font-semibold text-white shadow-[0_6px_18px_-6px_oklch(0.6_0.2_35_/_0.55)] transition-shadow hover:shadow-[0_10px_26px_-8px_oklch(0.6_0.2_35_/_0.7)] focus-visible:ring-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2"
            >
              Create your first project
            </Button>
          ) : (
            <Button disabled title="Your role cannot create projects">
              Create project (role-restricted)
            </Button>
          )}
          <Button variant="outline" className="border-line-soft" asChild>
            <Link href="/integrations">Connect tools</Link>
          </Button>
        </div>
      )}
    </div>
  );
}
