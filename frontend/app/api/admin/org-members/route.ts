import { bffProxy } from "@/lib/bff/proxy";

/**
 * The org-wide people directory — proxied to FastAPI `GET /admin/org-members`.
 *
 * Org-wide by design, not by oversight. The two-step onboarding handover needs a
 * Business Unit Admin to find someone who is not yet in their unit; a directory
 * filtered to their own unit would make the appointment they are being asked to
 * perform impossible. Reads are org-wide here, writes are unit-scoped in
 * /admin/assignments and /admin/members.
 */
export async function GET() {
  return bffProxy("/admin/org-members");
}
