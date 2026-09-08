import { describe, expect, it } from "vitest";
import { countRequests } from "@/components/requests/request-summary-cards";
import type { ApprovalGate, GovernanceApproval } from "@/lib/schemas";

/**
 * The tiles on Requests & Approvals count what the page below them shows.
 *
 * THE BUG, and it was visible in a screenshot: "Total requests 0 · Pending 0 · Raised
 * by me 0" sitting directly above an Inbox listing a document waiting for a decision,
 * uploaded by the very person reading the page. The tiles counted governance requests
 * only, and the Inbox shows two lanes — governance requests raised by a person, and
 * approvals derived from run and artifact state.
 *
 * A summary that contradicts the list under it is worse than no summary: it teaches
 * people that neither number is worth reading.
 *
 * WHY `approved` AND `rejected` STAY BEHIND. `GET /approvals` returns only what is
 * still waiting — a decided gate stops being derived at all, which is precisely what
 * makes the first-approver rule work (approve as one of two equal approvers and the row
 * leaves both queues). So a decided document leaves `total` too; there is no history
 * lane for it to move into. Keeping a decided copy around to count would be the stored
 * queue this deliberately is not.
 */

const ME = "user-me";

function request(over: Partial<GovernanceApproval> = {}): GovernanceApproval {
  return {
    status: "submitted",
    requestedById: "someone-else",
    ...over,
  } as GovernanceApproval;
}

function gate(over: Partial<ApprovalGate> = {}): ApprovalGate {
  return {
    type: "document",
    requestedBy: "bruno@abcbank.com",
    requestedById: "someone-else",
    ...over,
  } as ApprovalGate;
}

describe("what the tiles count", () => {
  it("counts a pending document that has no governance request behind it", () => {
    /** THE HEADLINE. Exactly the state in the screenshot: nothing in the governance
     *  lane, one document waiting. */
    const c = countRequests([], ME, [gate()]);

    expect(c.total).toBe(1);
    expect(c.pending).toBe(1);
  });

  it("adds the two lanes together rather than replacing one with the other", () => {
    const c = countRequests([request(), request({ status: "approved" })], ME, [gate()]);

    expect(c.total).toBe(3);
    expect(c.pending).toBe(2); // one submitted request + one document
    expect(c.approved).toBe(1);
  });

  it("still counts governance requests when no gate is waiting", () => {
    /** NON-VACUITY: the fix must not have made the tiles depend on the new lane. */
    const c = countRequests([request(), request({ status: "rejected" })], ME);

    expect(c.total).toBe(2);
    expect(c.pending).toBe(1);
    expect(c.rejected).toBe(1);
  });
});

describe('"Raised by me"', () => {
  it("counts a document the viewer uploaded", () => {
    /** bruno uploads a document and reads the page: "Raised by me 0" was wrong. */
    const c = countRequests([], ME, [gate({ requestedById: ME })]);

    expect(c.mine).toBe(1);
  });

  it("matches on the id and not the displayed name", () => {
    /** THE TRAP THIS AVOIDS. `requestedBy` is a rendered EMAIL — the queue resolves the
     *  stored actor id through `actor_labels` before sending it — so comparing it to an
     *  identity id would never match and this tile would sit at 0 forever, looking
     *  merely quiet rather than broken. */
    const c = countRequests([], ME, [
      gate({ requestedBy: ME, requestedById: "someone-else" }),
    ]);

    expect(c.mine).toBe(0);
  });

  it("does not count a run gate, which no person raised", () => {
    /** An agent raises a run gate; `requestedById` is null. Counting it as "raised by
     *  me" for whoever is looking would be an invented attribution. */
    const c = countRequests([], ME, [
      gate({ type: "approval", requestedBy: "agent", requestedById: null }),
    ]);

    expect(c.mine).toBe(0);
    expect(c.pending).toBe(1);
  });

  it("counts nothing for a viewer with no identity", () => {
    /** Null identity must not match null `requestedById` — "everyone's" is the one
     *  answer worse than "nobody's". */
    const c = countRequests([], null, [gate({ requestedById: null })]);

    expect(c.mine).toBe(0);
  });
});
