import { bffProxy } from "@/lib/bff/proxy";

/**
 * The permission catalogue — proxied to FastAPI `GET /admin/permissions`, which
 * reads the `permissions` table.
 *
 * THIS IS WHAT MAKES ADDING A PERMISSION A DATA CHANGE. Insert a row into
 * `permissions` and it becomes selectable on the Roles page and grantable to a
 * custom role, with no frontend deploy and no release.
 *
 * `lib/auth/permission-catalog.ts` does not go away and is not the source of truth:
 * it supplies the human LABEL and the one-line description, grouped for display. A
 * permission the database has and that file does not is shown by its raw id — usable
 * immediately, just plainer until somebody writes a label for it. A permission that
 * file has and the database does not is not offered at all, because nothing could
 * grant it.
 *
 * Wildcards are filtered out by the backend. They satisfy every permission check, so
 * putting one in a picker is offering "make this role an administrator" disguised as
 * a checkbox.
 */
export const dynamic = "force-dynamic";

export async function GET() {
  return bffProxy("/admin/permissions");
}
