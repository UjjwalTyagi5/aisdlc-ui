"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Menu } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { Separator } from "@/components/ui/separator";
import { useUiStore } from "@/stores/ui-store";
import { useSession } from "@/hooks/use-session";
import { useAccessScope } from "@/hooks/use-access-scope";
import { isActiveNav, visibleNav } from "@/lib/nav";

import { ScopeContextBar } from "./scope-indicator";
import { WorkspaceSwitcher } from "./workspace-switcher";

/**
 * Mobile navigation — triggered from the top bar on screens <1024px.
 * Desktop uses `<Sidebar />` instead.
 */
export function MobileSidebar() {
  const pathname = usePathname();
  const open = useUiStore((s) => s.mobileSidebarOpen);
  const setOpen = useUiStore((s) => s.setMobileSidebarOpen);
  const session = useSession({ required: true });
  const { role, isOrgWide, managedBusinessUnitIds, scope } = useAccessScope();

  // Same role/scope filtering as the desktop sidebar — the two menus must not
  // disagree about which modules exist, or a link hidden on desktop reappears
  // by rotating the device.
  const navItems = visibleNav(session?.permissions ?? [], {
    role,
    isOrgWide: scope === null ? undefined : isOrgWide,
    managedBusinessUnitIds: scope === null ? undefined : managedBusinessUnitIds,
  });

  // Auto-close when route changes
  React.useEffect(() => {
    setOpen(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname]);

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button variant="ghost" size="icon" className="lg:hidden" aria-label="Open navigation">
          <Menu className="size-5" aria-hidden />
        </Button>
      </SheetTrigger>
      <SheetContent side="left" className="flex w-72 flex-col p-0">
        <SheetHeader className="border-b p-3 text-left">
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <WorkspaceSwitcher />
          {/* The top bar hides the scope bar below lg, which is exactly where
              this menu lives — so it carries the context instead. */}
          <ScopeContextBar className="mt-2 flex-wrap" />
        </SheetHeader>
        <nav className="flex-1 overflow-y-auto p-2">
          <ul className="flex flex-col gap-0.5">
            {navItems.map((item) => {
              const active = isActiveNav(item, pathname);
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    className={cn(
                      "flex h-10 items-center gap-2 rounded-md px-3 text-sm font-medium transition-colors",
                      "hover:bg-accent hover:text-accent-foreground",
                      active && "bg-accent text-accent-foreground",
                    )}
                    aria-current={active ? "page" : undefined}
                  >
                    <item.icon className="size-4" aria-hidden />
                    {item.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
        <Separator />
        <div className="text-muted-foreground flex items-center justify-between p-3 font-mono text-[10px] tracking-wider uppercase">
          <span>v0.1.0</span>
          <span>Powered by PwC</span>
        </div>
      </SheetContent>
    </Sheet>
  );
}
