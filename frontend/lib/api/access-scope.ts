/**
 * Access-scope API client — the viewer's Business Unit / project boundary.
 * Calls the same-origin BFF (`/api/auth/access-scope`).
 */
import { AccessScopeOut } from "@/lib/schemas/access-scope";

import { api } from "./client";

export const getAccessScope = () =>
  api("/auth/access-scope", { schema: AccessScopeOut });
