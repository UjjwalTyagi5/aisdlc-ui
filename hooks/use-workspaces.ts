"use client";

import { useQuery } from "@tanstack/react-query";

import { listWorkspaces } from "@/lib/api/workspaces";
import { qk } from "@/lib/api/query-keys";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type { Workspace } from "@/lib/schemas";

/** All workspaces visible to the current user (org-wide for now). */
export function useWorkspaces() {
  return useQuery({
    queryKey: qk.workspaces.list(),
    queryFn: listWorkspaces,
    staleTime: 60_000,
  });
}

export interface ActiveWorkspace {
  active: Workspace | null;
  workspaces: Workspace[];
  isLoading: boolean;
  /** True when the workspaces query failed — distinct from "loaded, zero access". */
  isError: boolean;
  refetch: () => void;
  setActive: (id: string) => void;
}

/**
 * Resolves the active workspace: the persisted choice if still valid, else the
 * first active workspace. Archived workspaces are excluded from the switchable set.
 */
export function useActiveWorkspace(): ActiveWorkspace {
  const q = useWorkspaces();
  const activeId = useWorkspaceStore((s) => s.activeWorkspaceId);
  const setActive = useWorkspaceStore((s) => s.setActiveWorkspace);

  const workspaces = (q.data ?? []).filter((w) => w.status === "active");
  const active =
    workspaces.find((w) => w.id === activeId) ?? workspaces[0] ?? null;

  return {
    active,
    workspaces,
    isLoading: q.isLoading,
    isError: q.isError,
    refetch: () => void q.refetch(),
    setActive,
  };
}
