/**
 * Permission catalog — PRD §14.10 ("the authoritative, granular permission
 * register"), used by the custom-role builder on Roles & Access (§33.1).
 *
 * The builder only ever shows permissions the *creator* itself holds at that
 * scope, so there is no way to select a permission exceeding the creator's
 * own authority (PRD §14.9). Wildcards are never grantable — `admin:*` is
 * held implicitly by the Organization Admin, never composed into a bundle.
 *
 * The backend re-validates on POST, so this is UX only.
 *
 * DIVERGENCE NOTE — three entries below are *not* in the PRD §14.10 register
 * but exist in the shipped backend vocabulary, and are marked `legacy` so the
 * reconciliation is visible rather than silent:
 *   · workspace:manage — the code's name for Business-Unit administration
 *   · eval:view        — evaluation/quality visibility (PRD FR-08 implies it
 *                        but §14.10 does not register it)
 *   · artifact:approve_<phase> — the code's per-phase split of the PRD's
 *                        single `approve (owned agent)` permission
 */
export interface PermGroup {
  group: string;
  perms: {
    id: string;
    label: string;
    /** Short description of what the permission grants (PRD §14.10 col. 3). */
    grants?: string;
    /** True when the permission is not in the PRD §14.10 register. */
    legacy?: boolean;
  }[];
}

export const PERMISSION_CATALOG: PermGroup[] = [
  {
    group: "Access management",
    perms: [
      {
        id: "member:manage",
        label: "Manage members",
        grants: "Grant/revoke roles within the holder's scope.",
      },
      {
        id: "role:manage",
        label: "Manage roles",
        grants: "Define/adjust built-in role permissions and build custom roles.",
      },
    ],
  },
  {
    group: "Runs",
    perms: [
      { id: "run:create", label: "Start a run", grants: "Start an agent run / workstream." },
      { id: "run:view", label: "View runs", grants: "View a run's transcript and status." },
      { id: "run:cancel", label: "Cancel runs", grants: "Cancel an in-progress run." },
    ],
  },
  {
    group: "Artifacts",
    perms: [
      { id: "artifact:view", label: "View artifacts", grants: "Read approved artifacts within scope." },
      {
        id: "artifact:export",
        label: "Export artifacts",
        grants: "Export an artifact outside the platform.",
      },
      {
        id: "artifact:delete",
        label: "Delete artifacts",
        grants:
          "Permanently delete an artifact and its stored file. Not implied by Export — exporting takes a copy out, deleting destroys the original.",
      },
    ],
  },
  {
    group: "Agents",
    perms: [
      {
        id: "agent:invoke",
        label: "Use an agent",
        grants: "Chat/run an agent's Safe capabilities for an owned or contributed agent.",
      },
      {
        id: "approve",
        label: "Approve for an owned agent",
        grants:
          "Approve a Consequential action or Sign-off for an agent the role owns. Never granted to Developer — a Developer never self-approves.",
      },
      {
        id: "artifact:approve_requirements",
        label: "Approve Requirements",
        legacy: true,
      },
      { id: "artifact:approve_design", label: "Approve Design", legacy: true },
      {
        id: "artifact:approve_plan",
        label: "Approve Plan",
        grants:
          "Accept a project plan — its schedule, assignments and dates. Held by Scrum Master, who owns the PM agent, and Project Admin as fallback approver.",
      },
      { id: "artifact:approve_development", label: "Approve Development", legacy: true },
      // Listed in PIPELINE order, and all eight stages are present. Code Review,
      // Security and Documentation were missing here while the backend's
      // `_PHASE_PERMISSION` gated their gates on exactly these strings — so those
      // three sign-offs were reachable only by an Organization Admin's `admin:*`
      // wildcard, and no admin could delegate them because the checkbox did not
      // exist. A permission the enforcement path demands must be grantable.
      { id: "artifact:approve_code_review", label: "Approve Code Review", legacy: true },
      { id: "artifact:approve_security", label: "Approve Security", legacy: true },
      { id: "artifact:approve_testing", label: "Approve Testing", legacy: true },
      { id: "artifact:approve_deployment", label: "Approve Deployment", legacy: true },
      { id: "artifact:approve_documentation", label: "Approve Documentation", legacy: true },
    ],
  },
  {
    group: "Connectors",
    perms: [
      {
        id: "connector:view",
        label: "View connectors",
        grants: "See which connectors/connections are available.",
      },
      {
        id: "connector:request",
        label: "Request a connector",
        grants: "Request a new connector/connection.",
      },
      {
        id: "connector:manage",
        label: "Register / edit a connection",
        grants: "Register or edit a connection.",
      },
    ],
  },
  {
    group: "Governance",
    perms: [
      {
        id: "governance:decide",
        label: "Decide governance requests",
        grants:
          "Approve or reject a governance request. Not the same as being the person it is waiting on — the escalation chain still decides whose turn it is. Withdrawing your own request and escalating a stalled one need no permission: both are open to the person who raised it.",
        legacy: true,
      },
    ],
  },
  {
    group: "Configuration",
    perms: [
      { id: "project:create", label: "Create projects", grants: "Create a new project." },
      {
        id: "project:update",
        label: "Edit project settings",
        grants:
          "Change a project's name, wiring and total budget. Held by the Business Unit Admin and by the project's own admin — who is the person asked to set the budget at creation.",
        legacy: true,
      },
      {
        id: "model:manage",
        label: "Manage models",
        grants:
          "Approve models and grant them to business units (organization) / credential granted models (business unit, project).",
      },
      {
        id: "settings:manage",
        label: "Manage org settings",
        grants: "Organization-wide policy/compliance settings.",
      },
      {
        id: "workspace:manage",
        label: "Manage business units",
        grants: "Create, suspend, reassign and remove business units.",
        legacy: true,
      },
    ],
  },
  {
    group: "Observability",
    perms: [
      { id: "audit:view", label: "View the audit trail", grants: "View the audit trail." },
      { id: "cost:view", label: "View spend & budget", grants: "View spend/budget." },
      {
        id: "trace:view",
        label: "View traces",
        grants: "View execution traces within scope.",
      },
      { id: "eval:view", label: "View eval / quality", legacy: true },
    ],
  },
  {
    group: "Agent Studio",
    perms: [
      {
        id: "skill:edit",
        label: "Edit in own sandbox",
        grants: "Modify a skill/template configuration in the holder's own sandbox only.",
      },
      {
        id: "skill:edit:project",
        label: "Edit project defaults",
        grants: "Modify a project-scope skill/template default.",
      },
      {
        id: "skill:promote",
        label: "Submit a promotion",
        grants: "Submit a promotion request (sandbox→project or project→business unit).",
      },
      {
        id: "skill:approve",
        label: "Approve a promotion",
        grants: "Approve or reject a promotion request — never the requester (four-eyes).",
      },
      {
        id: "skill:import",
        label: "Import a skill pack",
        grants: "Import a skill/template pack from an external source or another business unit.",
      },
    ],
  },
];

/** Flat list of grantable permission ids. */
export const ALL_GRANTABLE_PERMISSIONS = PERMISSION_CATALOG.flatMap((g) =>
  g.perms.map((p) => p.id),
);

/** Permissions registered in PRD §14.10 — excludes the legacy reconciliations. */
export const PRD_PERMISSIONS = PERMISSION_CATALOG.flatMap((g) =>
  g.perms.filter((p) => !p.legacy).map((p) => p.id),
);
