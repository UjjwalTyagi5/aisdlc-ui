"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { BrainCircuit, Check, ChevronsUpDown, Loader2 } from "lucide-react";

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
import { getModelOptions } from "@/lib/api/models";
import { qk } from "@/lib/api/query-keys";
import { modelKeyOf } from "@/lib/orchestrator/types";

export interface ProjectModelOption {
  /**
   * `provider::model_id::provider_id` — the cockpit's unit of selection.
   *
   * The provider CONNECTION is part of the identity, not decoration: two keys can
   * serve the same model on different endpoints, rates and regions, and the cockpit
   * resolves a key back to an `offering_id` by matching this third segment against
   * `provider_id`.
   */
  key: string;
  /** What a run is actually dispatched against. */
  offeringId: string;
  provider: string;
  model_id: string;
  /** The provider connection's name — shown only when two serve the same model. */
  connectionName: string;
  /** The platform's own default among these offerings. */
  isDefault: boolean;
}

export interface ModelPickerProps {
  projectId: string | null;
  value: string | null;
  onValueChange: (modelKey: string) => void;
  /** Fires whenever the resolved option set changes (incl. the default pick). */
  onOptionsResolved?: (options: ProjectModelOption[], defaultKey: string | null) => void;
  disabled?: boolean;
  className?: string;
}

/**
 * The models this project can actually RUN — `GET /model/options`, and nothing else.
 *
 * WHY ONE ENDPOINT AND NOT TWO. The platform tracks two different facts and this
 * control used to try to compose them in the browser:
 *
 *   `/model/allowed/project` — the GRANT cascade (org grant → the Business Unit it
 *      reaches → what was pushed to the project). What an admin ticked. Every entry
 *      carries `credentialId: null`, because nothing on that payload claims a key
 *      exists for it.
 *   `/model/availability`    — per BUSINESS UNIT, whether anyone has keyed a model.
 *      A unit-level answer, so it is true for models this project has no keyed,
 *      verified connection to.
 *
 * Neither answers "can this project run this model right now". `/model/options` does:
 * `model_offerings` joined to a provider connection that is keyed and verified, then
 * intersected with that same cascade server-side. Reading the grant list instead is
 * what put `ai21 · jamba-1.5-mini` in this control on a project with two runnable
 * models — first alphabetically in a list of seven that could not answer a message.
 *
 * The old version listed the granted-but-unkeyed models anyway, disabled and labelled
 * "No key", on the argument that hiding them strands an admin who granted a model and
 * cannot find it. That argument is sound and it belongs on the project's Models screen
 * — which already names exactly that set (`unusableModelKeys`) and is where the fix
 * gets made. It does not belong on a composer, where the list's only job is "what can
 * answer the message I am about to send". The empty state below links there, so the
 * reason is still one click away rather than absent.
 *
 * WHAT THIS COSTS ELSEWHERE: nothing. This component is the Orchestrator's alone
 * (`components/orchestrator/cockpit.tsx` is its only importer); the selection concept
 * stays untouched in `components/app/project-model-selection-card.tsx`, which is the
 * surface that concept is for.
 *
 * A Popover + `cmdk` combobox rather than a `Select`, matching `ProjectPicker`: model
 * ids are long, near-identical strings that differ in a version suffix
 * ("claude-opus-4-5" vs "claude-opus-5"), which is exactly the list a human scans
 * badly and types well. The filter runs over provider *and* id, so both "anthropic"
 * and "opus" narrow it.
 */
export function ModelPicker({
  projectId,
  value,
  onValueChange,
  onOptionsResolved,
  disabled,
  className,
}: ModelPickerProps) {
  const [open, setOpen] = React.useState(false);

  // The cockpit reads the same query to turn the chosen key into an `offering_id`.
  // Same key, so the two cannot disagree and this costs no extra request.
  const optionsQ = useQuery({
    queryKey: qk.model.options(projectId),
    queryFn: () => getModelOptions(projectId!),
    enabled: !!projectId,
    staleTime: 30_000,
  });

  const options = React.useMemo<ProjectModelOption[]>(
    () =>
      (optionsQ.data?.options ?? []).map((o) => ({
        key: modelKeyOf({
          provider: o.provider,
          model_id: o.model_id,
          credentialId: o.provider_id,
        }),
        offeringId: o.offering_id,
        provider: o.provider,
        model_id: o.model_id,
        connectionName: o.display_name,
        isDefault: o.is_default,
      })),
    [optionsQ.data],
  );

  /**
   * Which one opens selected.
   *
   * Every rung searches WITHIN `options`, so a configured default that names an
   * offering this project can no longer run falls through instead of being honoured
   * — a default outlives the connection it named, and honouring it blindly would
   * reproduce the original defect from the other direction.
   *
   * The last rung is the first runnable option, and it is deliberate. On the live
   * project both `default_offering_id` and `default_model_id` are null, and leaving
   * the control empty would not make the choice explicit: the cockpit then sends
   * `offering_id: null, model_id: null` and the BACKEND picks the organisation
   * default — a model this project may not be granted and this control is not
   * showing. Refusing to choose hands the choice to an invisible fallback. Choosing
   * visibly, from a set where every member is known to run, is the honest version.
   */
  const defaultKey = React.useMemo(() => {
    if (options.length === 0) return null;
    const configuredOffering = optionsQ.data?.default_offering_id;
    const configuredModel = optionsQ.data?.default_model_id;
    return (
      (configuredOffering
        ? options.find((o) => o.offeringId === configuredOffering)
        : undefined) ??
      options.find((o) => o.isDefault) ??
      (configuredModel ? options.find((o) => o.model_id === configuredModel) : undefined) ??
      options[0]!
    ).key;
  }, [options, optionsQ.data]);

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

  if (optionsQ.isLoading) {
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

  // No combobox at all rather than an empty one: there is nothing to choose between,
  // and the models an admin granted are not an answer — they would each fail on the
  // model call. The link goes where that is diagnosed and fixed.
  if (!optionsQ.isError && options.length === 0) {
    return (
      <Link
        href={`/projects/${projectId}/models`}
        className="border-warning/40 bg-warning/[0.06] text-warning hover:border-warning/70 inline-flex h-8 items-center gap-2 rounded-md border px-2.5 text-[12px] transition-colors"
      >
        <BrainCircuit className="size-3.5 shrink-0" aria-hidden />
        No model this project can run
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
          disabled={disabled || optionsQ.isError}
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
                value={`${o.provider} ${o.model_id} ${o.connectionName}`}
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
                {/* Named only when it distinguishes: two connections serving the
                    same model are a real choice, one is noise. */}
                {options.filter((x) => x.model_id === o.model_id).length > 1 && (
                  <span className="text-muted-foreground shrink-0 truncate text-[11px]">
                    {o.connectionName}
                  </span>
                )}
                {o.isDefault && (
                  <span className="text-muted-foreground border-line-soft ml-auto shrink-0 rounded-full border px-1.5 py-px font-mono text-[9px] tracking-wide uppercase">
                    Default
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
