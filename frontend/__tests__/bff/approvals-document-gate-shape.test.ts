import { describe, expect, it } from "vitest";
import { ApprovalGate } from "@/lib/schemas/approval";
import { AGENT_OWNERSHIP, ROLE_META, ROLE_ORDER, type PlatformRole } from "@/lib/roles";
import type { Phase } from "@/lib/schemas";

/**
 * The document rows `GET /approvals` now returns actually parse.
 *
 * They did not. Adding pending documents to the queue widened the payload in three
 * ways at once — a new `type`, a null `runId`, a null `phase` — and the zod schema is
 * the only thing standing between that and a page that renders nothing but
 * `response did not match schema "/approvals"`. A schema break here takes out the
 * WHOLE queue, run gates included, because `api()` parses the array as one value: one
 * malformed row and nobody sees anything waiting on them.
 *
 * WHY THE BACKEND TESTS DID NOT CATCH IT. They assert on the FastAPI response, where
 * the shape is whatever Pydantic emitted — by construction it matches its own model.
 * The contract that broke is between that model and the zod schema on the other side
 * of the BFF, and no test spanned the two.
 *
 * The fixtures below are copied from what `_pending_documents` builds in
 * shared/routers/approvals.py. If that changes shape, these have to change with it —
 * which is the point: the failure becomes a red test here instead of an error toast
 * in the browser.
 */

/** Exactly what `app/api/approvals/route.ts` adds on top of the backend row. */
function owningRoleFor(phase: Phase | null): string {
  const owner = phase
    ? ROLE_ORDER.find((role: PlatformRole) => {
        if (role === "project_admin") return false;
        const involvement = AGENT_OWNERSHIP[role][phase];
        return involvement === "owner" || involvement === "primary";
      })
    : undefined;
  return owner ? ROLE_META[owner].label : ROLE_META.project_admin.label;
}

/** A document uploaded to a stage — the common case, and the one bruno hit. */
const stageDocument = {
  id: "artifact:11111111-1111-1111-1111-111111111111",
  type: "document",
  runId: null,
  projectId: "22222222-2222-2222-2222-222222222222",
  projectName: "test-demo",
  phase: "requirements",
  agentType: "requirements",
  requiredPermission: "artifact:approve_requirements",
  capabilityClass: "consequential",
  mandatory: false,
  title: "Ujjwal_Tyagi_Client_Profile_Updated.pptx awaiting approval",
  summary: "test-demo — a requirements document is waiting for an owner to accept it.",
  requestedBy: "bruno@abcbank.com",
  requestedAt: "2026-09-08T10:00:00+00:00",
  artifact: {
    id: "11111111-1111-1111-1111-111111111111",
    title: "Ujjwal_Tyagi_Client_Profile_Updated.pptx",
    type: "document",
  },
};

/** A project-wide document: no stage, so no phase and no owning agent. */
const projectWideDocument = {
  ...stageDocument,
  id: "artifact:33333333-3333-3333-3333-333333333333",
  phase: null,
  agentType: null,
  requiredPermission: "approve",
  artifact: { ...stageDocument.artifact, id: "33333333-3333-3333-3333-333333333333" },
};

/** A run gate, unchanged — the regression risk of widening the schema. */
const runGate = {
  id: "44444444-4444-4444-4444-444444444444:requirements",
  type: "approval",
  runId: "44444444-4444-4444-4444-444444444444",
  projectId: "22222222-2222-2222-2222-222222222222",
  projectName: "test-demo",
  phase: "requirements",
  agentType: "requirements",
  requiredPermission: "artifact:approve_requirements",
  capabilityClass: "consequential",
  mandatory: false,
  title: "Requirements awaiting approval",
  summary: "test-demo — the requirements stage is paused for a decision.",
  requestedBy: "agent",
  requestedAt: "2026-09-08T09:00:00+00:00",
};

/** The row as the browser receives it: backend payload + the BFF's added field. */
function throughBff(row: Record<string, unknown>) {
  return { ...row, waitingForRole: owningRoleFor(row.phase as Phase | null) };
}

describe("what GET /approvals returns for a pending document", () => {
  it("parses a stage document", () => {
    const parsed = ApprovalGate.safeParse(throughBff(stageDocument));

    expect(parsed.success ? null : parsed.error.issues).toBeNull();
  });

  it("parses a project-wide document, which has no run and no phase", () => {
    /** The row most likely to break: two nulls where the schema had required values,
     *  and a `requiredPermission` that names project administration instead of a
     *  stage. */
    const parsed = ApprovalGate.safeParse(throughBff(projectWideDocument));

    expect(parsed.success ? null : parsed.error.issues).toBeNull();
  });

  it("still parses an ordinary run gate", () => {
    /** NON-VACUITY, and the actual regression risk: making fields nullable to admit
     *  documents must not change what a run gate is allowed to be. */
    const parsed = ApprovalGate.safeParse(throughBff(runGate));

    expect(parsed.success ? null : parsed.error.issues).toBeNull();
  });

  it("names the Project Admin only when no delivery role owns the phase", () => {
    /** THE SECOND BUG THIS FILE FOUND, and it predates documents entirely.
     *
     *  `project_admin` is ALL_OWNER in the matrix — the fallback approver on every
     *  agent — and sits ahead of every delivery role in ROLE_ORDER, so `find` matched
     *  it first for EVERY phase. Every gate in the queue was labelled "Project Admin",
     *  including a Requirements gate a Business Analyst owns.
     *
     *  A null phase is the real no-owner case: a project-wide document belongs to no
     *  stage, so project administration genuinely is the answer there. */
    expect(owningRoleFor("requirements")).toBe(ROLE_META.ba.label);
    expect(owningRoleFor(null)).toBe(ROLE_META.project_admin.label);
  });

  it("rejects a row with a type the UI cannot render", () => {
    /** NON-VACUITY for the whole file: if `ApprovalGate` accepted anything, every
     *  assertion above would pass while proving nothing. */
    const parsed = ApprovalGate.safeParse(
      throughBff({ ...stageDocument, type: "something_new" }),
    );

    expect(parsed.success).toBe(false);
  });
});
