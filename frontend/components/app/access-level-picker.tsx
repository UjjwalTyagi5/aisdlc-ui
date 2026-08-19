"use client";

import * as React from "react";

import { cn } from "@/lib/utils";
import {
  ACCESS_LEVEL_HINT,
  ACCESS_LEVEL_LABEL,
  type ConnectorAccessLevel,
} from "@/lib/schemas/integration-access";

const LEVELS: ConnectorAccessLevel[] = ["read", "write", "read_write"];

/**
 * Read, write, or both — for one integration at one level of the cascade.
 *
 * THREE BUTTONS, NOT A SLIDER, and that is the whole design. `read` and `write` are
 * INCOMPARABLE: neither contains the other, so there is no axis to slide along.
 * Anything ordered — a slider, a stepper, a "level 1/2/3" — would state that write
 * is more than read, which is exactly the escalation the model exists to prevent.
 * Three peers, one selected, matches the lattice the server enforces.
 *
 * `ceiling` narrows what may be picked. A project cannot be given more than its unit
 * holds, so options outside the ceiling are shown DISABLED rather than hidden: an
 * admin who cannot find "read and write" learns nothing, while one who sees it greyed
 * out with a reason learns that the limit is real and where it comes from.
 *
 * The server refuses independently. This control makes the boundary visible; it does
 * not implement it, and it must never be the only thing standing between a project
 * and a level it may not have.
 */
export function AccessLevelPicker({
  value,
  onChange,
  ceiling,
  disabled,
  size = "md",
  className,
}: {
  value: ConnectorAccessLevel | null;
  onChange: (next: ConnectorAccessLevel) => void;
  /** The widest level allowed here — the unit's grant, when setting a project. */
  ceiling?: ConnectorAccessLevel | null;
  disabled?: boolean;
  size?: "sm" | "md";
  className?: string;
}) {
  // Mirrors `connector_access.contains()`. Kept as a local set test rather than a
  // comparison so read and write stay incomparable here too.
  const allowedModes = (level: ConnectorAccessLevel): Set<string> =>
    level === "read_write" ? new Set(["read", "write"]) : new Set([level]);

  const withinCeiling = (level: ConnectorAccessLevel) => {
    if (!ceiling) return true;
    const cap = allowedModes(ceiling);
    return [...allowedModes(level)].every((m) => cap.has(m));
  };

  return (
    <div
      className={cn("inline-flex flex-col gap-1", className)}
      role="radiogroup"
      aria-label="Access level"
    >
      <div className="border-line-soft inline-flex overflow-hidden rounded-md border">
        {LEVELS.map((level) => {
          const selected = value === level;
          const blocked = !withinCeiling(level);
          return (
            <button
              key={level}
              type="button"
              role="radio"
              aria-checked={selected}
              disabled={disabled || blocked}
              title={
                blocked && ceiling
                  ? `This business unit has ${ACCESS_LEVEL_LABEL[ceiling].toLowerCase()} access, so a project cannot be given more.`
                  : ACCESS_LEVEL_HINT[level]
              }
              onClick={() => !blocked && !disabled && onChange(level)}
              className={cn(
                "border-line-soft font-mono transition-colors first:border-l-0 border-l",
                size === "sm" ? "px-2 py-1 text-[10.5px]" : "px-2.5 py-1.5 text-[11.5px]",
                selected
                  ? "bg-brand-bright/10 text-brand-bright font-semibold"
                  : "text-muted-foreground hover:text-foreground",
                (disabled || blocked) && "cursor-not-allowed opacity-40 hover:text-muted-foreground",
              )}
            >
              {ACCESS_LEVEL_LABEL[level]}
            </button>
          );
        })}
      </div>
      {value && (
        <span className="text-muted-foreground/70 text-[10.5px] leading-snug">
          {ACCESS_LEVEL_HINT[value]}
        </span>
      )}
    </div>
  );
}
