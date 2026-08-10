import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { RestrictedAccess } from "@/components/auth/restricted-access";
import { getSession } from "@/lib/auth/session";
import { hasPermission } from "@/lib/auth/permissions";

export const metadata: Metadata = { title: "Activity" };

/**
 * Activity — the single sidebar entry for the two evidence surfaces
 * (PRD §34.8, §34.9). Traces and the Audit Trail both answer "what happened",
 * so they are one destination with two tabs rather than two nav items.
 *
 * They keep separate permissions, though, and the split is real: a Business
 * Unit Admin holds `audit:view` and not `trace:view`, a Project Admin the
 * reverse. So this route owns no UI of its own — it lands each viewer on the
 * tab they can actually open. Redirecting on the server means no flash of a
 * page the viewer is about to be bounced off.
 */
export default async function ActivityPage() {
  const session = await getSession();

  if (hasPermission(session, "trace:view")) redirect("/traces");
  if (hasPermission(session, "audit:view")) redirect("/audit");

  return (
    <RestrictedAccess description="Activity requires the trace:view or audit:view permission. Ask your admin for access." />
  );
}
