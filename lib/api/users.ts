/**
 * User-detail API client — the Users page's click-through view.
 * Calls the same-origin BFF (`/api/admin/users/:id`).
 */
import { UserDetail } from "@/lib/schemas/user-directory";

import { api } from "./client";

export const getUserDetail = (id: string) =>
  api(`/admin/users/${encodeURIComponent(id)}`, { schema: UserDetail });
