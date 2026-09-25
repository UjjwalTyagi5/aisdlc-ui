"use client";

import * as React from "react";
import { Check, Plus } from "lucide-react";

import { roleAgentSplit } from "@/lib/agent-access";
import { PHASE_LABEL } from "@/lib/agents";
import { AGENT_OWNERSHIP, type PlatformRole } from "@/lib/roles";
import type { DeliveryTrack, Phase } from "@/lib/schemas";

/**
 * The agents a chosen role reaches, named — plus the ones it doesn't, as
 * checkboxes.
 *
 * WHY IT SHOWS THE LOCKED ONES AT ALL. Granting extra access is only a real
 * choice if you can see what is missing; a list of what the role already has
 * answers "is this enough?" with no way to act on "no". The split is the
 * whole component: reachable agents are stated as fact, unreachable ones are
 * offered as a decision.
 *
 * Both halves come from `AGENT_OWNERSHIP` via `roleAgentSplit`, narrowed to
 * the track's roster. A custom role has no entry in that table, so it falls
 * back to naming nothing rather than guessing — see the note below.
 */
export function RoleAgentPreview({
  roleName,
  track,
  extra,
  onToggleExtra,
}: {
  roleName: string;
  track: DeliveryTrack;
  extra: Phase[];
  onToggleExtra: (phase: Phase) => void;
}) {
  const isBuiltIn = roleName in AGENT_OWNERSHIP;
  const split = React.useMemo(
    () =>
      isBuiltIn
        ? roleAgentSplit(roleName as PlatformRole, track)
        : { reachable: [], locked: [] },
    [isBuiltIn, roleName, track],
  );

  if (!isBuiltIn) {
    return (
      <p className="text-muted-foreground text-[11.5px]">
        Agent access for a custom role is whatever its permissions grant — it
        isn&apos;t in the platform&apos;s role × agent table, so there is no default roster to
        preview here.
      </p>
    );
  }

  // Once granted, an extra agent reads as a fact like any default one — it
  // moves up into "Gets these agents" (green) instead of sitting in "Grant
  // extra" merely recolored, which used to leave a granted agent looking like
  // a still-pending option. It stays a button there (unlike the true
  // defaults, which are plain text) because granting is reversible: clicking
  // it again drops it back down to "Grant extra".
  const grantedExtra = split.locked.filter((p) => extra.includes(p));
  const ungrantedExtra = split.locked.filter((p) => !extra.includes(p));

  return (
    <div className="border-line-soft bg-panel-elevated space-y-2.5 rounded-lg border p-2.5">
      <div>
        <p className="text-muted-foreground font-mono text-[10px] tracking-[0.12em] uppercase">
          Gets these agents
        </p>
        {split.reachable.length === 0 && grantedExtra.length === 0 ? (
          <p className="text-muted-foreground mt-1 text-[11.5px]">
            None by default on this track.
          </p>
        ) : (
          <div className="mt-1.5 flex flex-wrap gap-1">
            {split.reachable.map((p) => (
              <span
                key={p}
                className="border-success/30 bg-success/10 text-success inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10.5px]"
              >
                <Check className="size-2.5" aria-hidden />
                {PHASE_LABEL[p]}
              </span>
            ))}
            {grantedExtra.map((p) => (
              <button
                key={p}
                type="button"
                onClick={() => onToggleExtra(p)}
                aria-pressed
                title="Click to remove this extra agent"
                className="border-success/30 bg-success/10 text-success hover:bg-success/15 inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10.5px] transition-colors"
              >
                <Check className="size-2.5" aria-hidden />
                {PHASE_LABEL[p]}
              </button>
            ))}
          </div>
        )}
      </div>

      {ungrantedExtra.length > 0 && (
        <div className="border-line-soft border-t pt-2.5">
          <p className="text-muted-foreground font-mono text-[10px] tracking-[0.12em] uppercase">
            Grant extra · optional
          </p>
          <div className="mt-1.5 flex flex-wrap gap-1">
            {ungrantedExtra.map((p) => (
              <button
                key={p}
                type="button"
                onClick={() => onToggleExtra(p)}
                aria-pressed={false}
                className="border-line-soft bg-surface-1 text-muted-foreground hover:text-foreground inline-flex items-center gap-1 rounded-full border border-dashed px-2 py-0.5 font-mono text-[10.5px] transition-colors"
              >
                <Plus className="size-2.5" aria-hidden />
                {PHASE_LABEL[p]}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
