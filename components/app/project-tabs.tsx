"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";
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
 */

interface ProjectTab {
  label: string;
  /** Path suffix appended to /projects/[id]; "" is the overview. */
  segment: string;
  requirePermission?: string;
}

const TABS: ProjectTab[] = [
  { label: "Overview", segment: "" },
  { label: "Workstreams", segment: "workstreams" },
  { label: "Orchestrator", segment: "orchestrator" },
  { label: "Approvals", segment: "approvals", requirePermission: "artifact:view" },
  { label: "Members", segment: "members" },
  { label: "Cost", segment: "cost", requirePermission: "cost:view" },
  { label: "Capabilities", segment: "capabilities" },
  { label: "Settings", segment: "settings" },
];

export function ProjectTabs({ projectId }: { projectId: string }) {
  const pathname = usePathname();
  const session = useSession();

  const base = `/projects/${projectId}`;

  const visible = TABS.filter(
    (t) => !t.requirePermission || hasPermission(session, t.requirePermission),
  );

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
