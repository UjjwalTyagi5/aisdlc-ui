import { notImplemented } from "@/lib/bff/not-implemented";

/**
 * Admit someone to the organisation — the Organization Admin's half of the
 * two-step handover ([[two-step-onboarding-handover]]).
 *
 * NOT IMPLEMENTED AS ONE TRANSACTION BY THE BACKEND. It is three acts, and
 * FastAPI has only some of them: creating the person (`POST /admin/members`
 * exists, but takes a password rather than inviting), placing them in a unit
 * (`POST /admin/assignments` exists), and raising the `role_assignment` request
 * that tells the unit's admin they owe this person a role (does not exist —
 * governance requests are not modelled).
 *
 * The third act is the point of the flow, not a garnish: without it the person
 * lands in a unit and nobody is told to give them a job. `lib/mock/onboarding`
 * did all three against in-memory stores, so the toast said "onboarded" and the
 * database gained nothing.
 *
 * Placing an EXISTING person still works — that is the [id] route's PATCH, which
 * writes real bindings through `/admin/assignments`.
 *
 * BACKLOG: FastAPI `POST /onboarding`, doing all three in one transaction.
 */
export async function POST() {
  return notImplemented("POST /onboarding");
}
