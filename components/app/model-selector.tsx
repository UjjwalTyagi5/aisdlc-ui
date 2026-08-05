"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Loader2, SlidersHorizontal } from "lucide-react";

import { cn } from "@/lib/utils";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectSeparator,
  SelectTrigger,
} from "@/components/ui/select";
import { getModelOptions } from "@/lib/api/models";
import { qk } from "@/lib/api/query-keys";
import { providerLabel } from "@/lib/models/provider-labels";
import type { ModelOption, ModelProviderKind } from "@/lib/schemas/model";

/** Subtle per-provider dot — mirrors the admin Model Providers vocabulary without
 * inventing a second icon set. Tints stay quiet (a dot, not a badge). */
const PROVIDER_DOT: Record<ModelProviderKind, string> = {
  anthropic: "bg-[oklch(0.65_0.16_45)]", // clay — Anthropic
  openai: "bg-[oklch(0.7_0.13_165)]", // teal — OpenAI
  google: "bg-[oklch(0.62_0.16_255)]", // blue — Google
};

export interface ModelSelectorProps {
  /** Selected offering id (provider connection + model); undefined → org default / first. */
  value?: string;
  onValueChange: (offeringId: string | undefined) => void;
  /** "compact" → header toolbars (size sm); "field" → forms/dialogs (full width). */
  variant?: "compact" | "field";
  /** Accessible name for the trigger (falls back to "Agent model"). */
  "aria-label"?: string;
  id?: string;
  className?: string;
  disabled?: boolean;
}

/**
 * Reusable LLM picker fed by the org's connected BYOK providers (`GET /model/options`).
 * Selects an OFFERING (a specific provider connection + model), not a bare model id, so
 * two API keys that expose the same model stay distinguishable. The chosen offering_id
 * is what runs dispatch against (see `createRun({offering_id})`).
 */
export function ModelSelector({
  value,
  onValueChange,
  variant = "compact",
  id,
  className,
  disabled,
  "aria-label": ariaLabel = "Agent model",
}: ModelSelectorProps) {
  const optionsQ = useQuery({
    queryKey: qk.model.options(),
    queryFn: () => getModelOptions(),
    staleTime: 60_000,
  });

  const options = React.useMemo(() => optionsQ.data?.options ?? [], [optionsQ.data]);
  const defaultOfferingId = optionsQ.data?.default_offering_id ?? null;
  const compact = variant === "compact";

  // Group by provider CONNECTION (display_name) so two keys for the same model
  // read as distinct, labeled groups.
  const grouped = React.useMemo(() => {
    const by = new Map<string, ModelOption[]>();
    for (const o of options) {
      const list = by.get(o.display_name) ?? [];
      list.push(o);
      by.set(o.display_name, list);
    }
    return [...by.entries()];
  }, [options]);

  const triggerCls = cn(
    "border-line-soft bg-surface-1 gap-2",
    compact ? "h-8 w-auto min-w-[210px] max-w-[340px] text-[12.5px]" : "w-full",
    className,
  );

  // ── Loading ──────────────────────────────────────────────────────────────
  if (optionsQ.isLoading) {
    return (
      <div
        className={cn(
          "border-line-soft bg-surface-1 text-muted-foreground inline-flex items-center gap-2 rounded-md border px-3",
          compact ? "h-8 min-w-[210px] text-[12.5px]" : "h-9 w-full text-sm",
        )}
        aria-busy
      >
        <Loader2 className="size-3.5 animate-spin" aria-hidden />
        Loading models…
      </div>
    );
  }

  // ── Empty — no connected providers → route to Model Providers ─────────────
  if (!optionsQ.isError && options.length === 0) {
    return (
      <Link
        href="/admin/models"
        className={cn(
          "border-line-soft bg-surface-1 text-muted-foreground hover:text-foreground hover:border-border inline-flex items-center gap-2 rounded-md border transition-colors",
          compact ? "h-8 px-2.5 text-[12px]" : "h-9 w-full px-3 text-sm",
        )}
      >
        <SlidersHorizontal className="size-3.5 shrink-0" aria-hidden />
        Connect a model provider
      </Link>
    );
  }

  // The offering to show: explicit choice → org default → first available.
  const selectedId =
    value && options.some((o) => o.offering_id === value)
      ? value
      : defaultOfferingId && options.some((o) => o.offering_id === defaultOfferingId)
        ? defaultOfferingId
        : options[0]?.offering_id;
  const selectedOpt = selectedId
    ? options.find((o) => o.offering_id === selectedId)
    : undefined;

  return (
    <Select
      value={selectedId ?? ""}
      onValueChange={onValueChange}
      disabled={disabled || optionsQ.isError}
    >
      <SelectTrigger id={id} aria-label={ariaLabel} className={triggerCls}>
        {/* Outer span absorbs the trigger's [&>span]:line-clamp-1 (→ -webkit-box);
            the inner flex keeps the dot + label on one row instead of stacking. */}
        <span className="min-w-0 truncate">
          <span className="flex items-center gap-2">
            {selectedOpt && (
              <span
                className={cn("size-2 shrink-0 rounded-full", PROVIDER_DOT[selectedOpt.provider])}
                aria-hidden
              />
            )}
            <span className="truncate text-[12.5px]">
              {selectedOpt ? (
                <>
                  <span className="font-medium">{selectedOpt.display_name}</span>
                  <span className="text-muted-foreground"> · </span>
                  <span className="font-mono">{selectedOpt.model_id}</span>
                </>
              ) : (
                "Select model"
              )}
            </span>
          </span>
        </span>
      </SelectTrigger>
      <SelectContent className="max-h-[min(60vh,22rem)]">
        {grouped.map(([displayName, models], gi) => (
          <React.Fragment key={displayName}>
            {gi > 0 && <SelectSeparator />}
            <SelectGroup>
              <SelectLabel className="flex items-center gap-2 text-[11px]">
                <span
                  className={cn("size-1.5 shrink-0 rounded-full", PROVIDER_DOT[models[0]!.provider])}
                  aria-hidden
                />
                <span className="truncate font-semibold">{displayName}</span>
                <span className="text-muted-foreground/70 font-mono text-[10px] tracking-wide uppercase">
                  {providerLabel(models[0]!.provider)}
                </span>
              </SelectLabel>
              {models.map((m) => (
                <SelectItem key={m.offering_id} value={m.offering_id}>
                  <span className="flex items-center gap-2">
                    <span
                      className={cn("size-1.5 shrink-0 rounded-full", PROVIDER_DOT[m.provider])}
                      aria-hidden
                    />
                    <span className="font-mono text-[12.5px]">{m.model_id}</span>
                    {m.is_default && (
                      <span className="text-success bg-success/10 border-success/30 rounded-full border px-1.5 py-0 font-mono text-[9px] font-semibold uppercase">
                        Default
                      </span>
                    )}
                  </span>
                </SelectItem>
              ))}
            </SelectGroup>
          </React.Fragment>
        ))}
      </SelectContent>
    </Select>
  );
}
