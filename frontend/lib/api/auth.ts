/**
 * Server-only auth-bootstrap BFF call (REQ-M7-12 frontend half / milestone-7.2
 * plan 05).
 *
 * TRANSPORT NOTE — despite living under `lib/api/` alongside browser-facing
 * modules (runs.ts, artifacts.ts, ...) that use `api()` from `lib/api/client`,
 * THIS module deliberately uses the SERVER-ONLY `bffFetch` transport
 * (`lib/bff/client.ts` + `mintBffToken`). It is called exclusively from the
 * server-only `getSession()` (lib/auth/session.ts) during session
 * construction — the browser `api()` client resolves against
 * `NEXT_PUBLIC_API_BASE` and would be wrong here, defeating the BFF boundary
 * (server mints the backend JWT; the browser never holds it, T-M4-07).
 */
import { z } from "zod";

import type { Session } from "@/lib/auth/types";
import { bffFetch } from "@/lib/bff/client";

const PermissionsResponse = z.object({
  permissions: z.array(z.string()),
});

/**
 * Calls the backend `GET /auth/permissions` (public()-marked bootstrap
 * endpoint, identity derived from the verified-JWT middleware) and returns
 * the caller's DB-resolved effective permission strings.
 *
 * Fails CLOSED to `[]` on ANY error — non-2xx, malformed body, or network
 * failure — so a backend hiccup never silently grants phantom access
 * (T-7.2-26: the resolver itself is fail-closed; this mirrors that contract
 * at the transport boundary).
 *
 * @param session - The BASE session (pre-enrichment; its `permissions` may
 *   legitimately be `[]` at this point — see the IDENTITY-TOKEN SEAM note in
 *   `getSession()`). Reused directly; no synthetic/placeholder Session and no
 *   new minter — `mintBffToken` derives `sub`/`tenant_id` from it as usual.
 */
export async function fetchPermissions(session: Session): Promise<string[]> {
  try {
    const json = await bffFetch("/auth/permissions", {
      session,
      schema: PermissionsResponse,
    });
    return json.permissions;
  } catch (err) {
    console.error(
      "[fetchPermissions] failed to resolve permissions — failing closed to []",
      err,
    );
    return [];
  }
}
