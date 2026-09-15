"use client";

import { ChevronDown, ChevronsUpDown, ChevronUp } from "lucide-react";

import { cn } from "@/lib/utils";
import { TableHead } from "@/components/ui/table";

export type SortDir = "asc" | "desc";

/** The `aria-sort` value for a column header — "none" unless it is the active one. */
export function ariaSort(active: boolean, dir: SortDir): "ascending" | "descending" | "none" {
  return active ? (dir === "asc" ? "ascending" : "descending") : "none";
}

/**
 * A column header you can sort by, in either direction.
 *
 * THE WHOLE HEADER IS THE CONTROL, not a separate arrow beside it. A small icon next
 * to non-clickable text gives one action two hit areas of very different size, and
 * people aim at the words — the same reason the spend panel's heading is its own
 * toggle.
 *
 * The arrow is always rendered, as a dimmed double-chevron when the column is not the
 * active sort. An arrow that only appears on the sorted column tells you how the table
 * is ordered and hides WHICH OTHER COLUMNS YOU COULD ORDER IT BY — the affordance
 * disappears exactly when it is being looked for.
 *
 * Columns that cannot be sorted use a plain `TableHead`. Rendering this in a disabled
 * state instead would advertise an action the table cannot take.
 */
export function SortableHead({
  label,
  active,
  dir,
  onSort,
  className,
  align = "left",
}: {
  label: string;
  /** Whether THIS column is the one currently ordering the table. */
  active: boolean;
  /** The active direction — only meaningful when `active`. */
  dir: SortDir;
  /** Called with the direction to switch to: the opposite if active, else "desc". */
  onSort: (next: SortDir) => void;
  className?: string;
  align?: "left" | "right";
}) {
  const next: SortDir = active && dir === "desc" ? "asc" : "desc";
  const Arrow = !active ? ChevronsUpDown : dir === "desc" ? ChevronDown : ChevronUp;

  return (
    <TableHead className={className} aria-sort={ariaSort(active, dir)}>
      <button
        type="button"
        onClick={() => onSort(next)}
        // Screen readers get the state from aria-sort on the header; the label here
        // says what the CLICK will do, which is the part that is not otherwise legible.
        aria-label={`Sort by ${label}, ${next === "desc" ? "descending" : "ascending"}`}
        className={cn(
          "group focus-visible:ring-ring -mx-1 inline-flex items-center gap-1 rounded-sm px-1 py-0.5",
          "focus-visible:ring-2 focus-visible:outline-none",
          align === "right" && "flex-row-reverse",
        )}
      >
        <span className={cn(active ? "text-foreground font-semibold" : undefined)}>{label}</span>
        <Arrow
          className={cn(
            "size-3.5 shrink-0 transition-colors",
            active ? "text-brand-bright" : "text-muted-foreground/40 group-hover:text-muted-foreground",
          )}
          aria-hidden
        />
      </button>
    </TableHead>
  );
}
