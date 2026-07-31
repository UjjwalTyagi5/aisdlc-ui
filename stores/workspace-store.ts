"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";

/**
 * Active-workspace context. The workspace is the business-unit boundary the user
 * is currently acting in; scoped surfaces (projects, runs, cost, members…) read
 * it. Persisted so the choice survives reloads. (URL `/w/{id}` scoping can be
 * layered on later — additive; this store stays the source of the active id.)
 */
interface WorkspaceState {
  activeWorkspaceId: string | null;
  setActiveWorkspace: (id: string) => void;
}

/** Mirror the active workspace into a cookie so the server-side BFF can forward
 * it to FastAPI as X-Workspace-Id (the store itself is browser-only). */
function writeWorkspaceCookie(id: string | null) {
  if (typeof document === "undefined") return;
  if (id) {
    document.cookie = `sdlc_active_workspace=${id}; path=/; max-age=31536000; samesite=lax`;
  } else {
    document.cookie = "sdlc_active_workspace=; path=/; max-age=0; samesite=lax";
  }
}

export const useWorkspaceStore = create<WorkspaceState>()(
  persist(
    (set) => ({
      activeWorkspaceId: null,
      setActiveWorkspace: (id) => {
        writeWorkspaceCookie(id);
        set({ activeWorkspaceId: id });
      },
    }),
    {
      name: "sdlc.workspace",
      // Re-assert the cookie on rehydrate so a returning tab keeps scoping correctly.
      onRehydrateStorage: () => (state) => {
        if (state?.activeWorkspaceId) writeWorkspaceCookie(state.activeWorkspaceId);
      },
    },
  ),
);
