"use client";

import * as React from "react";
import { ShieldCheck } from "lucide-react";

import { cn } from "@/lib/utils";
import { PHASE_LABEL } from "@/lib/agents";
import { ROLE_META } from "@/lib/roles";
import { TRACK_META, TRACK_ORDER } from "@/lib/tracks";
import type { DeliveryTrack } from "@/lib/schemas/enums";
import { AGENT_BY_PHASE, TRACK_DETAIL, agentsInTrack } from "@/lib/catalogue";
import { Chip } from "@/components/catalogue/catalogue-primitives";

/**
 * The workflow map — every track as a stage rail, with its governance
 * checkpoints beneath.
 *
 * Deliberately a rail rather than a node-and-edge diagram. The PRD is explicit
 * that progression is user-driven, not automatic ("they behave as ordered
 * pipeline stages even though progression itself is user-driven"), so drawing
 * arrows and branches would assert an automation the platform does not claim.
 * A numbered rail says "this is the order" without implying it advances on its
 * own.
 *
 * The rails are generated from `agentsInTrack`, so a change to a track's roster
 * redraws this with no edit here.
 */
export function WorkflowMap({
  selected,
  onSelect,
}: {
  selected: DeliveryTrack | null;
  onSelect: (track: DeliveryTrack | null) => void;
}) {
  const tracks = selected ? [selected] : TRACK_ORDER;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-1.5">
        <button
          onClick={() => onSelect(null)}
          className={cn(
            "rounded-full border px-3 py-1 font-mono text-[11px] transition-colors",
            selected === null
              ? "border-brand-bright/40 bg-brand-bright/10 text-brand-bright"
              : "border-line-soft text-muted-foreground hover:text-foreground",
          )}
        >
          All tracks
        </button>
        {TRACK_ORDER.map((t) => (
          <button
            key={t}
            onClick={() => onSelect(t)}
            className={cn(
              "rounded-full border px-3 py-1 font-mono text-[11px] transition-colors",
              selected === t
                ? "border-brand-bright/40 bg-brand-bright/10 text-brand-bright"
                : "border-line-soft text-muted-foreground hover:text-foreground",
            )}
          >
            T{TRACK_META[t].number} · {TRACK_META[t].shortLabel}
          </button>
        ))}
      </div>

      {tracks.map((track) => {
        const agents = agentsInTrack(track);
        const detail = TRACK_DETAIL[track];
        return (
          <div key={track} className="border-line-soft bg-panel-elevated rounded-2xl border p-5">
            <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
              <h3 className="font-display text-[15px] font-bold tracking-[-0.01em]">
                Track {TRACK_META[track].number} · {TRACK_META[track].label}
              </h3>
              <Chip>{agents.length} agents</Chip>
            </div>

            {/* Stage rail — horizontally scrollable rather than wrapped, so the
                sequence stays readable as a sequence on a narrow screen. */}
            <div className="-mx-1 overflow-x-auto px-1 pb-2">
              <ol className="flex min-w-max items-stretch gap-2">
                {agents.map((agent, i) => (
                  <li key={agent.phase} className="flex items-stretch gap-2">
                    <div className="border-line-soft bg-surface-1 flex w-[150px] flex-col gap-1.5 rounded-xl border p-3">
                      <span className="text-muted-foreground font-mono text-[10px]">
                        {String(i + 1).padStart(2, "0")}
                      </span>
                      <span className="text-[12.5px] leading-tight font-semibold">
                        {PHASE_LABEL[agent.phase]}
                      </span>
                      <span className="text-muted-foreground mt-auto font-mono text-[10px]">
                        {ROLE_META[agent.ownerRole].shortLabel}
                      </span>
                    </div>
                    {i < agents.length - 1 && (
                      <span
                        className="text-muted-foreground/30 self-center font-mono text-sm"
                        aria-hidden
                      >
                        →
                      </span>
                    )}
                  </li>
                ))}
              </ol>
            </div>

            <div className="border-line-soft mt-4 border-t pt-3">
              <p className="text-muted-foreground mb-2 flex items-center gap-1.5 font-mono text-[10px] tracking-[0.12em] uppercase">
                <ShieldCheck className="text-brand-bright size-3" aria-hidden />
                Governance checkpoints
              </p>
              <ul className="flex flex-wrap gap-1.5">
                {detail.checkpoints.map((c) => (
                  <li key={c}>
                    <Chip>{c}</Chip>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/**
 * Which agents a role owns, across every track — the persona view of the same
 * data the workflow map draws.
 *
 * Ownership comes from `AGENT_OWNER_ROLE` via the catalogue, so this cannot
 * disagree with the gate rows that route approvals to the same role.
 */
export function PersonaAgentList({ phases }: { phases: readonly string[] }) {
  if (phases.length === 0) {
    return (
      <p className="text-muted-foreground text-[12px] italic">
        Owns no agent gate — participates without holding an approval.
      </p>
    );
  }
  return (
    <ul className="flex flex-wrap gap-1.5">
      {phases.map((p) => {
        const agent = AGENT_BY_PHASE.get(p as never);
        if (!agent) return null;
        return (
          <li key={p}>
            <Chip tone="brand">{agent.name}</Chip>
          </li>
        );
      })}
    </ul>
  );
}
