"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

/**
 * Roles & Access — one screen, three views (PRD §33.1).
 *
 * "The Roles & Access screen is the single place where roles are defined and
 * assigned. It shows the built-in role permissions and the custom-role
 * builder, scoped organization/unit/project."
 *
 * The build had assignments and custom roles on two unconnected routes, and
 * showed the built-in role permissions nowhere. This strip presents all three
 * as the single screen the PRD specifies, without moving any existing route.
 */

/**
 * No "Assignments" tab.
 *
 * Assigning a role moved to Users, where the person is. This screen is about
 * what a role MEANS — the built-in permissions, and the custom-role builder.
 * Defining and assigning are different jobs, and keeping them on one screen
 * made you pick a scope before you could find a person.
 */
const TABS = [
  { label: "Built-in roles", href: "/admin/access/roles" },
  { label: "Custom roles", href: "/admin/roles" },
];

export function AccessTabs() {
  const pathname = usePathname();

  return (
    <nav aria-label="Roles and access views" className="border-line-soft border-b">
      <ul className="scrollbar-none flex gap-1 overflow-x-auto">
        {TABS.map((t) => {
          const active = pathname === t.href;
          return (
            <li key={t.href} className="shrink-0">
              <Link
                href={t.href}
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

/** Shared page heading so all three views read as one screen. */
export function AccessHeader({ description }: { description: string }) {
  return (
    <header className="flex flex-col items-start gap-1">
      <div className="text-brand-bright mb-2.5 flex items-center gap-2 font-mono text-[11px] tracking-[0.14em] uppercase">
        <span className="bg-brand-bright inline-block h-px w-5" aria-hidden />
        Govern
      </div>
      <h1 className="font-display text-[32px] leading-[1.04] font-bold tracking-[-0.03em]">
        Roles &amp; Access
      </h1>
      <p className="text-muted-foreground mt-1.5 max-w-[620px] text-[14px]">
        {description}
      </p>
    </header>
  );
}
