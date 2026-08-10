import { PHASE_ALL, PHASE_LABEL } from "@/lib/agents";
import { AGENT_OWNERSHIP, type Involvement, type PlatformRole } from "@/lib/roles";
import { agentsForTrack } from "@/lib/tracks";
import type { DeliveryTrack, Phase } from "@/lib/schemas/enums";

/**
 * WHICH AGENTS A ROLE REACHES.
 *
 * Derived from `AGENT_OWNERSHIP` (lib/roles.ts), which is the PRD's own role ×
 * agent involvement table. Deliberately NOT a second list: the involvement
 * scale already encodes the answer, and a parallel "agents this role may use"
 * map would be the first thing to drift when a role's involvement changes.
 *
 * The rule is the whole of it — `none` means no reach, everything else means
 * reach. The other five values (`owner`, `primary`, `build`, `requests`,
 * `use`) differ in what you do WITH an agent, not in whether you may open it,
 * and collapsing them here is what keeps this one question answerable in one
 * place. Where the distinction matters — who signs off, who may run — the
 * callers already read `AGENT_OWNERSHIP` or `GATE_POLICY` directly.
 *
 * A consequence worth stating plainly, because it will look like a bug
 * otherwise: most delivery roles are `use` on every agent, so `locked` comes
 * back empty for them. Today only the governance tier (no agent access at all)
 * and Developer (`design`, `deployment`) have anything locked. That is the
 * data's current answer, not a gap in this function — narrow a role in
 * `AGENT_OWNERSHIP` and every surface built on this follows.
 */
export function involvementFor(role: PlatformRole, phase: Phase): Involvement {
  return AGENT_OWNERSHIP[role][phase];
}

/** Does this role reach this agent at all? */
export function roleReachesAgent(role: PlatformRole, phase: Phase): boolean {
  return involvementFor(role, phase) !== "none";
}

export interface RoleAgentSplit {
  /** Agents this role reaches by default, in pipeline order. */
  reachable: Phase[];
  /** Agents it does not — the ones a person would have to request. */
  locked: Phase[];
}

/**
 * Split a roster into what a role reaches and what it doesn't.
 *
 * `track` narrows the roster to the agents a project actually runs
 * (`agentsForTrack`) — listing Data Engineering as "locked" on a Greenfield
 * project would invite a request for an agent that project will never run.
 * Omit it to consider the full thirteen, which is what the project-creation
 * dialog does before a track is chosen.
 */
export function roleAgentSplit(
  role: PlatformRole,
  track?: DeliveryTrack,
): RoleAgentSplit {
  // PHASE_ALL, not PHASE_ORDER. PHASE_ORDER is the greenfield pipeline's
  // eight stages; describing what a ROLE reaches needs all thirteen, or the
  // five track-specific agents silently read as "not locked" because they
  // were never considered.
  const roster = track ? agentsForTrack(track) : PHASE_ALL;
  const reachable: Phase[] = [];
  const locked: Phase[] = [];
  for (const phase of roster) {
    (roleReachesAgent(role, phase) ? reachable : locked).push(phase);
  }
  return { reachable, locked };
}

/** Agent names for a role, ready to print — "Requirements, Design, Testing". */
export function agentNamesFor(role: PlatformRole, track?: DeliveryTrack): string[] {
  return roleAgentSplit(role, track).reachable.map((p) => PHASE_LABEL[p]);
}
