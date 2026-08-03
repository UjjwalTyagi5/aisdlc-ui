"use client";

import * as React from "react";
import { Building2, Check, Globe } from "lucide-react";

import { cn } from "@/lib/utils";
import { BUSINESS_UNIT_LABEL_PLURAL } from "@/lib/scope";
import type { GrantVisibility } from "@/lib/schemas/grant";

export interface GrantValue {
  visibility: GrantVisibility;
  businessUnitIds: string[];
}

export interface GrantVisibilityControlProps {
  value: GrantValue;
  onChange: (next: GrantValue) => void;
  /** Every Business Unit a `specific` grant could name. */
  workspaces: { id: string; displayName: string }[];
  disabled?: boolean;
  /** Distinguishes this control's inputs from every other row's. */
  idPrefix: string;
  className?: string;
}

/**
 * How far one grant reaches: everywhere, or only the {BUSINESS_UNIT_LABEL_PLURAL}
 * you name.
 *
 * Shared by models and connectors so the two read identically — the same two
 * words in the same order mean the same thing on both pages, which is the
 * whole reason `GrantVisibility` is one type rather than two.
 *
 * Switching to Global does not clear the unit list in local state: an admin
 * toggling between the two to compare shouldn't lose their selection. The
 * store clears it on save (see `setOrgModelGrants`), so what persists is still
 * unambiguous.
 */
export function GrantVisibilityControl({
  value,
  onChange,
  workspaces,
  disabled,
  idPrefix,
  className,
}: GrantVisibilityControlProps) {
  const specific = value.visibility === "specific";

  const toggleUnit = (id: string) => {
    const has = value.businessUnitIds.includes(id);
    onChange({
      ...value,
      businessUnitIds: has
        ? value.businessUnitIds.filter((x) => x !== id)
        : [...value.businessUnitIds, id],
    });
  };

  return (
    <div className={cn("space-y-2", className)}>
      <div
        role="radiogroup"
        aria-label="Visibility"
        className="border-line-soft bg-surface-1 inline-flex rounded-lg border p-0.5"
      >
        {(
          [
            { key: "global", label: "Global", icon: Globe },
            { key: "specific", label: "Specific", icon: Building2 },
          ] as const
        ).map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            type="button"
            role="radio"
            aria-checked={value.visibility === key}
            disabled={disabled}
            onClick={() => onChange({ ...value, visibility: key })}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 font-mono text-[10.5px] font-semibold tracking-wider uppercase transition-all",
              value.visibility === key
                ? "bg-panel-elevated text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground",
              disabled && "cursor-not-allowed opacity-60",
            )}
          >
            <Icon className="size-3" aria-hidden />
            {label}
          </button>
        ))}
      </div>

      {specific ? (
        workspaces.length === 0 ? (
          <p className="text-muted-foreground text-[11.5px]">
            No {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()} to grant this to yet.
          </p>
        ) : (
          <ul className="flex flex-wrap gap-1.5">
            {workspaces.map((w) => {
              const on = value.businessUnitIds.includes(w.id);
              return (
                <li key={w.id}>
                  <button
                    type="button"
                    id={`${idPrefix}-${w.id}`}
                    role="checkbox"
                    aria-checked={on}
                    disabled={disabled}
                    onClick={() => toggleUnit(w.id)}
                    className={cn(
                      "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10.5px] transition-colors",
                      on
                        ? "border-brand-bright/40 bg-brand-bright/10 text-foreground"
                        : "border-line-soft bg-surface-1 text-muted-foreground hover:text-foreground",
                      disabled && "cursor-not-allowed opacity-60",
                    )}
                  >
                    {on && <Check className="size-2.5" aria-hidden />}
                    {w.displayName}
                  </button>
                </li>
              );
            })}
          </ul>
        )
      ) : (
        <p className="text-muted-foreground text-[11.5px]">
          Available to every {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase().slice(0, -1)} and project
          automatically.
        </p>
      )}

      {specific && value.businessUnitIds.length === 0 && (
        <p className="text-warning text-[11.5px]">
          Nobody can use this until you name at least one{" "}
          {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase().slice(0, -1)}.
        </p>
      )}
    </div>
  );
}
