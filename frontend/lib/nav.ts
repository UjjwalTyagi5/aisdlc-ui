import { ROLE_ORDER, type PlatformRole } from "@/lib/roles";
import { AGENT_DEFAULT_OWNER_ROLE } from "@/lib/governance";
import {
  Activity,
  Boxes,
  BrainCircuit,
  Building2,
  Coins,
  FolderKanban,
  Inbox,
  KeyRound,
  LayoutDashboard,
  Plug,
  ScrollText,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  UserCircle,
  Users,
  Waypoints,
  Workflow,
  type LucideIcon,
} from "lucide-react";

export type Role = "admin" | "member" | "viewer";

/**
 * SIDEBAR GROUP LABELS vs the identifiers below.
 *
 * The three groups render as Build, Control plane and Observability. The
 * exported arrays are still `deliverNav`, `governNav` and `observeNav`, and
 * that mismatch is deliberate rather than an oversight: renaming the symbols
 * would touch the sidebar, the mobile sidebar, the command palette and their
 * tests for no behavioural gain, and the labels are the kind of thing that
 * gets revised again. The mapping is stated here so the next reader does not
 * go looking for a `buildNav` that never existed.
 *
 *   deliverNav → "Build"          the work itself
 *   governNav  → "Control plane"  who may do what (PRD §32.2)
 *   observeNav → "Observability"  what happened, and what it cost
 */

/**
 * Roles for whom the GLOBAL Agent Studio entry is not the way in.
 *
 * Derived from `AGENT_DEFAULT_OWNER_ROLE` rather than listed: the three roles
 * that own a shared tier of the cascade (Org, Business Unit, Project) are the
 * ones who arrive at Agent Studio to publish a default other people inherit,
 * and they need it reachable from anywhere. Everyone else has only their
 * Personal tier, which is an override applied to their own runs inside a
 * project — so their way in is the project's own entry, seeded with that
 * project (`projectNav` below), and Personal is one click from there.
 *
 * Listing the nine contributor roles by hand would be the same set today and
 * the first thing to drift when a role is added.
 */
const AGENT_STUDIO_TIER_OWNERS = new Set<PlatformRole>(
  Object.values(AGENT_DEFAULT_OWNER_ROLE).filter((r): r is PlatformRole => r !== null),
);
const AGENT_STUDIO_GLOBAL_HIDDEN: readonly PlatformRole[] = ROLE_ORDER.filter(
  (r) => !AGENT_STUDIO_TIER_OWNERS.has(r),
);

export interface NavItem {
  label: string;
  href: string;
  icon: LucideIcon;
  segment: string;
  requireRole?: Role;
  /** A single required permission, or an array meaning "any of these" (OR). */
  requirePermission?: string | string[];
  hiddenInSidebar?: boolean;
  /** PRD section that specifies this screen — surfaced in dev tooling only. */
  prdSection?: string;
  /**
   * Platform roles this entry is hidden from even though their permissions
   * would pass.
   *
   * A permission check answers "could this person act here at all", which is a
   * coarser question than "is this page any use to them". The governance tier
   * holds `artifact:view`-adjacent permissions via `admin:*` but has no agent
   * access whatsoever (PRD §14.8), so Agent Studio renders for them as a
   * fully-populated editor for agents they may never run — worse than absent,
   * because it implies a capability that isn't there. Use this only for that
   * case: a page the role can technically open but which is structurally
   * meaningless to them. Anything a role must not *do* belongs in a permission.
   */
  hideForRoles?: readonly PlatformRole[];
  /**
   * Requires at least one binding at this scope to be useful.
   *
   * `"business_unit"` hides an entry from someone who administers no unit — a
   * Project Admin opening Business Units would otherwise land on a list
   * filtered down to the one unit they can read but not manage, which reads as
   * a broken admin page rather than an intentional boundary.
   */
  requireScope?: "organization" | "business_unit";
}

/**
 * The platform's fixed page set — PRD §15.1 and §32.1.
 *
 * "The platform has one fixed set of pages — the same for everyone. What
 * changes by role is only which pages a person can open and what they can do
 * inside them." Each role therefore sees a *scoped version of this same set*,
 * never a different module set.
 *
 * Grouping below is presentational only; it does not add or remove pages.
 *
 * NOTE ON VOCABULARY — the PRD calls the middle scope a **Business Unit**
 * (§12, §12.1). The routes and permission strings still say `workspace`
 * (`/workspaces`, `workspace:manage`, `X-Workspace-Id`) because renaming them
 * would break existing routing. The user-facing label is the PRD's;
 * the URL is the codebase's. See `lib/scope.ts`.
 */

// ─── Build — the work itself ──────────────────────────────────────────────────
export const deliverNav: NavItem[] = [
  {
    label: "Dashboard",
    href: "/dashboard",
    icon: LayoutDashboard,
    segment: "dashboard",
    prdSection: "§36",
  },
  {
    label: "Projects",
    href: "/projects",
    icon: FolderKanban,
    segment: "projects",
    prdSection: "§36",
  },
  {
    label: "Requests & Approvals",
    href: "/approvals",
    icon: Inbox,
    segment: "approvals",
    // artifact:view floors agent-run gates (approver roles); workspace:manage
    // floors governance approvals (project creation, model credentials) —
    // org_admin/bu_admin hold the latter but never the former.
    requirePermission: ["artifact:view", "workspace:manage"],
    prdSection: "§33.2",
  },
  {
    /**
     * The cross-project auto-sequencing cockpit — pick a project, pick one of
     * the models it is allowed to run on, and it executes that project's agent
     * roster in hand-off order.
     *
     * Distinct from the per-project Orchestrator at
     * `/projects/[id]/orchestrator`, which is the PRD §34.11 reading (a
     * conversation partner that never auto-advances). Both routes are live;
     * this entry points at the sequencing one because that is the surface you
     * come to the sidebar to start work in.
     *
     * Gated on `artifact:view` and nothing else, deliberately matching
     * `/projects/[id]/orchestrator`: anyone who can open that page can open
     * this one. Driving is narrower than opening — the page itself renders
     * read-only for everyone but the Project Admin (PRD §15.5–§15.11), the
     * same split the per-project page already makes.
     */
    label: "Orchestrator",
    href: "/orchestrator",
    icon: Workflow,
    segment: "orchestrator",
    requirePermission: "artifact:view",
    // Hidden from the governance tier for the same reason as Agent Studio: it
    // is a cockpit for RUNNING a project's agent roster, and neither admin tier
    // has agent access at all (PRD §14.8). They govern who may run what; the
    // running itself belongs to the delivery roles inside a project.
    hideForRoles: ["org_admin", "bu_admin"],
    prdSection: "§34.11",
  },
  {
    label: "Agent Studio",
    href: "/agent-studio",
    icon: SlidersHorizontal,
    segment: "agent-studio",
    // NO LONGER HIDDEN from the governance tier. The original reading — "they
    // have no agent access, so a prompt editor is a page about a capability
    // they don't hold" — was right about RUNNING an agent and wrong about
    // this page, which is not where agents are run. It is where each tier
    // publishes the default instructions the tiers beneath it inherit, and
    // the Org and Business Unit tiers of that cascade are precisely the two
    // these roles own (AGENT_DEFAULT_OWNER_ROLE). Hiding it left both tiers
    // ownerless: nobody could set an org-wide or unit-wide default at all.
    //
    // Orchestrator stays hidden, and the difference is the point — that one
    // IS running agents.
    //
    // Hidden from contributors, who hold no shared tier — see
    // AGENT_STUDIO_GLOBAL_HIDDEN. Not a loss of access: their Personal tier
    // is reached from the project they are working in, where the override
    // actually applies.
    hideForRoles: AGENT_STUDIO_GLOBAL_HIDDEN,
    prdSection: "§34.6",
  },
];

// ─── Control plane (PRD §32.2) ────────────────────────────────────────────────
export const governNav: NavItem[] = [
  {
    label: "Business Units",
    href: "/workspaces",
    icon: Building2,
    segment: "workspaces",
    requirePermission: "workspace:manage",
    // Administering at least one unit is what makes this page an admin surface.
    requireScope: "business_unit",
    prdSection: "§34.1",
  },
  {
    label: "Users",
    href: "/users",
    icon: Users,
    segment: "users",
    requirePermission: "member:manage",
    // ADMINISTERING A UNIT, not merely holding `member:manage`. A Project Admin holds
    // that permission too — for their project's own roster — and it let them open an
    // ORG-WIDE people directory with a role-assignment control on every row. The
    // permission answers "may this person manage members somewhere"; the scope answers
    // "over whom", and only the second makes this page theirs.
    requireScope: "business_unit",
    prdSection: "§36",
  },
  {
    label: "Roles & Access",
    href: "/admin/access",
    icon: ShieldCheck,
    segment: "admin",
    requirePermission: "member:manage",
    // Same reason as Users. This page states what every built-in role may do across
    // the organisation and lets an Organization Admin retune it — nothing on it is
    // scoped to a project, so a Project Admin opening it reads the whole tenant's
    // permission model with no way to act on any of it.
    requireScope: "business_unit",
    prdSection: "§33.1",
  },
  {
    label: "Model Management",
    href: "/admin/models",
    icon: BrainCircuit,
    segment: "models",
    requirePermission: "model:manage",
    prdSection: "§34.2",
  },
  {
    // Connectors + MCP servers live on one page (app/(app)/integrations) —
    // there is no separate "MCP Servers" surface, so there is no separate
    // nav entry for it either (a bare wrapper page at /admin/mcp rendering
    // the exact same panel used to duplicate this one).
    label: "Integrations",
    href: "/integrations",
    icon: Plug,
    segment: "integrations",
    // `connector:manage`, NOT `connector:view`. This is the estate — which
    // kinds the organization permits and which units hold them — and it is
    // only actionable for the three tiers that grant: Org Admin, Business
    // Unit Admin, Project Admin.
    //
    // Every delivery role holds `connector:view`, so the OR gate put this in
    // a contributor's sidebar too, where it answered a question they don't
    // have. Theirs is "which integrations may I use here, and where does my
    // key go" — that is the project's own Integrations screen, which they
    // still reach both from the project tabs and from the "This project"
    // section of this sidebar.
    requirePermission: "connector:manage",
    prdSection: "§34.3, §34.4",
  },
];

// ─── Observability — the evidence surface (PRD §34.8, §34.9) ──────────────────
/**
 * Traces and the Audit Trail answer the same question — *what happened* —
 * at two altitudes: agent execution spans, and the governance decisions
 * around them. They were two sidebar entries; they are now one **Activity**
 * surface with a tab per altitude, so the sidebar poses the question once.
 *
 * Both routes stay live and keep their own permission (`trace:view` /
 * `audit:view`), which is why the entry points at `/activity` — a redirect
 * that lands each viewer on the tab they actually hold.
 *
 * Cost & Budget sits here too. It was under the control plane on the argument
 * that "what did this cost against which cap" is a budget question rather than
 * an evidence one — but in practice spend is read the same way a trace is: you
 * come to it after the fact, to find out what happened. The cap-setting it
 * also carries is the exception, not the reason you open the page. The control
 * plane is now uniformly "who may do what"; Observability is uniformly "what
 * did happen, and what did it cost".
 */
export const observeNav: NavItem[] = [
  {
    label: "Activity",
    href: "/activity",
    icon: Waypoints,
    segment: "activity",
    requirePermission: ["trace:view", "audit:view"],
    prdSection: "§34.8, §34.9",
  },
  {
    label: "Cost & Budget",
    href: "/cost",
    icon: Coins,
    segment: "cost",
    requirePermission: "cost:view",
    prdSection: "§34.5",
  },
];

/**
 * Reachable but not a sidebar entry.
 *
 * `Runs` is retained as a cross-project convenience view. The PRD puts
 * Workstreams *inside* a project (§32.1), so it no longer occupies a
 * top-level slot — but the route stays live so existing links keep working.
 *
 * `Traces` and `Audit Trail` are tabs of Activity now, not destinations of
 * their own; they stay here so breadcrumbs and deep links keep resolving.
 *
 * `Settings` is a placeholder with nothing to configure, and `settings:manage`
 * is held only by the Organization Admin — so as a sidebar entry it gave
 * exactly one role a link to an empty page. Hidden until it has content;
 * the route is untouched.
 */
/**
 * THE OPEN PROJECT'S OWN NAV, shown only while you are inside one.
 *
 * The global entries above answer governance questions — which models the
 * organization approved, which connectors it permits. A contributor opening
 * their project has a narrower and more immediate one: what can *this*
 * project use, and where do I put my key. Those answers live on
 * project-scoped screens that were previously reachable only through the
 * tabs at the top of a project page, which is a different place to look for
 * the same three nouns the sidebar already names.
 *
 * Rendered as a separate section rather than folded into Deliver, because
 * these hrefs are not static: each one belongs to whichever project is open,
 * and a link that silently changes meaning depending on where you stand
 * should say so by sitting under the project's name.
 *
 * `segment` is matched against the path AFTER the project id — see
 * `projectNavFor` below, which builds the real hrefs.
 */
export interface ProjectNavItem {
  label: string;
  /** Path suffix under /projects/[id], including any query. */
  to: string;
  icon: LucideIcon;
  /** Segment used for the active-state match. */
  segment: string;
}

export const projectNav: ProjectNavItem[] = [
  { label: "Integrations", to: "/integrations", icon: Plug, segment: "integrations" },
  // A page of its own now, not a deep link into Settings. "Which models can I
  // use here" is not a settings question for the people who most often ask it,
  // and the Settings tab stays — both render the same card, so the selection
  // cannot disagree between them.
  { label: "Model Management", to: "/models", icon: Boxes, segment: "models" },
  // Points at the GLOBAL Agent Studio seeded with this project (`?project=`),
  // not at a project-local route — there isn't one, and there shouldn't be.
  // The studio is a drill-down through one cascade (Org → BU → Project →
  // Personal); a second copy rooted at the project would cut it off from the
  // tiers it inherits from, which is the thing you most need to see when
  // deciding whether to override.
  { label: "Agent Studio", to: "", icon: SlidersHorizontal, segment: "agent-studio" },
];

/** The open project's nav, with real hrefs. */
export function projectNavFor(projectId: string): (ProjectNavItem & { href: string })[] {
  return projectNav.map((i) => ({
    ...i,
    // An empty `to` means the entry lives outside /projects and carries the
    // project as a query instead — Agent Studio is the only such case.
    href: i.to
      ? `/projects/${projectId}${i.to}`
      : `/${i.segment}?project=${encodeURIComponent(projectId)}`,
  }));
}

/**
 * The project id in the current path, or null when we are not inside one.
 *
 * Matches `/projects/<id>` and anything below it, but NOT `/projects` itself —
 * the list page has no project open, so a project section there would name
 * nothing.
 */
export function openProjectId(pathname: string): string | null {
  const m = /^\/projects\/([^/?#]+)/.exec(pathname);
  return m ? (m[1] ?? null) : null;
}

export const utilityNav: NavItem[] = [
  {
    /**
     * The platform's discovery portal, and the one entry here with no
     * permission and no scope requirement — it renders only the documented
     * model (agents, tracks, roles, governance) and reads no project, unit or
     * spend data, so there is nothing in it to scope.
     *
     * It sits in the footer beside Help & docs rather than in Deliver because
     * it is reference material, not a place you work: you come to it to learn
     * what exists, then leave. A permanent slot in the main rail would have it
     * competing with the pages people actually operate in, which is the same
     * reasoning that keeps My access out of the rail.
     *
     * `hiddenInSidebar` keeps it out of the main nav loop; the footer renders
     * it explicitly (components/app/sidebar.tsx).
     */
    label: "Agent Catalogue",
    href: "/catalogue",
    icon: Boxes,
    segment: "catalogue",
    hiddenInSidebar: true,
    prdSection: "§20, §21–§25",
  },
  {
    label: "Profile",
    href: "/profile",
    icon: UserCircle,
    segment: "profile",
    prdSection: "§36",
  },
  {
    // Every role's own effective permissions and scope. Deliberately NOT in the
    // sidebar: it is a reference page you visit when something is missing, not
    // a place you work, and a permanent slot would compete with the pages that
    // are. Reached from the user menu, and linked from every empty state that
    // exists because of a scope boundary.
    label: "My access",
    href: "/my-access",
    icon: KeyRound,
    segment: "my-access",
    hiddenInSidebar: true,
    prdSection: "§33.1",
  },
  {
    label: "Traces",
    href: "/traces",
    icon: Waypoints,
    segment: "traces",
    requirePermission: "trace:view",
    hiddenInSidebar: true,
    prdSection: "§34.8",
  },
  {
    label: "Audit Trail",
    href: "/audit",
    icon: ScrollText,
    segment: "audit",
    requirePermission: "audit:view",
    hiddenInSidebar: true,
    prdSection: "§34.9",
  },
  {
    label: "Settings",
    href: "/settings",
    icon: Settings,
    segment: "settings",
    requirePermission: "settings:manage",
    hiddenInSidebar: true,
    prdSection: "§34.10",
  },
  {
    label: "Runs",
    href: "/runs",
    icon: Activity,
    segment: "runs",
    hiddenInSidebar: true,
  },
  {
    label: "Design playground",
    href: "/playground",
    icon: Sparkles,
    segment: "playground",
    hiddenInSidebar: true,
  },
];

/** Flat list — used by the command palette and breadcrumbs. */
export const mainNav: NavItem[] = [
  ...deliverNav,
  ...governNav,
  ...observeNav,
  ...utilityNav,
];

/** Legacy aliases — kept so existing imports keep compiling. */
export const workspaceNav = deliverNav;
export const orgNav = governNav;

export const segmentLabels: Record<string, string> = {
  dashboard: "Dashboard",
  workspaces: "Business Units",
  projects: "Projects",
  users: "Users",
  profile: "Profile",
  "agent-studio": "Agent Studio",
  integrations: "Integrations",
  audit: "Audit Trail",
  admin: "Administration",
  access: "Roles & Access",
  roles: "Roles & Access",
  // "Models", not "Model Management" — the segment is now shared with the
  // project-scoped page, and both screens title themselves Models anyway.
  models: "Models",
  cost: "Cost & Budget",
  catalogue: "Agent Catalogue",
  activity: "Activity",
  "my-access": "My access",
  traces: "Traces",
  settings: "Settings",
  playground: "Playground",
  workstreams: "Workstreams",
  orchestrator: "Orchestrator",
  artifacts: "Artifacts",
  members: "Members",
  requirements: "Requirements",
  design: "Design",
  development: "Development",
  review: "Code Review",
  "code-review": "Code Review",
  security: "Security",
  testing: "Testing",
  runs: "Workstreams",
  approvals: "Requests & Approvals",
  new: "New project",
  onboarding: "Onboarding",
  deployment: "Deployment",
  documentation: "Documentation",
  discovery: "Discovery & Assessment",
  strategy: "Strategy",
  "migration-mapping": "Migration Mapping",
  validation: "Validation",
  "data-engineering": "Data Engineering",
  capabilities: "Capabilities",
  copilot: "Copilot",
};

export function prettySegment(seg: string): string {
  if (segmentLabels[seg]) return segmentLabels[seg];
  if (seg.startsWith("[") && seg.endsWith("]")) return "…";
  return seg.replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function isActiveNav(item: NavItem, pathname: string): boolean {
  if (item.href === pathname) return true;
  return pathname.startsWith(`${item.href}/`);
}

const holds = (perms: string[], p?: string | string[]) => {
  if (!p) return true;
  if (perms.includes("admin:*")) return true;
  const required = Array.isArray(p) ? p : [p];
  return required.some((r) => perms.includes(r));
};

/**
 * What the viewer is, beyond their permission strings — the extra context the
 * role- and scope-aware filters need.
 *
 * Optional throughout, and every filter degrades to permission-only behaviour
 * when it is omitted: the sidebar resolves scope asynchronously, and a nav that
 * collapsed to nothing for one frame while that request landed would be a worse
 * bug than the over-permissive menu this replaces.
 */
export interface NavContext {
  role?: PlatformRole | null;
  /** True for an organization-scoped viewer — passes every scope requirement. */
  isOrgWide?: boolean;
  /** Business Units the viewer administers (not merely reads). */
  managedBusinessUnitIds?: string[];
}

function visibleTo(item: NavItem, perms: string[], ctx?: NavContext): boolean {
  /**
   * A Contributor has no working surface, because a Contributor has no job yet.
   *
   * The role is the placeholder someone carries between being onboarded and
   * being given a role by their Business Unit Admin, and every page in this
   * sidebar answers a question that presumes one: a Dashboard of the projects
   * they are on (none), a Projects list (empty), an approvals queue (nothing
   * routes to them). Four working links to four empty pages reads as a broken
   * platform rather than as a pending assignment.
   *
   * Handled here rather than as `hideForRoles: ["contributor"]` on each entry,
   * because the rule is about the ROLE holding nothing — not about these four
   * pages — so the next entry added must inherit it without anyone remembering.
   * What stays reachable is the utility rail (Agent Catalogue, Help & docs),
   * which is exactly what someone waiting to be given work can usefully read.
   */
  if (ctx?.role === "contributor") return false;

  if (!holds(perms, item.requirePermission)) return false;

  if (ctx?.role && item.hideForRoles?.includes(ctx.role)) return false;

  if (item.requireScope && ctx) {
    // Undefined (still loading) must not read as "administers nothing" — only an
    // explicitly empty list hides the entry.
    if (item.requireScope === "organization" && ctx.isOrgWide === false) return false;
    if (
      item.requireScope === "business_unit" &&
      !ctx.isOrgWide &&
      ctx.managedBusinessUnitIds?.length === 0
    ) {
      return false;
    }
  }

  return true;
}

/** Every page the viewer may open, in fixed-page-set order. */
export function visibleNav(perms: string[], ctx?: NavContext): NavItem[] {
  return mainNav.filter((i) => !i.hiddenInSidebar && visibleTo(i, perms, ctx));
}

export function visibleDeliverNav(perms: string[], ctx?: NavContext): NavItem[] {
  return deliverNav.filter((i) => visibleTo(i, perms, ctx));
}

export function visibleGovernNav(perms: string[], ctx?: NavContext): NavItem[] {
  return governNav.filter((i) => visibleTo(i, perms, ctx));
}

export function visibleObserveNav(perms: string[], ctx?: NavContext): NavItem[] {
  return observeNav.filter((i) => visibleTo(i, perms, ctx));
}

/** Legacy aliases — the sidebar migrated to the three-group form. */
export const visibleWorkspaceNav = visibleDeliverNav;
export const visibleOrgNav = visibleGovernNav;
