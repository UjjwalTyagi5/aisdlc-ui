/**
 * Scope vocabulary — PRD §12, §12.1.
 *
 * The PRD's four-level hierarchy is:
 *
 *   Organization → Business Unit → Project → Workstream
 *
 * The codebase predates that vocabulary and calls the Business Unit tier a
 * "workspace" — in routes (`/workspaces`), permission strings
 * (`workspace:manage`), request headers (`X-Workspace-Id`) and schemas
 * (`lib/schemas/workspace.ts`).
 *
 * Renaming those would break existing routing, which is out of bounds. So the
 * rule is: **the URL stays `workspace`, the label says "Business Unit".**
 * Every user-facing string routes through this module so the two can never
 * drift, and so a future rename has exactly one place to change.
 */

export type ScopeLevel = "organization" | "business_unit" | "project" | "workstream";

export interface ScopeMeta {
  /** Singular label, as the PRD names it. */
  label: string;
  /** Plural label. */
  labelPlural: string;
  /** What this scope is, one line (PRD §12 table). */
  purpose: string;
  /** The role that owns this scope. */
  owningRole: string;
  /** Route segment the codebase actually uses for this scope, if any. */
  routeSegment: string | null;
}

export const SCOPE_ORDER: readonly ScopeLevel[] = [
  "organization",
  "business_unit",
  "project",
  "workstream",
] as const;

export const SCOPE_META: Record<ScopeLevel, ScopeMeta> = {
  organization: {
    label: "Organization",
    labelPlural: "Organizations",
    purpose: "The whole deployment / tenant.",
    owningRole: "Organization Admin",
    routeSegment: null,
  },
  business_unit: {
    label: "Business Unit",
    labelPlural: "Business Units",
    purpose:
      "A business unit, division or segregated subsidiary — the boundary for budget, connections and blast-radius isolation.",
    owningRole: "Business Unit Admin",
    // Deliberate: the route is /workspaces, the label is "Business Unit".
    routeSegment: "workspaces",
  },
  project: {
    label: "Project",
    labelPlural: "Projects",
    purpose: "A single delivery project bound to one or more repositories.",
    owningRole: "Project Admin",
    routeSegment: "projects",
  },
  workstream: {
    label: "Workstream",
    labelPlural: "Workstreams",
    purpose:
      "A single unit of delivery work — a change, incident or feature — inside a project.",
    owningRole: "Project Admin, delegated to the accountable contributor",
    routeSegment: "runs",
  },
};

/** The user-facing name for the Business Unit tier. Never hardcode this. */
export const BUSINESS_UNIT_LABEL = SCOPE_META.business_unit.label;
export const BUSINESS_UNIT_LABEL_PLURAL = SCOPE_META.business_unit.labelPlural;

/** The user-facing name for a workstream (the code calls these "runs"). */
export const WORKSTREAM_LABEL = SCOPE_META.workstream.label;
export const WORKSTREAM_LABEL_PLURAL = SCOPE_META.workstream.labelPlural;

export function scopeLabel(level: ScopeLevel, plural = false): string {
  return plural ? SCOPE_META[level].labelPlural : SCOPE_META[level].label;
}
