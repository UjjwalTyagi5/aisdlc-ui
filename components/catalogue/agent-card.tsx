"use client";

import * as React from "react";
import { ArrowRight, Star } from "lucide-react";

import { cn } from "@/lib/utils";
import { PHASE_LABEL } from "@/lib/agents";
import { ROLE_META } from "@/lib/roles";
import { TRACK_META } from "@/lib/tracks";
import type { DeliveryTrack } from "@/lib/schemas/enums";
import {
  profileFor,
  tracksForAgent,
  type CatalogueAgent,
} from "@/lib/catalogue";
import {
  CapabilityClassChip,
  Chip,
  Disclosure,
  LabelledList,
} from "@/components/catalogue/catalogue-primitives";

/**
 * One agent, as a catalogue entry.
 *
 * The card shows identity and reach at a glance — name, purpose, owning role,
 * which tracks it runs in — and holds the per-track specifics behind a
 * disclosure. An agent that runs in five tracks has five sets of inputs,
 * outputs and approval flows; flattening those into the card would make every
 * card a page, and hiding them entirely would make the catalogue decorative.
 *
 * `trackFilter` narrows the disclosure to a single track when the catalogue is
 * filtered, so the card answers "what does this agent do *here*" rather than
 * making the reader find the right row.
 */
export function AgentCard({
  agent,
  trackFilter,
  isFavourite,
  onToggleFavourite,
  onOpen,
  style,
}: {
  agent: CatalogueAgent;
  trackFilter?: DeliveryTrack | null;
  isFavourite: boolean;
  onToggleFavourite: () => void;
  onOpen: () => void;
  style?: React.CSSProperties;
}) {
  const tracks = tracksForAgent(agent.phase);
  const shown = trackFilter ? tracks.filter((t) => t === trackFilter) : tracks;
  const owner = ROLE_META[agent.ownerRole];

  return (
    <article
      className={cn(
        "border-line-soft bg-panel-elevated group relative flex flex-col gap-3 rounded-2xl border p-5",
        "transition-all duration-200 hover:-translate-y-[2px] hover:border-border",
        "hover:shadow-[0_14px_30px_-16px_oklch(0_0_0_/_0.5)]",
      )}
      style={style}
    >
      <div className="flex items-start justify-between gap-3">
        <button
          onClick={onOpen}
          className="min-w-0 flex-1 text-left focus-visible:ring-ring rounded focus-visible:ring-2 focus-visible:outline-none"
        >
          <h3 className="font-display truncate text-[16px] font-bold tracking-[-0.01em]">
            {agent.name}
          </h3>
          <p className="text-muted-foreground mt-1 text-[12.5px] leading-relaxed">{agent.purpose}</p>
        </button>
        <button
          onClick={onToggleFavourite}
          aria-pressed={isFavourite}
          aria-label={isFavourite ? `Remove ${agent.name} from favourites` : `Add ${agent.name} to favourites`}
          className={cn(
            "focus-visible:ring-ring shrink-0 rounded-md p-1.5 transition-colors focus-visible:ring-2 focus-visible:outline-none",
            isFavourite
              ? "text-brand-bright"
              : "text-muted-foreground/40 hover:text-muted-foreground",
          )}
        >
          <Star className={cn("size-4", isFavourite && "fill-current")} aria-hidden />
        </button>
      </div>

      {/* Owning role — the single most useful fact after the purpose, because
          it says who approves this agent's gated actions. */}
      <p className="text-muted-foreground font-mono text-[11px]">
        Owned by <span className="text-foreground font-semibold">{owner.label}</span>
        <span className="opacity-60"> · Project Admin is the fallback approver</span>
      </p>

      <div className="flex flex-wrap items-center gap-1.5">
        {agent.classes.map((c) => (
          <CapabilityClassChip key={c} value={c} />
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        {tracks.map((t) => (
          <Chip key={t} tone={trackFilter === t ? "brand" : "neutral"}>
            T{TRACK_META[t].number} {TRACK_META[t].shortLabel}
          </Chip>
        ))}
      </div>

      {agent.dependsOn.length > 0 && (
        <p className="text-muted-foreground font-mono text-[10.5px]">
          Reads from {agent.dependsOn.map((p) => PHASE_LABEL[p]).join(", ")}
        </p>
      )}

      <div className="mt-auto space-y-2 pt-1">
        {shown.map((t) => {
          const profile = profileFor(agent, t);
          if (!profile) return null;
          return (
            <Disclosure
              key={t}
              summary={
                <span className="flex items-center gap-2">
                  <span className="font-mono text-[11px] tracking-wider uppercase">
                    Track {TRACK_META[t].number}
                  </span>
                  <span className="text-muted-foreground">
                    {profile.mode ?? TRACK_META[t].shortLabel}
                  </span>
                </span>
              }
              defaultOpen={shown.length === 1}
            >
              <div className="space-y-3">
                <LabelledList label="Consumes" items={profile.inputs} />
                <LabelledList label="Produces" items={profile.outputs} />
                <div>
                  <p className="text-muted-foreground mb-1 font-mono text-[10px] tracking-[0.12em] uppercase">
                    Approval flow
                  </p>
                  <p className="text-[12.5px] leading-relaxed">{profile.approvalFlow}</p>
                </div>
              </div>
            </Disclosure>
          );
        })}
      </div>

      <div className="flex flex-wrap items-center gap-1.5 pt-1">
        {agent.tags.map((tag) => (
          <span key={tag} className="text-muted-foreground/70 font-mono text-[10px]">
            #{tag.replace(/\s+/g, "")}
          </span>
        ))}
      </div>

      <button
        onClick={onOpen}
        className="text-brand-bright hover:text-foreground mt-1 flex items-center gap-1 font-mono text-[11px] transition-colors"
      >
        Business value
        <ArrowRight className="size-3" aria-hidden />
      </button>
    </article>
  );
}
