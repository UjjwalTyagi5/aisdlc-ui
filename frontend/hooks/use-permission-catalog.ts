"use client";

import { useQuery } from "@tanstack/react-query";
import { z } from "zod";

import { api } from "@/lib/api/client";
import { PERMISSION_CATALOG, type PermGroup } from "@/lib/auth/permission-catalog";

/**
 * The grantable permissions, as the DATABASE has them — grouped and labelled for
 * display by the static catalogue.
 *
 * TWO SOURCES, ONE AUTHORITY. `permissions` in Postgres decides what EXISTS; adding
 * a row there makes a permission selectable here with no frontend deploy, which is
 * the point of serving it. `lib/auth/permission-catalog.ts` decides how each one
 * READS — its group, its label, its one-line description — because none of that is
 * worth a database column and all of it is worth reviewing in a diff.
 *
 * The two can disagree in both directions and each disagreement resolves the safe
 * way:
 *   in the DB, not in the catalogue   shown under "Other", labelled by its raw id.
 *                                     Usable immediately, plainer until someone
 *                                     writes a label.
 *   in the catalogue, not in the DB   not shown at all. Nothing could grant it, so
 *                                     offering the checkbox would be offering a
 *                                     permission that silently does nothing.
 *
 * Falls back to the static catalogue while loading or if the request fails, so the
 * panel renders something usable rather than an empty permission list that reads as
 * "this role grants nothing".
 */

const PermissionRow = z.object({ id: z.string() });

export const listPermissionCatalog = () =>
  api("/admin/permissions", { schema: z.array(PermissionRow) });

/** Group the DB's ids using the static catalogue's grouping and labels. */
export function groupPermissions(ids: string[]): PermGroup[] {
  const known = new Map(
    PERMISSION_CATALOG.flatMap((g) => g.perms.map((p) => [p.id, { group: g.group, perm: p }])),
  );

  const groups: PermGroup[] = PERMISSION_CATALOG.map((g) => ({
    group: g.group,
    perms: g.perms.filter((p) => ids.includes(p.id)),
  })).filter((g) => g.perms.length > 0);

  const unlabelled = ids.filter((id) => !known.has(id)).sort();
  if (unlabelled.length > 0) {
    groups.push({
      group: "Other",
      perms: unlabelled.map((id) => ({
        id,
        label: id,
        grants: "Added to the permission catalogue but not yet described here.",
      })),
    });
  }
  return groups;
}

export function usePermissionCatalog(): PermGroup[] {
  const { data } = useQuery({
    queryKey: ["admin", "permissions"],
    queryFn: listPermissionCatalog,
    // The catalogue changes when someone adds a permission, which is rare and
    // deliberate — not something to re-fetch on every focus.
    staleTime: 5 * 60_000,
  });

  if (!data) return PERMISSION_CATALOG;
  return groupPermissions(data.map((p) => p.id));
}
