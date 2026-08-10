import { cn } from "@/lib/utils";

/**
 * The Org Admin's active/inactive marker on a Business Unit.
 *
 * Deliberately quiet, and deliberately absent when the unit is active: an
 * "Active" chip on every row in a list where almost everything is active is
 * noise that trains people to stop reading badges. Only the exception is worth
 * a pixel, so the active case renders nothing.
 *
 * This is a label, not a gate — an inactive unit behaves exactly like an active
 * one (see `Workspace.isActive` in lib/schemas/workspace.ts). The wording says
 * "Inactive" rather than "Disabled" for that reason.
 */
export function ActiveBadge({
  isActive,
  className,
}: {
  isActive: boolean;
  className?: string;
}) {
  if (isActive) return null;
  return (
    <span
      className={cn(
        "border-line-soft text-muted-foreground bg-muted/40 inline-flex items-center rounded-full border px-2.5 py-0.5 font-mono text-[10.5px] font-semibold",
        className,
      )}
      title="Marked inactive by an Organization Admin. Nothing is restricted by this."
    >
      Inactive
    </span>
  );
}
