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

/** The minimum of a ScopeBinding this module needs, kept structural so callers can
 *  pass what they already resolved without this file importing the schema. */
export interface NamedScopeBinding {
  kind: string;
  scopeId: string;
  scopeName: string;
}

/**
 * What the scope chip should say — the viewer's boundary, named.
 *
 * ONE RULE, BECAUSE "0 business units" KEPT COMING BACK. Three surfaces derived this
 * independently and each got it wrong for a different persona:
 *
 *   - the Cost page and the app-wide ScopeContextBar counted `managedBusinessUnitIds`,
 *     which is empty for a Project Admin bound at unit scope — they are IN a unit, they
 *     simply do not run it — so the chip told them they were nowhere;
 *   - the Projects page counted the units present in the PROJECT LIST, so a Business
 *     Unit Admin whose unit held no projects yet got "0 business units" while
 *     administering one.
 *
 * Both are the same mistake: inferring where someone is from something other than where
 * they are. A count of zero on a chip is almost always this bug rather than the truth —
 * a viewer with no scope at all gets an empty state, not a chip.
 *
 * ADMINISTERED FIRST, THEN MERELY HELD. Naming the unit somebody runs is more useful
 * than naming one they contribute to, so a sole managed unit wins; otherwise any sole
 * unit is named; otherwise the count of units they are in.
 */
export function scopeChipName(
  level: ScopeLevel,
  opts: {
    isOrgWide: boolean;
    bindings: NamedScopeBinding[];
    managedBusinessUnitIds: string[];
    /** Overrides the unit name when the caller has a better one — e.g. the Projects
     *  page, where a single group heading already names the unit the list came from. */
    preferredName?: string | null;
  },
): string | null {
  const { isOrgWide, bindings, managedBusinessUnitIds, preferredName } = opts;
  if (isOrgWide) return null;

  const units = bindings.filter((b) => b.kind === "business_unit");
  const projects = bindings.filter((b) => b.kind === "project");

  if (level === "business_unit") {
    if (preferredName) return preferredName;
    const soleManaged =
      managedBusinessUnitIds.length === 1
        ? units.find((b) => b.scopeId === managedBusinessUnitIds[0])
        : undefined;
    if (soleManaged) return soleManaged.scopeName;
    if (units.length === 1) return units[0]!.scopeName;
    return `${units.length} ${units.length === 1 ? BUSINESS_UNIT_LABEL.toLowerCase() : BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}`;
  }

  if (preferredName) return preferredName;
  if (projects.length === 1) return projects[0]!.scopeName;
  return projects.length > 0 ? `${projects.length} projects` : null;
}
