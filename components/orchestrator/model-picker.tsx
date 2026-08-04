"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { BrainCircuit, Check, ChevronsUpDown, KeyRound, Loader2 } from "lucide-react";

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
import { getModelAvailability, getProjectModelSelection } from "@/lib/api/models";
import { qk } from "@/lib/api/query-keys";
import { modelKeyOf } from "@/lib/orchestrator/types";

export interface ProjectModelOption {
  key: string;
  provider: string;
  model_id: string;
  /** False when nobody has supplied a key for it — selectable but inert. */
  credentialed: boolean;
  /** True when it came from the BU's grant rather than the project's own pick. */
  inherited: boolean;
}

export interface ModelPickerProps {
  projectId: string | null;
  /** The project's Business Unit — needed to resolve credential state. */
  workspaceId: string | null;
  value: string | null;
  onValueChange: (modelKey: string) => void;
  /** Fires whenever the resolved option set changes (incl. the default pick). */
  onOptionsResolved?: (options: ProjectModelOption[], defaultKey: string | null) => void;
  disabled?: boolean;
  className?: string;
}

/**
 * The models this project may actually run on.
 *
 * Two things have to be true, and the platform tracks them separately:
 *
 *  1. **Allowed** — the model reached the project through the cascade
 *     (org grant → the BU it reaches → what the project selected from it).
 *     `GET /model/allowed/project` is the last tier of that.
 *  2. **Credentialed** — somebody has actually supplied a key.
 *     `GET /model/availability` answers this per Business Unit.
 *
 * A model that is allowed but has no key is shown, disabled, and labelled — not
 * hidden. Hiding it produces the worst version of this screen: a project admin
 * who granted a model and cannot find it, with nothing on the page explaining
 * why. Naming the reason turns a mystery into a task.
 *
 * When the project has selected nothing yet (`usingDefaults`), the BU's whole
 * inherited set is offered rather than an empty dropdown — the project has not
 * narrowed the grant, so the grant is what applies.
 *
 * A Popover + `cmdk` combobox rather than a `Select`, matching `ProjectPicker`:
 * model ids are long, near-identical strings that differ in a version suffix
 * ("claude-opus-4-5" vs "claude-opus-5"), which is exactly the list a human
 * scans badly and types well. The filter runs over provider *and* id, so both
 * "anthropic" and "opus" narrow it.
 */
export function ModelPicker({
  projectId,
  workspaceId,
  value,
  onValueChange,
  onOptionsResolved,
  disabled,
  className,
}: ModelPickerProps) {
  const [open, setOpen] = React.useState(false);

  const selectionQ = useQuery({
    queryKey: ["model", "project-selection", projectId] as const,
    queryFn: () => getProjectModelSelection(projectId!),
    enabled: !!projectId,
    staleTime: 30_000,
  });

  const availabilityQ = useQuery({
    queryKey: qk.model.availability(workspaceId ?? ""),
    queryFn: () => getModelAvailability(workspaceId!),
    enabled: !!workspaceId,
    staleTime: 30_000,
  });

  const options = React.useMemo<ProjectModelOption[]>(() => {
    const sel = selectionQ.data;
    if (!sel) return [];

    const inheritedKeys = new Set(sel.inherited.map(modelKeyOf));
    // Nothing selected → the project has not narrowed its BU's grant, so the
    // grant is the offer.
    const source = sel.selected.length > 0 ? sel.selected : sel.inherited;

    const credentialedKeys = new Set(
      (availabilityQ.data ?? [])
        .filter((a) => a.centrallyCredentialed || a.locallyCredentialed)
        .map(modelKeyOf),
    );

    return source.map((e) => {
      const key = modelKeyOf(e);
      return {
        key,
        provider: e.provider,
        model_id: e.model_id,
        // Availability not loaded yet must not read as "no key" — that would
        // grey out every option for a frame and look like a broken page.
        credentialed: availabilityQ.data ? credentialedKeys.has(key) : true,
        inherited: sel.selected.length === 0 && inheritedKeys.has(key),
      };
    });
  }, [selectionQ.data, availabilityQ.data]);

  const defaultKey = React.useMemo(() => {
    const explicit = selectionQ.data?.defaultKey ?? null;
    if (explicit && options.some((o) => o.key === explicit)) return explicit;
    return options.find((o) => o.credentialed)?.key ?? options[0]?.key ?? null;
  }, [selectionQ.data, options]);

  // Report upward so the page can seed a session's model without duplicating
  // any of the resolution above.
  const notify = React.useRef(onOptionsResolved);
  notify.current = onOptionsResolved;
  const signature = options.map((o) => o.key).join("|");
  React.useEffect(() => {
    notify.current?.(options, defaultKey);
    // `signature` stands in for `options` — the array identity changes on every
    // render of the memo's inputs, which would loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature, defaultKey]);

  if (!projectId) {
    return (
      <div className="border-line-soft bg-surface-1 text-muted-foreground inline-flex h-8 min-w-[190px] items-center gap-2 rounded-md border border-dashed px-3 text-[12px]">
        <BrainCircuit className="size-3.5 shrink-0" aria-hidden />
        Pick a project first
      </div>
    );
  }

  if (selectionQ.isLoading) {
    return (
      <div
        className="border-line-soft bg-surface-1 text-muted-foreground inline-flex h-8 min-w-[190px] items-center gap-2 rounded-md border px-3 text-[12.5px]"
        aria-busy
      >
        <Loader2 className="size-3.5 animate-spin" aria-hidden />
        Loading models…
      </div>
    );
  }

  if (!selectionQ.isError && options.length === 0) {
    return (
      <Link
        href={`/projects/${projectId}/settings`}
        className="border-warning/40 bg-warning/[0.06] text-warning hover:border-warning/70 inline-flex h-8 items-center gap-2 rounded-md border px-2.5 text-[12px] transition-colors"
      >
        <BrainCircuit className="size-3.5 shrink-0" aria-hidden />
        No models granted to this project
      </Link>
    );
  }

  const selectedKey = value && options.some((o) => o.key === value) ? value : defaultKey;
  const selected = options.find((o) => o.key === selectedKey);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          aria-label="Model"
          disabled={disabled || selectionQ.isError}
          className={cn(
            "border-line-soft bg-surface-1 h-8 w-auto min-w-[190px] max-w-[320px] justify-between gap-2 px-3 text-[12.5px] font-normal",
            className,
          )}
        >
          <span className="flex min-w-0 items-center gap-2">
            <BrainCircuit className="text-muted-foreground size-3.5 shrink-0" aria-hidden />
            <span className="truncate text-[12.5px]">
              {selected ? (
                <>
                  <span className="text-muted-foreground">{selected.provider}</span>
                  <span className="text-muted-foreground"> · </span>
                  <span className="font-mono font-medium">{selected.model_id}</span>
                </>
              ) : (
                "Select model"
              )}
            </span>
          </span>
          <ChevronsUpDown className="size-3.5 shrink-0 opacity-50" aria-hidden />
        </Button>
      </PopoverTrigger>

      <PopoverContent className="w-[min(24rem,90vw)] p-0" align="start">
        <Command filter={substringFilter}>
          <CommandInput placeholder="Search models…" />
          <CommandList className="max-h-[min(60vh,20rem)]">
            <CommandEmpty>No matching model.</CommandEmpty>
            {options.map((o) => (
              <CommandItem
                key={o.key}
                value={`${o.provider} ${o.model_id}`}
                disabled={!o.credentialed}
                onSelect={() => {
                  onValueChange(o.key);
                  setOpen(false);
                }}
              >
                <Check
                  className={cn(
                    "size-3.5 shrink-0",
                    o.key === selectedKey ? "opacity-100" : "opacity-0",
                  )}
                  aria-hidden
                />
                <span className="text-muted-foreground shrink-0 font-mono text-[10px] tracking-wide uppercase">
                  {o.provider}
                </span>
                <span className="truncate font-mono text-[12.5px]">{o.model_id}</span>
                {o.inherited && (
                  <span className="text-muted-foreground border-line-soft ml-auto shrink-0 rounded-full border px-1.5 py-px font-mono text-[9px] tracking-wide uppercase">
                    Inherited
                  </span>
                )}
                {!o.credentialed && (
                  <span className="text-warning border-warning/40 bg-warning/[0.06] ml-auto inline-flex shrink-0 items-center gap-1 rounded-full border px-1.5 py-px font-mono text-[9px] tracking-wide uppercase">
                    <KeyRound className="size-2.5" aria-hidden />
                    No key
                  </span>
                )}
              </CommandItem>
            ))}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
