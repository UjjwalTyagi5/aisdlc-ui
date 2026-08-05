"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";
import { useCanSeeProjectCost } from "@/hooks/use-can-see-project-cost";
import { useSession } from "@/hooks/use-session";
import { hasPermission } from "@/lib/auth/permissions";

/**
 * In-project navigation — PRD §32.1 ("Inside a Project").
 *
 * The PRD specifies the areas a project exposes once opened: Overview, Agents,
 * Workstreams, Orchestrator, Artifacts, Approvals/Review, Settings, Members,
 * Cost, Traces and Audit Trail. Agents are reached from the pipeline on
 * Overview rather than a tab, since which agents exist depends on the track.
 *
 * Visibility is permission-gated, matching the screen → role matrix (§35):
 * traces and audit are admin + Security Engineer only.
 *
 * NOT ADVERTISED HERE — Workstreams, Orchestrator, Approvals and Capabilities.
 * Their routes still exist and still resolve: this list decides what the strip
 * offers, not what the app can reach, so bookmarks and in-page links keep
 * working. Each has a home elsewhere that is the one people actually use —
 * Orchestrator and Approvals are top-level sidebar destinations spanning every
 * project, and an eight-tab strip made the four that matter per project harder
 * to find than the four that did not. Re-advertising one is a single line.
 */

interface ProjectTab {
  label: string;
  /** Path suffix appended to /projects/[id]; "" is the overview. */
  segment: string;
  requirePermission?: string;
  /**
   * Admins-only: this project's own admin, its Business Unit's admin, or an
   * organization-scoped role. Checked against the access scope rather than a
   * permission, because "administers THIS project" is a question about a
   * binding and no permission can express it.
   */
  adminOnly?: boolean;
}

const TABS: ProjectTab[] = [
  { label: "Overview", segment: "" },
  { label: "Members", segment: "members" },
  // Advertised, unlike Capabilities: this is now the only screen where anyone
  // uses an integration at all. The admin tiers onboard and grant and never
  // consume, so a project that could not find its own tools would have nowhere
  // else to look. Gated on connector:view, which every delivery role holds and
  // the stakeholder does not.
  // Integrations and Models are NOT tabs. They live in the sidebar's "This
  // project" section, which is where the same three nouns are named for the
  // organization — so the scoped answer sits under the scoped heading rather
  // than in a second navigation strip six pixels below the first. A tab bar
  // and a sidebar offering the same destination is one destination with two
  // addresses, and the reader has to learn which one to reach for.
  // Spend is treated as commercially sensitive rather than ambient — a
  // deliberate narrowing of PRD §34.5, which would show it to every builder.
  // The page enforces the same rule (projects/[id]/cost/page.tsx); this only
  // stops the strip advertising a door that refuses.
  { label: "Cost", segment: "cost", adminOnly: true },
  // Gated: everything on Settings is administering the project, and a
  // contributor holds none of it. The page already disables every control for
  // them, which is a read-only screen about somebody else's job — the tab is
  // the honest place to say so.
  { label: "Settings", segment: "settings", requirePermission: "project:update" },
];

export function ProjectTabs({ projectId }: { projectId: string }) {
  const pathname = usePathname();
  const session = useSession();
  // Fails closed while resolving: a tab that appears and then vanishes is
  // worse than one that arrives a beat late.
  const canSeeCost = useCanSeeProjectCost(projectId);

  const base = `/projects/${projectId}`;

  const visible = TABS.filter((t) => {
    if (t.requirePermission && !hasPermission(session, t.requirePermission)) return false;
    if (t.adminOnly && !canSeeCost) return false;
    return true;
  });

  return (
    <nav
      aria-label="Project sections"
      className="border-line-soft -mx-4 border-b px-4 md:-mx-10 md:px-10"
    >
      <ul className="scrollbar-none flex gap-1 overflow-x-auto">
        {visible.map((t) => {
          const href = t.segment ? `${base}/${t.segment}` : base;
          const active = t.segment
            ? pathname === href || pathname.startsWith(`${href}/`)
            : pathname === base;

          return (
            <li key={t.segment || "overview"} className="shrink-0">
              <Link
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "relative -mb-px inline-flex h-10 items-center border-b-2 px-3 text-[13px] font-medium transition-colors",
                  "focus-visible:ring-ring rounded-t-sm focus-visible:ring-2 focus-visible:outline-none",
                  active
                    ? "border-primary text-foreground"
                    : "hover:text-foreground border-transparent text-muted-foreground",
                )}
              >
                {t.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

export type { ProjectTab };
