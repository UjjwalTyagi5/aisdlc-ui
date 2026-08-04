"use client";

import * as React from "react";

import { ThemeToggle } from "@/components/theme-toggle";
import { Separator } from "@/components/ui/separator";

import { Breadcrumbs } from "./breadcrumbs";
import { GlobalSearchTrigger } from "./global-search-trigger";
import { MobileSidebar } from "./mobile-sidebar";
import { NotificationsBell } from "./notifications-bell";
import { UserMenu } from "./user-menu";

/**
 * Sticky blurred control bar — "Mission Control" elevation (Plan 06).
 *
 * Elevation changes (restyle-only; all props/controls preserved):
 * - Border uses --line-soft (warm-tinted hairline) instead of raw border-b
 * - Background uses panel-elevated surface at 60% opacity for depth layering
 * - The search trigger slot receives a mono ⌘K kbd hint via its existing
 *   className prop — the hint renders inside GlobalSearchTrigger itself
 * - z-index bumped to z-30 (above mesh/grain z-0) so the bar stays crisp
 */
export function TopBar() {
  return (
    <header
      className="sticky top-0 z-30 flex h-14 shrink-0 items-center gap-3 border-b border-line-soft bg-panel-elevated/60 px-3 backdrop-blur-md supports-[backdrop-filter]:bg-panel-elevated/40 lg:px-4"
      aria-label="Page header"
    >
      <MobileSidebar />
      <div className="hidden min-w-0 flex-1 items-center gap-3 sm:flex">
        <Breadcrumbs />
      </div>
      <div className="ml-auto flex items-center gap-1 sm:gap-2">
        {/* No scope or role chip here.

            This bar used to carry both, which — with the sidebar's scope chip
            and the chip each page states above its own numbers — put the same
            two facts on screen up to four times at once, and the role three
            times on the dashboard alone. The boundary still has to be stated,
            so it is: once per page, next to the figures it qualifies. The role
            you are acting as lives in the account menu to the right, which is
            where people look to check it. */}
        <GlobalSearchTrigger className="hidden md:flex" />
        <Separator orientation="vertical" className="mx-1 hidden h-6 md:block" />
        <NotificationsBell />
        <ThemeToggle />
        <UserMenu />
      </div>
    </header>
  );
}
