import type { PlatformRole } from "@/lib/roles";
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

// ─── Deliver — the work itself ────────────────────────────────────────────────
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
    // Governance-tier roles have NO agent access at all (PRD §14.8), so a
    // prompt editor is not a scoped version of this page for them — it is a
    // page about a capability they don't hold.
    hideForRoles: ["org_admin", "bu_admin"],
    prdSection: "§34.6",
  },
];

// ─── Govern — the control plane (PRD §32.2) ───────────────────────────────────
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
    prdSection: "§36",
  },
  {
    label: "Roles & Access",
    href: "/admin/access",
    icon: ShieldCheck,
    segment: "admin",
    requirePermission: "member:manage",
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
    requirePermission: ["connector:view", "connector:manage"],
    prdSection: "§34.3, §34.4",
  },
];

// ─── Observe — the evidence surface (PRD §34.8, §34.9) ────────────────────────
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
 * Cost & Budget sits here too. It was under Govern on the argument that "what
 * did this cost against which cap" is a budget question rather than an
 * evidence one — but in practice spend is read the same way a trace is: you
 * come to it after the fact, to find out what happened. The cap-setting it
 * also carries is the exception, not the reason you open the page, and the
 * page's own header has always called itself "Observe"
 * (app/(app)/cost/page.tsx). Govern is now uniformly "who may do what";
 * Observe is uniformly "what did happen, and what did it cost".
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
  models: "Model Management",
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
