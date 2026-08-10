"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Building2, LogOut, Users } from "lucide-react";

import { PwcMark } from "@/components/brand/pwc-mark";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface NavLink {
  label: string;
  href: string;
  icon: React.ComponentType<{ className?: string }>;
}

const NAV: NavLink[] = [
  { label: "Organizations", href: "/platform", icon: Building2 },
  { label: "Platform users", href: "/platform/users", icon: Users },
];

/**
 * Shell for the platform-admin (Application Team) console. Gives the
 * `/platform/*` pages the same elevation language as the org app: a sticky
 * blurred control bar with the PwC brand lockup, section nav, theme toggle,
 * and an ambient brand-glow mesh behind a generous, wide content column.
 */
export function PlatformShell({
  userName,
  children,
}: {
  userName: string;
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const isActive = (href: string) =>
    href === "/platform" ? pathname === "/platform" : pathname.startsWith(href);

  return (
    <div className="bg-background relative flex min-h-dvh flex-col">
      <div className="bg-mesh pointer-events-none fixed inset-0 z-0" aria-hidden />

      <header className="border-line-soft bg-panel-elevated/70 supports-[backdrop-filter]:bg-panel-elevated/50 sticky top-0 z-30 border-b backdrop-blur-md">
        <div className="flex h-16 w-full items-center gap-6 px-6 md:px-10 lg:px-16">
          <Link href="/platform" className="flex items-center gap-3" aria-label="Platform Console">
            <PwcMark size={38} />
            <div className="hidden flex-col leading-tight sm:flex">
              <span className="font-display text-sm font-bold tracking-tight">
                Platform Console
              </span>
              <span className="text-brand-bright font-mono text-[10px] font-semibold tracking-[0.14em] uppercase">
                Application Team
              </span>
            </div>
          </Link>

          <nav className="ml-2 hidden items-center gap-1 md:flex" aria-label="Platform sections">
            {NAV.map((item) => {
              const active = isActive(item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={cn(
                    "flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                    active
                      ? "bg-secondary text-foreground"
                      : "text-muted-foreground hover:text-foreground hover:bg-secondary/60",
                  )}
                  aria-current={active ? "page" : undefined}
                >
                  <item.icon className="size-4" />
                  {item.label}
                </Link>
              );
            })}
          </nav>

          <div className="ml-auto flex items-center gap-2">
            <span className="text-muted-foreground hidden text-xs lg:inline">{userName}</span>
            <ThemeToggle />
            <form action="/api/auth/logout" method="POST">
              <Button variant="ghost" size="sm" type="submit" className="gap-2">
                <LogOut className="size-4" aria-hidden />
                <span className="hidden sm:inline">Sign out</span>
              </Button>
            </form>
          </div>
        </div>

        {/* Mobile section nav */}
        <nav
          className="border-line-soft flex items-center gap-1 border-t px-4 py-2 md:hidden"
          aria-label="Platform sections"
        >
          {NAV.map((item) => {
            const active = isActive(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium",
                  active ? "bg-secondary text-foreground" : "text-muted-foreground",
                )}
              >
                <item.icon className="size-4" />
                {item.label}
              </Link>
            );
          })}
        </nav>
      </header>

      <main className="relative z-10 w-full flex-1 px-6 py-10 md:px-10 lg:px-16">{children}</main>
    </div>
  );
}
