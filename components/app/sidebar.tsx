"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Boxes,
  HelpCircle,
  Lock,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Separator } from "@/components/ui/separator";
import { useUiStore } from "@/stores/ui-store";
import { useSession } from "@/hooks/use-session";
import {
  isActiveNav,
  openProjectId,
  projectNavFor,
  visibleDeliverNav,
  visibleGovernNav,
  visibleObserveNav,
  type NavItem,
} from "@/lib/nav";
import { BUSINESS_UNIT_LABEL_PLURAL } from "@/lib/scope";
import { useAccessScope } from "@/hooks/use-access-scope";

import { OrgHeader } from "./org-header";

const APP_VERSION = "0.1.0";

/*
 * The 13-agent status rail that used to live here is gone (2026-08-03).
 *
 * It was rendered without an `agentStatuses` prop, so every dot resolved to
 * "idle" permanently — the status it existed to show never once appeared. Its
 * rows were `<li>`s with no link, so they navigated nowhere. That left one real
 * affordance, a hover tooltip carrying the agent's name and one-line
 * description, in exchange for thirteen rows that pushed Govern and Observe up
 * the sidebar.
 *
 * Both halves now have better homes. Discovery belongs to the Agent Catalogue
 * (/catalogue, linked in the footer below), which is searchable and cross-
 * references each agent by track, owning role and capability class. Status
 * belongs to the project pipeline on a project's Overview, which is real,
 * per-run, and scoped to that project's track — something a global rail could
 * not do, as its own comment conceded while listing all 13 including the 5 a
 * Track-1 project never runs.
 */

// ─── Nav item ─────────────────────────────────────────────────────────────────

function NavLink({
  item,
  pathname,
  collapsed,
  locked,
}: {
  item: NavItem;
  pathname: string;
  collapsed: boolean;
  locked?: boolean;
}) {
  const active = !locked && isActiveNav(item, pathname);

  const inner = (
    <span
      className={cn(
        "relative flex h-9 w-full items-center gap-2 rounded-md px-2 text-sm font-medium transition-colors",
        locked
          ? "cursor-default text-muted-foreground/40"
          : [
              "hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
              "focus-visible:ring-sidebar-ring focus-visible:ring-offset-sidebar focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none",
              active
                ? "bg-sidebar-accent text-sidebar-accent-foreground"
                : "text-muted-foreground",
            ],
        collapsed && "justify-center px-0",
      )}
      aria-current={active ? "page" : undefined}
    >
      {active && !collapsed && (
        <span
          className="bg-brand-gradient absolute top-[8px] bottom-[8px] -left-2 w-[3px] rounded-r-sm"
          style={{ boxShadow: "0 0 12px var(--brand-bright)" }}
          aria-hidden="true"
        />
      )}
      {locked ? (
        <Lock className="size-4 shrink-0 opacity-40" aria-hidden />
      ) : (
        <item.icon className="size-4 shrink-0" aria-hidden />
      )}
      {!collapsed && <span className="truncate">{item.label}</span>}
    </span>
  );

  const el = locked ? (
    <span className="block">{inner}</span>
  ) : (
    <Link href={item.href} className="block">
      {inner}
    </Link>
  );

  if (collapsed) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>{el}</TooltipTrigger>
        <TooltipContent side="right">
          {locked
            ? `${item.label} — no ${BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()} access`
            : item.label}
        </TooltipContent>
      </Tooltip>
    );
  }
  return el;
}

// ─── Section label ────────────────────────────────────────────────────────────

function SectionLabel({
  label,
  collapsed,
  dimmed,
}: {
  label: string;
  collapsed: boolean;
  dimmed?: boolean;
}) {
  if (collapsed) {
    return (
      <div
        className="border-line-soft mx-2 my-1.5 border-t"
        role="separator"
        aria-hidden="true"
      />
    );
  }
  return (
    <div
      className={cn(
        "font-display mb-0.5 mt-2 px-2 text-[10px] font-semibold tracking-widest uppercase",
        dimmed ? "text-muted-foreground/40" : "text-muted-foreground",
      )}
    >
      {label}
    </div>
  );
}

// ─── No-workspace state ───────────────────────────────────────────────────────

function NoWorkspaceSection({ collapsed }: { collapsed: boolean }) {
  const router = useRouter();

  if (collapsed) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <div className="mx-2 my-1.5 flex cursor-default items-center justify-center">
            <Lock className="text-muted-foreground/30 size-3.5" aria-hidden />
          </div>
        </TooltipTrigger>
        <TooltipContent side="right">
          No {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()} access
        </TooltipContent>
      </Tooltip>
    );
  }

  return (
    <div className="border-line-soft/50 mx-2 rounded-lg border border-dashed px-3 py-2.5">
      <p className="text-muted-foreground/60 font-mono text-[10.5px] font-medium leading-snug">
        No {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()} access.
        <br />
        Ask an admin to add you.
      </p>
      <button
        onClick={() => router.push("/workspaces")}
        className="text-brand-bright mt-1.5 font-mono text-[10.5px] underline underline-offset-2 opacity-80 transition-opacity hover:opacity-100"
      >
        View {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()} →
      </button>
    </div>
  );
}

/** Distinct from NoWorkspaceSection: the request failed, not "loaded, zero access". */
function WorkspaceLoadErrorSection({
  collapsed,
  onRetry,
}: {
  collapsed: boolean;
  onRetry: () => void;
}) {
  if (collapsed) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            onClick={onRetry}
            className="mx-2 my-1.5 flex items-center justify-center"
            aria-label={`Retry loading ${BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}`}
          >
            <Lock className="text-destructive/50 size-3.5" aria-hidden />
          </button>
        </TooltipTrigger>
        <TooltipContent side="right">
          Couldn&apos;t load {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()} — click to retry
        </TooltipContent>
      </Tooltip>
    );
  }

  return (
    <div className="border-destructive/30 mx-2 rounded-lg border border-dashed px-3 py-2.5">
      <p className="text-muted-foreground/60 font-mono text-[10.5px] font-medium leading-snug">
        Couldn&apos;t load {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}.
        <br />
        Check your connection and try again.
      </p>
      <button
        onClick={onRetry}
        className="text-brand-bright mt-1.5 font-mono text-[10.5px] underline underline-offset-2 opacity-80 transition-opacity hover:opacity-100"
      >
        Retry →
      </button>
    </div>
  );
}

// ─── Sidebar ─────────────────────────────────────────────────────────────────

export function Sidebar() {
  const pathname = usePathname();
  const collapsed = useUiStore((s) => s.sidebarCollapsed);
  const toggle = useUiStore((s) => s.toggleSidebar);
  const session = useSession({ required: true });

  const {
    role,
    isOrgWide,
    businessUnitIds,
    managedBusinessUnitIds,
    scope,
    isLoading: scopeLoading,
    isError: scopeError,
    refetch: refetchScope,
  } = useAccessScope();

  const perms = session?.permissions ?? [];
  // The PRD's fixed page set, in three presentational groups (§15.1, §32.1).
  // Every role sees a scoped version of this same set — never a different one.
  //
  // The nav context adds the two things a permission string can't express:
  // which role is acting (Agent Studio is meaningless to the governance tier)
  // and whether they administer any Business Unit (Business Units is not an
  // admin page for someone who administers none). Passing `undefined` for the
  // scope fields until the query resolves keeps the menu stable rather than
  // briefly collapsing — see NavContext.
  const navCtx = {
    role,
    isOrgWide: scope === null ? undefined : isOrgWide,
    managedBusinessUnitIds: scope === null ? undefined : managedBusinessUnitIds,
  };
  const deliverItems = visibleDeliverNav(perms, navCtx);
  const governItems = visibleGovernNav(perms, navCtx);
  const observeItems = visibleObserveNav(perms, navCtx);

  /**
   * The open project's own entries. Not permission-filtered here: each target
   * gates itself, and the two that matter to a contributor (their credentials,
   * their models) are exactly the ones they may open. Gating the LINK on a
   * manage permission would hide the read-only view they are entitled to.
   */
  const openProject = openProjectId(pathname);
  const projectItems = openProject ? projectNavFor(openProject) : [];

  // Three distinct states, not two: a slow/failed request must never read as
  // "loaded, you have zero access" — that's a false access-denied message.
  //
  // Whether the Deliver links work is a question about the viewer's BINDINGS,
  // which is what the access scope answers. It used to be asked of the
  // workspace list via the switcher's active unit; with no switcher there is no
  // active unit, and "do I belong to any unit" was always the real question.
  const wsPending = scopeLoading;
  const wsFailed = !scopeLoading && scopeError;
  const hasWorkspace = !scopeLoading && !scopeError && (isOrgWide || businessUnitIds.length > 0);

  return (
    <aside
      data-collapsed={collapsed}
      className={cn(
        "border-line-soft flex h-dvh shrink-0 flex-col border-r transition-[width] duration-200 ease-out",
        "from-surface-2 to-sidebar text-sidebar-foreground bg-gradient-to-b",
        collapsed ? "w-16" : "w-60",
      )}
      aria-label="Primary"
    >
      {/* Organization identity + Business Unit actions — never a unit name */}
      <div className="border-line-soft border-b p-2">
        <OrgHeader collapsed={collapsed} />
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto p-2" aria-label="Main navigation">

        {/* ── Deliver — the work itself ─────────────────────────────────── */}
        {/* Named for what the group IS, not for a Business Unit. This label
            used to carry the active unit's name, which was a lie for anyone
            bound to more than one — see components/app/org-header.tsx. */}
        <SectionLabel
          label="Deliver"
          collapsed={collapsed}
          dimmed={!hasWorkspace && !wsPending}
        />

        {/* No scope chip here.

            It said the same word as the chip each page prints above its own
            figures, so "ORGANIZATION" appeared twice on every screen — and the
            nav is the wrong place for it: these links go to the same routes
            whoever you are, so labelling the MENU with a boundary explains
            nothing. The page states it where it changes the reading, next to
            the numbers it bounds. */}

        {hasWorkspace || wsPending ? (
          // Deliver hrefs are static (lib/nav.ts) and don't depend on the
          // workspace query — keep them real links while pending so there is
          // no dead-nav window between sign-in and the query resolving.
          <ul className="flex flex-col gap-0.5">
            {deliverItems.map((item) => (
              <li key={item.href}>
                <NavLink item={item} pathname={pathname} collapsed={collapsed} />
              </li>
            ))}
          </ul>
        ) : wsFailed ? (
          <>
            <ul className="mb-1 flex flex-col gap-0.5">
              {deliverItems.map((item) => (
                <li key={item.href}>
                  <NavLink
                    item={item}
                    pathname={pathname}
                    collapsed={collapsed}
                    locked
                  />
                </li>
              ))}
            </ul>
            <WorkspaceLoadErrorSection collapsed={collapsed} onRetry={refetchScope} />
          </>
        ) : (
          <>
            {/* Locked when the person belongs to no business unit yet. */}
            <ul className="mb-1 flex flex-col gap-0.5">
              {deliverItems.map((item) => (
                <li key={item.href}>
                  <NavLink
                    item={item}
                    pathname={pathname}
                    collapsed={collapsed}
                    locked
                  />
                </li>
              ))}
            </ul>
            <NoWorkspaceSection collapsed={collapsed} />
          </>
        )}

        {/* ── This project — only while one is open ─────────────────────
            The three nouns the sidebar already names, answered for the
            project you are standing in rather than for the organization.
            Absent on /projects and everywhere else, because there is no
            project to scope them to. */}
        {projectItems.length > 0 && (
          <>
            <SectionLabel label="This project" collapsed={collapsed} />
            <ul className="flex flex-col gap-0.5">
              {projectItems.map((item) => (
                <li key={item.href}>
                  <NavLink
                    item={{
                      label: item.label,
                      href: item.href,
                      icon: item.icon,
                      segment: item.segment,
                    }}
                    pathname={pathname}
                    collapsed={collapsed}
                  />
                </li>
              ))}
            </ul>
          </>
        )}

        {/* ── Govern — the control plane (PRD §32.2) ────────────────────── */}
        {governItems.length > 0 && (
          <>
            <SectionLabel label="Govern" collapsed={collapsed} />
            <ul className="flex flex-col gap-0.5">
              {governItems.map((item) => (
                <li key={item.href + item.label}>
                  <NavLink item={item} pathname={pathname} collapsed={collapsed} />
                </li>
              ))}
            </ul>
          </>
        )}

        {/* ── Observe — traces & audit (PRD §34.8, §34.9) ───────────────── */}
        {observeItems.length > 0 && (
          <>
            <SectionLabel label="Observe" collapsed={collapsed} />
            <ul className="flex flex-col gap-0.5">
              {observeItems.map((item) => (
                <li key={item.href + item.label}>
                  <NavLink item={item} pathname={pathname} collapsed={collapsed} />
                </li>
              ))}
            </ul>
          </>
        )}
      </nav>

      <Separator className="bg-line-soft" />

      {/* Footer */}
      <div className="flex flex-col gap-0.5 p-2">
        {/* Reference material, above the help links it belongs with. Ungated
            deliberately — see its entry in lib/nav.ts::utilityNav. */}
        <SidebarFooterLink
          href="/catalogue"
          label="Agent Catalogue"
          icon={Boxes}
          collapsed={collapsed}
        />
        <SidebarFooterLink href="/help" label="Help & docs" icon={HelpCircle} collapsed={collapsed} />
        <SidebarFooterLink href="/feedback" label="Send feedback" icon={MessageSquare} collapsed={collapsed} />
        <Button
          variant="ghost"
          size="sm"
          onClick={toggle}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-keyshortcuts="Control+B"
          className={cn(
            "text-muted-foreground mt-1 h-8 justify-start gap-2",
            collapsed && "justify-center",
          )}
        >
          {collapsed ? (
            <PanelLeftOpen className="size-4" aria-hidden />
          ) : (
            <PanelLeftClose className="size-4" aria-hidden />
          )}
          {!collapsed && <span className="text-xs">Collapse</span>}
        </Button>
        {!collapsed && (
          <div className="text-muted-foreground mt-1 flex items-center justify-between px-2 font-mono text-[10px] tracking-wider uppercase">
            <span>v{APP_VERSION}</span>
            <span>Powered by PwC</span>
          </div>
        )}
      </div>
    </aside>
  );
}

function SidebarFooterLink({
  href,
  label,
  icon: Icon,
  collapsed,
}: {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  collapsed: boolean;
}) {
  const link = (
    <Link
      href={href}
      className={cn(
        "text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground flex h-8 items-center gap-2 rounded-md px-2 text-xs transition-colors",
        collapsed && "justify-center px-0",
      )}
    >
      <Icon className="size-4 shrink-0" aria-hidden />
      {!collapsed && <span className="truncate">{label}</span>}
    </Link>
  );
  if (!collapsed) return link;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{link}</TooltipTrigger>
      <TooltipContent side="right">{label}</TooltipContent>
    </Tooltip>
  );
}
