"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";

import { getAccessScope } from "@/lib/api/access-scope";
import { qk } from "@/lib/api/query-keys";
import { ROLE_META, type PlatformRole } from "@/lib/roles";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import type { AccessScopeOut, ScopeBinding } from "@/lib/schemas/access-scope";

import { useSession } from "./use-session";

/**
 * The viewer's access scope — WHICH Business Units and projects they may see.
 *
 * Every client surface that lists or counts scope-bearing things reads this
 * rather than filtering by hand, so a new page cannot accidentally omit the
 * boundary. Endpoints are already scoped server-side; this hook exists for the
 * things a server filter cannot do — labelling the active context, choosing the
 * dashboard shape, and deciding whether an empty list means "nothing here" or
 * "nothing you're allowed to see".
 *
 * Three states, never collapsed into two (the same mistake fixed once already
 * in `use-workspaces.ts`): a pending or failed request must never render as
 * "you have access to nothing", which reads as a false access-denied.
 */
export interface AccessScopeState {
  scope: AccessScopeOut | null;
  /** The role the viewer is acting as, for persona badges and copy. */
  role: PlatformRole | null;
  isLoading: boolean;
  isError: boolean;
  refetch: () => void;

  // ── Derived predicates — all fail CLOSED while pending or on error ─────────
  isOrgWide: boolean;
  /** Widest scope held: drives dashboard shape and the scope indicator. */
  level: "organization" | "business_unit" | "project";
  businessUnitIds: string[];
  managedBusinessUnitIds: string[];
  projectIds: string[];
  managedProjectIds: string[];
  bindings: ScopeBinding[];
  allBindings: ScopeBinding[];
  canReadBusinessUnit: (id: string | null | undefined) => boolean;
  canManageBusinessUnit: (id: string | null | undefined) => boolean;
  canReadProject: (id: string | null | undefined) => boolean;
  canManageProject: (id: string | null | undefined) => boolean;
}

export function useAccessScope(): AccessScopeState {
  const session = useSession();
  const role = effectivePlatformRole(session);

  const q = useQuery({
    queryKey: qk.session.accessScope(),
    queryFn: getAccessScope,
    enabled: session !== null,
    // Scope changes only when someone's membership changes — an uncommon,
    // explicitly-invalidated event — so a short poll would be pure noise.
    staleTime: 5 * 60_000,
  });

  const scope = q.data ?? null;
  const resolved = scope !== null;

  // Fail closed: with no resolved scope, no id is readable. The one exception
  // is an organization-scoped role, whose authority does not come from a
  // membership row — gating it on a request that has not landed yet would
  // blank the Organization Admin's own dashboard for a frame.
  const orgByRole = role !== null && ROLE_META[role].scope === "organization";
  const isOrgWide = scope?.isOrgWide ?? orgByRole;

  const has = React.useCallback(
    (ids: string[] | undefined, id: string | null | undefined) => {
      if (isOrgWide) return true;
      if (!resolved || !id) return false;
      return (ids ?? []).includes(String(id));
    },
    [isOrgWide, resolved],
  );

  return {
    scope,
    role,
    isLoading: q.isLoading,
    isError: q.isError,
    refetch: () => void q.refetch(),

    isOrgWide,
    level: scope?.level ?? (orgByRole ? "organization" : "project"),
    businessUnitIds: scope?.businessUnitIds ?? [],
    managedBusinessUnitIds: scope?.managedBusinessUnitIds ?? [],
    projectIds: scope?.projectIds ?? [],
    managedProjectIds: scope?.managedProjectIds ?? [],
    bindings: scope?.actingBindings ?? [],
    allBindings: scope?.allBindings ?? [],
    canReadBusinessUnit: (id) => has(scope?.businessUnitIds, id),
    canManageBusinessUnit: (id) => has(scope?.managedBusinessUnitIds, id),
    canReadProject: (id) => has(scope?.projectIds, id),
    canManageProject: (id) => has(scope?.managedProjectIds, id),
  };
}

/**
 * The single Business Unit a Business Unit Admin manages, when there is exactly
 * one — the case the scope indicator and the "you are managing X" copy speak
 * to. Null for an Organization Admin (no single unit), for a contributor (they
 * manage none), and for the genuinely multi-unit admin, all of which need
 * different copy rather than a misleading singular.
 */
export function useSoleManagedBusinessUnit(): ScopeBinding | null {
  const { bindings, managedBusinessUnitIds, isOrgWide } = useAccessScope();
  if (isOrgWide || managedBusinessUnitIds.length !== 1) return null;
  return (
    bindings.find(
      (b) => b.kind === "business_unit" && b.scopeId === managedBusinessUnitIds[0],
    ) ?? null
  );
}
