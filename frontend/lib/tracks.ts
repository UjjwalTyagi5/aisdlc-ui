import type { DeliveryTrack, Phase } from "@/lib/schemas/enums";

/**
 * The five delivery tracks — PRD Part II (§6–§11).
 *
 * A track is a configurable delivery *template*, not a separate product
 * (PRD §6, "Track design principle"). All five run on one control plane,
 * one role catalogue and one governance model; what changes per track is
 * the agent roster, the entry context, the required artifacts and the gates.
 *
 * This module is the single source of truth for that roster. Every surface
 * that lists a project's agents — the sidebar rail, the pipeline, the
 * Orchestrator, project overview — derives from `agentsForTrack()` so a
 * project on Track 4 can never render a Track 1 stage it does not have.
 */

export const TRACK_ORDER: readonly DeliveryTrack[] = [
  "greenfield",
  "enhancement",
  "modernization",
  "rpa_infra",
  "data_engineering",
] as const;

export interface TrackMeta {
  /** Track number as the PRD names it ("Track 3"). */
  number: 1 | 2 | 3 | 4 | 5;
  /** Display name. */
  label: string;
  /** Short name for chips and dense table cells. */
  shortLabel: string;
  /** One-line purpose, for pickers and the project header. */
  summary: string;
  /** Primary entry context (PRD §6 table, column 2). */
  entryContext: string;
  /** The PRD section that specifies this track. */
  prdSection: string;
}

export const TRACK_META: Record<DeliveryTrack, TrackMeta> = {
  greenfield: {
    number: 1,
    label: "Greenfield implementation",
    shortLabel: "Greenfield",
    summary:
      "Blank-slate implementation through the full eight-agent roster in hand-off order.",
    entryContext: "Business intent, BRD, discovery inputs, target operating constraints",
    prdSection: "§7",
  },
  enhancement: {
    number: 2,
    label: "Enhancement & support",
    shortLabel: "Enhancement",
    summary:
      "The same eight agents as Greenfield, entered at whichever stage the change actually requires. Design is skipped when no design update is needed.",
    entryContext: "Incident, defect, change request, logs, existing code and runbooks",
    prdSection: "§8",
  },
  modernization: {
    number: 3,
    label: "Code modernization",
    shortLabel: "Modernization",
    summary:
      "The full eight-stage shape plus Discovery & Assessment and Strategy — ten agents, for migrating an unfamiliar legacy system.",
    entryContext:
      "Application inventory, repository, architecture, dependency and runtime data",
    prdSection: "§9",
  },
  rpa_infra: {
    number: 4,
    label: "RPA & infrastructure migration",
    shortLabel: "RPA & Infra",
    summary:
      "A dedicated eight-agent roster flexing across RPA-to-RPA, RPA-to-Code and infrastructure migration flavours.",
    entryContext:
      "Bot inventory, process maps, bot code/configuration, exception logs, infrastructure inventory/IaC",
    prdSection: "§10",
  },
  data_engineering: {
    number: 5,
    label: "Data engineering",
    shortLabel: "Data Eng",
    summary:
      "The Greenfield roster plus the Data Engineering agent, entered from a data intent.",
    entryContext:
      "Source database/warehouse inventory, pipeline/job definitions, target analytics requirements",
    prdSection: "§11",
  },
};

/**
 * Per-track agent roster, in hand-off order, exactly as the PRD stage tables
 * define them. These are rosters, not scripts — nothing auto-advances
 * (PRD §34.11): the user drives every hand-off.
 */
const TRACK_AGENTS: Record<DeliveryTrack, readonly Phase[]> = {
  // PRD §7 — 8 agents.
  greenfield: [
    "requirements",
    "design",
    "plan",
    "development",
    "review",
    "security",
    "testing",
    "deployment",
    "documentation",
  ],
  // PRD §8 — the identical nine-agent portfolio; only the entry point differs.
  enhancement: [
    "requirements",
    "design",
    "plan",
    "development",
    "review",
    "security",
    "testing",
    "deployment",
    "documentation",
  ],
  // PRD §9 — 10 agents: Track 1's eight plus Discovery & Assessment and Strategy.
  modernization: [
    "requirements",
    "discovery",
    "design",
    "strategy",
    "development",
    "review",
    "security",
    "testing",
    "deployment",
    "documentation",
  ],
  // PRD §10 — 8 agents: four migration-specific plus four shared.
  rpa_infra: [
    "requirements",
    "discovery",
    "migration_mapping",
    "development",
    "security",
    "validation",
    "deployment",
    "documentation",
  ],
  // PRD §11 — 6 agents for a data pipeline, no design/development/code-review.
  data_engineering: [
    "requirements",
    "data_engineering",
    "security",
    "testing",
    "deployment",
    "documentation",
  ],
};

/** The agent roster for a track, in hand-off order. */
export function agentsForTrack(track: DeliveryTrack): readonly Phase[] {
  return TRACK_AGENTS[track];
}

/** How many agents a track runs — the "10 agents total" figure in the PRD tables. */
export function agentCountForTrack(track: DeliveryTrack): number {
  return TRACK_AGENTS[track].length;
}

/** Whether a given agent participates in a given track. */
export function trackHasAgent(track: DeliveryTrack, phase: Phase): boolean {
  return TRACK_AGENTS[track].includes(phase);
}

/**
 * Track 2 enters at whichever stage the change requires rather than always at
 * Requirements (PRD §8). The trigger determines the entry agent.
 */
export const ENHANCEMENT_ENTRY_POINTS: {
  trigger: string;
  entry: Phase;
  note: string;
}[] = [
  {
    trigger: "Incident / defect with a clear code fix",
    entry: "requirements",
    note: "Requirements Agent runs in triage / impact-analysis mode.",
  },
  {
    trigger: "Change request needing design",
    entry: "requirements",
    note: "Requirements → Design, then Development onward.",
  },
  {
    trigger: "Small, well-scoped code change",
    entry: "development",
    note: "Straight to Development; Design is skipped entirely.",
  },
];

/**
 * Track 4 flavours (PRD §10). A wave may mix flavours; each agent states
 * which flavour a given capability applies to.
 */
export const RPA_INFRA_FLAVOURS = [
  {
    id: "rpa_to_rpa",
    label: "RPA-to-RPA platform migration",
    detail: "Automation Anywhere A360 → UiPath (primary) or Power Automate (secondary)",
  },
  {
    id: "rpa_to_code",
    label: "RPA-to-Code migration",
    detail: "Retire a bot, replace it with application code calling the API/database directly",
  },
  {
    id: "infrastructure",
    label: "Infrastructure migration",
    detail: "On-prem → cloud, VMs → containers, EOL OS/database → supported version",
  },
] as const;

export type RpaInfraFlavour = (typeof RPA_INFRA_FLAVOURS)[number]["id"];
