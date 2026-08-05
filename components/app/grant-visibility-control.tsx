"use client";

import * as React from "react";
import { Building2, Check, Globe, Search } from "lucide-react";

import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
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
  /**
   * Naming nobody is a legitimate end state, not an error.
   *
   * Onboarding a provider and deciding its reach are two decisions, and forcing
   * them into one act means an admin who has the key but not yet the answer
   * cannot save at all. When optional, an empty selection reads as "registered,
   * granted to nobody yet" and the copy says where to finish it.
   */
  optional?: boolean;
  className?: string;
}

/** Above this many units a chip cloud stops being scannable and starts being a
 *  wall — the same threshold at which the eye needs a search box rather than a
 *  sweep. Below it, chips are faster: everything is visible at once. */
const SEARCHABLE_ABOVE = 8;

/**
 * How far one grant reaches: everywhere, or only the {BUSINESS_UNIT_LABEL_PLURAL}
 * you name.
 *
 * Shared by models and connectors so the two read identically — the same two
 * words in the same order mean the same thing on both pages, which is the
 * whole reason `GrantVisibility` is one type rather than two.
 *
 * The unit list has TWO renderings on purpose. A handful of units is best shown
 * whole; twenty is not, and a chip cloud of twenty wraps into a paragraph you
 * have to read rather than scan. Past `SEARCHABLE_ABOVE` it becomes a fixed-
 * height searchable list with a running count, which costs the same to read at
 * five units as at five hundred.
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
  optional,
  className,
}: GrantVisibilityControlProps) {
  const specific = value.visibility === "specific";
  const [query, setQuery] = React.useState("");

  const unit = BUSINESS_UNIT_LABEL_PLURAL.toLowerCase().replace(/s$/, "");
  const searchable = workspaces.length > SEARCHABLE_ABOVE;

  const toggleUnit = (id: string) => {
    const has = value.businessUnitIds.includes(id);
    onChange({
      ...value,
      businessUnitIds: has
        ? value.businessUnitIds.filter((x) => x !== id)
        : [...value.businessUnitIds, id],
    });
  };

  const matches = React.useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return workspaces;
    return workspaces.filter((w) => w.displayName.toLowerCase().includes(q));
  }, [workspaces, query]);

  const chosen = value.businessUnitIds.length;

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
        ) : searchable ? (
          <div className="border-line-soft overflow-hidden rounded-lg border">
            <div className="border-line-soft flex items-center gap-2 border-b px-2">
              <Search className="text-muted-foreground size-3.5 shrink-0" aria-hidden />
              <Input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                disabled={disabled}
                placeholder={`Search ${workspaces.length} ${BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}…`}
                aria-label={`Search ${BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}`}
                className="h-8 border-0 bg-transparent px-0 text-[12.5px] shadow-none focus-visible:ring-0"
              />
              {chosen > 0 && (
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => onChange({ ...value, businessUnitIds: [] })}
                  className="text-muted-foreground hover:text-foreground shrink-0 font-mono text-[10.5px] transition-colors"
                >
                  Clear
                </button>
              )}
            </div>
            <ul className="max-h-44 overflow-y-auto">
              {matches.length === 0 ? (
                <li className="text-muted-foreground p-2.5 text-[12px]">No {unit} matches.</li>
              ) : (
                matches.map((w) => {
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
                          "hover:bg-surface-1 flex w-full items-center gap-2 px-2.5 py-1.5 text-left transition-colors",
                          disabled && "cursor-not-allowed opacity-60",
                        )}
                      >
                        <span
                          className={cn(
                            "grid size-4 shrink-0 place-items-center rounded border",
                            on
                              ? "border-brand-bright/50 bg-brand-bright/10 text-foreground"
                              : "border-line-soft",
                          )}
                        >
                          {on && <Check className="size-2.5" aria-hidden />}
                        </span>
                        <span className="truncate text-[12.5px]">{w.displayName}</span>
                      </button>
                    </li>
                  );
                })
              )}
            </ul>
            <p className="border-line-soft text-muted-foreground border-t px-2.5 py-1.5 font-mono text-[10.5px]">
              {chosen} of {workspaces.length} named
            </p>
          </div>
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
          Available to every {unit} and project automatically.
        </p>
      )}

      {specific &&
        workspaces.length > 0 &&
        chosen === 0 &&
        (optional ? (
          <p className="text-muted-foreground text-[11.5px]">
            Name nobody and it is registered but reaches no {unit} — grant it later from{" "}
            <span className="text-foreground">Models &amp; access</span>.
          </p>
        ) : (
          <p className="text-warning text-[11.5px]">
            Nobody can use this until you name at least one {unit}.
          </p>
        ))}
    </div>
  );
}
