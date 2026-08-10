"use client";

import * as React from "react";

import { CommandPalette } from "./command-palette";
import { OfflineBanner } from "./offline-banner";
import { Sidebar } from "./sidebar";
import { StreamSubscriber } from "./stream-subscriber";
import { TopBar } from "./top-bar";

/**
 * The enterprise app shell: fixed left sidebar (desktop), top bar,
 * scrollable content region, and the global command palette.
 * Rendered by `app/(app)/layout.tsx`.
 *
 * Elevation (Plan 06): the content column carries the .bg-mesh radial-glow
 * atmosphere + a .grain noise overlay behind the scrollable region so the
 * "Mission Control" depth lands on every page without touching individual
 * screen components. Both utilities are pointer-events:none / z-index:0 and
 * sit behind content (see globals.css).
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-dvh overflow-hidden">
      <div className="hidden lg:block">
        <Sidebar />
      </div>

      {/* Content column — atmosphere lives here, behind all content */}
      <div className="relative flex min-w-0 flex-1 flex-col">
        {/* Radial brand-glow mesh — fixed position, z-0, pointer-events:none */}
        <div
          className="bg-mesh pointer-events-none fixed inset-0 z-0"
          aria-hidden="true"
        />
        {/* Grain noise overlay — fixed, z-0, pointer-events:none, mix-blend-mode:overlay */}
        <div
          className="grain pointer-events-none fixed inset-0 z-0"
          aria-hidden="true"
        />

        <OfflineBanner />
        <TopBar />
        <main
          id="main"
          className="relative z-10 flex-1 overflow-auto"
          tabIndex={-1}
        >
          {children}
        </main>
      </div>

      <CommandPalette />
      <StreamSubscriber />
    </div>
  );
}
