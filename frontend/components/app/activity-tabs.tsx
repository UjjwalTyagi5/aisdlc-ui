"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";
import { useSession } from "@/hooks/use-session";
import { hasPermission } from "@/lib/auth/permissions";

/**
 * Activity — one screen, two views (PRD §34.8, §34.9).
 *
 * Traces are the machine's account of a run; the Audit Trail is the
 * governance record around it. Same question, two altitudes — so they share a
 * surface rather than competing for two sidebar slots.
 *
 * A tab the viewer cannot open is not rendered: a Business Unit Admin holds
 * `audit:view` without `trace:view`, and showing them a tab that only leads to
 * a restricted-access page would be worse than showing nothing. When only one
 * tab survives, the strip hides entirely — a single tab is just a heading.
 */
const TABS = [
  { label: "Traces", href: "/traces", permission: "trace:view" },
  { label: "Audit Trail", href: "/audit", permission: "audit:view" },
] as const;

export function ActivityTabs() {
  const pathname = usePathname();
  const session = useSession();

  const visible = TABS.filter((t) => hasPermission(session, t.permission));
  if (visible.length < 2) return null;

  return (
    <nav aria-label="Activity views" className="border-line-soft border-b">
      <ul className="scrollbar-none flex gap-1 overflow-x-auto">
        {visible.map((t) => {
          const active = pathname === t.href || pathname.startsWith(`${t.href}/`);
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
