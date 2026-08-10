"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";

import type { SseState } from "@/lib/stream/use-sse";

/**
 * Ephemeral UI state. Everything here is client-only, non-authoritative,
 * and safe to reset. Server-authoritative state lives in TanStack Query (Chunk 5).
 */

interface UiState {
  sidebarCollapsed: boolean;
  toggleSidebar: () => void;
  setSidebarCollapsed: (v: boolean) => void;

  mobileSidebarOpen: boolean;
  setMobileSidebarOpen: (v: boolean) => void;

  commandPaletteOpen: boolean;
  openCommandPalette: () => void;
  closeCommandPalette: () => void;
  setCommandPaletteOpen: (v: boolean) => void;

  /** Workspace SSE state — driven by `<StreamSubscriber />`. */
  connectionState: SseState;
  setConnectionState: (s: SseState) => void;

  /** Unread notification count — incremented by SSE `hitl.pending` + friends. */
  unreadCount: number;
  bumpUnread: () => void;
  resetUnread: () => void;
}

export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      sidebarCollapsed: false,
      toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
      setSidebarCollapsed: (v) => set({ sidebarCollapsed: v }),

      mobileSidebarOpen: false,
      setMobileSidebarOpen: (v) => set({ mobileSidebarOpen: v }),

      commandPaletteOpen: false,
      openCommandPalette: () => set({ commandPaletteOpen: true }),
      closeCommandPalette: () => set({ commandPaletteOpen: false }),
      setCommandPaletteOpen: (v) => set({ commandPaletteOpen: v }),

      connectionState: "idle",
      setConnectionState: (s) => set({ connectionState: s }),

      unreadCount: 0,
      bumpUnread: () => set((s) => ({ unreadCount: s.unreadCount + 1 })),
      resetUnread: () => set({ unreadCount: 0 }),
    }),
    {
      name: "sdlc.ui",
      // Only persist user-chosen layout preferences — not ephemeral modal/stream state
      partialize: (s) => ({ sidebarCollapsed: s.sidebarCollapsed }),
    },
  ),
);
