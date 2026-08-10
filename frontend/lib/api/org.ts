/**
 * Organization rollup API client. Calls the same-origin BFF (`/api/org/*`).
 */
import { api } from "./client";
import { OrgOverview } from "@/lib/schemas/org-overview";

export const getOrgOverview = () => api("/org/overview", { schema: OrgOverview });
