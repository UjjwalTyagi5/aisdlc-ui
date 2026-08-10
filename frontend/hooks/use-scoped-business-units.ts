"use client";

import * as React from "react";

import { useAccessScope } from "./use-access-scope";

export interface ScopedBusinessUnit {
  id: string;
  name: string;
  /** True when the viewer administers it (a `bu_admin` binding), not merely
   *  reads it as the parent of one of their projects. */
  managed: boolean;
}

export interface ScopedBusinessUnitsState {
  /** Every Business Unit the viewer may act in, from their bindings. */
  units: ScopedBusinessUnit[];
  isOrgWide: boolean;
  isLoading: boolean;
  isError: boolean;
}

/**
 * The Business Units a viewer is actually bound to — what scoped pages read
 * instead of "the active workspace".
 *
 * The active-workspace store answered a different question: which unit a
 * switcher had last been left on. With that switcher gone
 * ([[no-bu-switcher-in-chrome]]) there is no such thing, and there never
 * honestly was for someone bound to two units — `workspaces[0]` was an
 * arbitrary pick that silently decided which unit a page configured.
 *
 * So the answer is the SET, not one of them. Pages union their reads across it
 * and, where a write has to land somewhere specific (a credential belongs to
 * exactly one unit), ask at the point of writing rather than inheriting a
 * choice nobody made. With one unit — the common case, and always true of a
 * Business Unit Admin who runs one — that collapses to the old behaviour with
 * no extra UI.
 *
 * Names come from the bindings themselves: a unit binding carries its own
 * name, a project binding carries its parent's, so no workspace list is needed
 * and nothing has to be fetched twice.
 */
export function useScopedBusinessUnits(): ScopedBusinessUnitsState {
  const { scope, bindings, businessUnitIds, managedBusinessUnitIds, isOrgWide, isLoading, isError } =
    useAccessScope();

  const units = React.useMemo(() => {
    const names = new Map<string, string>();
    for (const b of bindings) {
      if (b.kind === "business_unit") names.set(b.scopeId, b.scopeName);
      else if (b.parentId && b.parentName) names.set(b.parentId, b.parentName);
    }
    return businessUnitIds.map((id) => ({
      id,
      name: names.get(id) ?? id,
      managed: managedBusinessUnitIds.includes(id),
    }));
  }, [bindings, businessUnitIds, managedBusinessUnitIds]);

  return {
    units,
    isOrgWide,
    // Fail closed while the scope request is in flight: an empty set rendered
    // as "you belong to nothing" is a false access-denied, so callers need the
    // pending state to be distinguishable — the same three-state rule the
    // sidebar and use-workspaces already follow.
    isLoading: isLoading || scope === null,
    isError,
  };
}
