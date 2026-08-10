"use client";

import * as React from "react";
import { Building2, Check, ChevronDown, Globe } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandInput,
  CommandItem,
  CommandList,
  substringFilter,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { BUSINESS_UNIT_LABEL_PLURAL } from "@/lib/scope";

export interface AccessUnit {
  id: string;
  name: string;
}

/**
 * Which Business Units hold something, as ONE cell.
 *
 * Replaces a column-per-unit grid. That grid read well at three units and
 * collapses at twenty: the table grows sideways without bound, every row
 * becomes mostly empty checkboxes, and the model name — the thing you scan
 * for — is squeezed to nothing. A column per instance of your data is a
 * layout that only works while you have little of it.
 *
 * So the cell states the ANSWER ("All units", "Payments +2") and the picker
 * behind it is a searchable list, which is flat in the number of units. The
 * summary names up to two and counts the rest: naming all of them recreates
 * the same overflow in a smaller box.
 */
export function UnitAccessPicker({
  units,
  selected,
  isGlobal,
  disabled,
  onToggle,
  className,
}: {
  units: AccessUnit[];
  /** Ids with access. Ignored when `isGlobal` — everyone has it. */
  selected: string[];
  /** Granted to every unit; the picker is read-only and says why. */
  isGlobal?: boolean;
  disabled?: boolean;
  onToggle: (unitId: string) => void;
  className?: string;
}) {
  const [open, setOpen] = React.useState(false);

  const chosen = React.useMemo(
    () => units.filter((u) => selected.includes(u.id)),
    [units, selected],
  );

  const summary = isGlobal ? (
    <>
      <Globe className="size-3 shrink-0" aria-hidden />
      All {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}
    </>
  ) : chosen.length === 0 ? (
    <>
      <Building2 className="size-3 shrink-0" aria-hidden />
      <span className="text-warning">None</span>
    </>
  ) : (
    <>
      <Building2 className="size-3 shrink-0" aria-hidden />
      <span className="truncate">
        {chosen
          .slice(0, 2)
          .map((u) => u.name)
          .join(", ")}
        {chosen.length > 2 && ` +${chosen.length - 2}`}
      </span>
    </>
  );

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          aria-label={`Business unit access — ${
            isGlobal ? "all units" : chosen.map((u) => u.name).join(", ") || "none"
          }`}
          disabled={disabled}
          className={cn(
            "border-line-soft bg-surface-1 h-7 max-w-[220px] justify-between gap-1.5 px-2 text-[11.5px] font-normal",
            className,
          )}
        >
          <span className="flex min-w-0 items-center gap-1.5">{summary}</span>
          <ChevronDown className="size-3 shrink-0 opacity-50" aria-hidden />
        </Button>
      </PopoverTrigger>

      <PopoverContent className="w-[min(20rem,90vw)] p-0" align="start">
        {isGlobal ? (
          <p className="text-muted-foreground p-3 text-[12px]">
            Granted globally — it reaches every{" "}
            {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase().replace(/s$/, "")} automatically. Switch the
            grant to specific to name units.
          </p>
        ) : (
          <Command filter={substringFilter}>
            <CommandInput placeholder={`Search ${BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}…`} />
            <CommandList className="max-h-[min(50vh,16rem)]">
              <CommandEmpty>No matching unit.</CommandEmpty>
              {units.map((u) => {
                const has = selected.includes(u.id);
                return (
                  <CommandItem
                    key={u.id}
                    value={`${u.name} ${u.id}`}
                    onSelect={() => onToggle(u.id)}
                  >
                    <span
                      className={cn(
                        "grid size-4 shrink-0 place-items-center rounded border",
                        has
                          ? "border-success/50 bg-success/10 text-success"
                          : "border-line-soft",
                      )}
                    >
                      {has && <Check className="size-2.5" aria-hidden />}
                    </span>
                    <span className="truncate text-[12.5px]">{u.name}</span>
                  </CommandItem>
                );
              })}
            </CommandList>
          </Command>
        )}
      </PopoverContent>
    </Popover>
  );
}
