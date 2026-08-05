"use client";

import * as React from "react";
import { Boxes, Building2, Globe } from "lucide-react";

import { cn } from "@/lib/utils";
import type { ModelGrantMatrixRow } from "@/lib/schemas/model";

export interface ModelGovernanceCounts {
  providers: number;
  onboarded: number;
  global: number;
}

/**
 * The estate in four numbers: how much you have, how far it reaches, and what
 * is broken.
 *
 * WHAT THIS DELIBERATELY NO LONGER SHOWS. It used to break models into three
 * mutually exclusive reach buckets — org-wide, unit-restricted, not granted —
 * which summed to the total and so looked rigorous. It was the wrong rigour:
 * the sum is arithmetic nobody needed, and two of the three answered a
 * question ("how many models are restricted") that the grant matrix below
 * answers properly, per model and per unit. What the row is for is orientation
 * before you scroll — the size of the estate and whether anything is wrong.
 *
 * `providers` leads because it is the unit the page below is built from: the
 * cards are one per provider, and the first thing to know is how many vendors
 * this organization actually runs on.
 *
 * Counted over MODELS, not grants. A model can be granted twice — Sonnet
 * reaches everyone on the shared platform key and Lending alone on the EU
 * gateway — so counting grants would report more models than the organization
 * has. `global` is therefore "has a global grant", whatever else it carries.
 *
 * No health count here. A keyless-but-granted model is worth surfacing, but
 * against the subscription it affects — the status pill on the provider card
 * and "Holds no key" in the detail table both do that, next to the thing you
 * would have to open to fix it.
 */
export function countModelGovernance(rows: ModelGrantMatrixRow[]): ModelGovernanceCounts {
  // Only models some subscription actually carries. The matrix also lists
  // catalogue entries nobody has onboarded, which are not part of the estate.
  const onboarded = rows.filter((r) => r.credentialId !== null);

  const providers = new Set<string>();
  const models = new Set<string>();
  const global = new Set<string>();

  for (const r of onboarded) {
    providers.add(r.provider);
    models.add(r.model_id);
    if (r.granted && r.visibility === "global") global.add(r.model_id);
  }

  return { providers: providers.size, onboarded: models.size, global: global.size };
}

interface TileSpec {
  key: keyof ModelGovernanceCounts;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  /** Tint applied to the figure only — the row itself stays neutral. */
  tone?: string;
}

const TILES: TileSpec[] = [
  { key: "providers", label: "Providers", icon: Building2 },
  { key: "onboarded", label: "Models", icon: Boxes },
  { key: "global", label: "Org-wide", icon: Globe },
];

/**
 * Three counts across the top of Models — the same tile as Requests &
 * Approvals, deliberately, so two governance screens do not invent two
 * vocabularies for "here is the shape of it".
 *
 * Size only. This row orients you before you scroll and says nothing about
 * health: a warning count here competed with the per-provider status pills and
 * the "Holds no key" marker on the detail table, which both say the same thing
 * against the specific subscription you would have to open anyway.
 */
export function ModelGovernanceSummary({
  counts,
  className,
}: {
  counts: ModelGovernanceCounts;
  className?: string;
}) {
  return (
    <div className={cn("grid grid-cols-1 gap-3 sm:grid-cols-3", className)}>
      {TILES.map(({ key, label, icon: Icon, tone }) => {
        const value = counts[key];
        return (
          <div
            key={key}
            className="border-line-soft bg-panel-elevated rounded-xl border px-4 py-3"
          >
            <div className="text-muted-foreground flex items-center justify-between gap-2">
              <span className="font-mono text-[10px] tracking-[0.12em] uppercase">{label}</span>
              <Icon className="size-3.5 shrink-0" aria-hidden />
            </div>
            <p
              className={cn(
                "font-display mt-1 text-[26px] leading-none font-bold tabular-nums",
                value > 0 && tone,
              )}
            >
              {value}
            </p>
          </div>
        );
      })}
    </div>
  );
}
