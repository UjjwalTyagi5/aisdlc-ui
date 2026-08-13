import { notImplemented } from "@/lib/bff/not-implemented";

/**
 * Propose a change to an Agent Studio default you don't own — instead of
 * publishing it, raise it with the tier's owner.
 *
 * NOT IMPLEMENTED BY THE BACKEND. It has two halves and neither exists: the
 * governance request it files (see app/api/governance-approvals/route.ts) and
 * the approve-time side effect that publishes the draft version once the owner
 * says yes. FastAPI has `/agent-profiles/{id}/publish`, which is the direct
 * action this route exists to AVOID for someone who lacks the authority.
 *
 * BACKLOG: FastAPI `POST /agent-profiles/{id}/propose`, once governance requests
 * are modelled.
 */
export async function POST() {
  return notImplemented("POST /agent-profiles/{id}/propose");
}
