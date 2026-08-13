import { bffProxy } from "@/lib/bff/proxy";

/**
 * The notification bell — proxied to FastAPI `GET /notifications` and
 * `POST /notifications/read`.
 *
 * Previously returned an empty list with a comment saying the backend owed it one.
 * It does now: a `notifications` table written by the governance-request lifecycle,
 * so "your request was approved" has somewhere to be delivered to.
 *
 * ADDRESSED, NEVER BROADCAST — the scoping note that used to live here is now a
 * CHECK constraint. Every row names a PERSON or a ROLE, and the listing intersects
 * both against the caller. The two exist because they answer different questions:
 * "your request was approved" belongs to one identity and follows them, while "a
 * request is waiting on the Business Unit Admin" belongs to whoever holds that role
 * right now — including someone appointed after it was raised, who would never see a
 * notification addressed to their predecessor.
 */
export const dynamic = "force-dynamic";

export async function GET() {
  return bffProxy("/notifications");
}

export async function POST() {
  return bffProxy("/notifications/read", { method: "POST" });
}
