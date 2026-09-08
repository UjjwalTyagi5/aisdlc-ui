import { z } from "zod";

import {
  ApprovalEvent,
  type ApprovalSubmitInput,
  type ProjectId,
  Run,
  RunCreateResponse,
  type RunId,
  Step,
  paginated,
} from "@/lib/schemas";

import { AttachmentRef } from "./conversations";
import { api, API_BASE, ApiRequestError } from "./client";

const AttachmentsEnvelope = z.object({ attachments: z.array(AttachmentRef) });

/**
 * Attach files to an Orchestrator run.
 *
 * NOT `uploadAttachments` from `./conversations`, which posts to
 * `/conversations/{id}/attachments`. That endpoint authorises through the conversation
 * row, and the Orchestrator's conversation row is created by the SOCKET on the first
 * turn — so attaching before typing anything 404s against a run that exists. This one
 * resolves the run itself and holds from the moment the run does.
 *
 * Multipart, so a raw fetch rather than `api()`.
 */
export async function uploadRunAttachments(
  runId: string,
  files: File[],
): Promise<AttachmentRef[]> {
  const form = new FormData();
  for (const f of files) form.append("files", f, f.name);
  const res = await fetch(
    `${API_BASE}/runs/${encodeURIComponent(runId)}/attachments`,
    { method: "POST", credentials: "include", body: form },
  );
  if (!res.ok) {
    throw new ApiRequestError(res.status, await res.json().catch(() => null), res.statusText);
  }
  return AttachmentsEnvelope.parse(await res.json()).attachments;
}

/**
 * The files already attached to a run.
 *
 * The backend feeds EVERY stored attachment into EVERY later turn, so a composer that
 * forgot its chips on reload would be describing a prompt that is not the one being
 * sent — the user could no longer see what the agent is being given.
 */
export const listRunAttachments = (runId: string) =>
  api(`/runs/${encodeURIComponent(runId)}/attachments`, {
    schema: AttachmentsEnvelope,
  }).then((r) => r.attachments);

export const createRun = (body: {
  project_id: string;
  /** Preferred: the exact provider connection + model (from /model/options). */
  offering_id?: string | null;
  /** Legacy fallback when no offering is chosen. */
  model_id?: string | null;
  trigger?: string;
  /**
   * Accepted and ignored. EVERY run is chat-driven now — the Copilot drives each
   * stage and gate approvals advance it via POST /api/runs/[id]/copilot/advance.
   * The field survives only so an older client does not fail schema validation
   * mid-deploy; the backend no longer branches on it.
   */
  conversational?: boolean;
}) => api("/runs", { method: "POST", body, schema: RunCreateResponse });

export const listRuns = (query?: {
  projectId?: ProjectId;
  status?: string;
  page?: number;
  pageSize?: number;
}) =>
  api("/runs", {
    query: query as Record<string, string | number | undefined>,
    schema: paginated(Run),
  });

export const getRun = (id: RunId) =>
  api(`/runs/${encodeURIComponent(id)}`, { schema: Run });

/** Cancel a running run. */
export const cancelRun = (id: RunId) =>
  api(`/runs/${encodeURIComponent(id)}/cancel`, { method: "POST", schema: Run });

/** Hard-delete a run (and its artifacts). */
export const deleteRun = (id: RunId) =>
  api(`/runs/${encodeURIComponent(id)}`, { method: "DELETE" });

export const getRunSteps = (id: RunId) =>
  api(`/runs/${encodeURIComponent(id)}/steps`, { schema: z.array(Step) });

export const submitApproval = (id: RunId, input: ApprovalSubmitInput) =>
  api(`/runs/${encodeURIComponent(id)}/approvals`, {
    method: "POST",
    body: input,
    schema: ApprovalEvent,
  });

/** What `POST /runs/{id}/approvals` answers with. */
export const GateDecisionResult = z.object({
  runId: z.string(),
  decision: z.string(),
  reason: z.string().nullable(),
  idempotencyKey: z.string().nullable(),
  recordedAt: z.string(),
});
export type GateDecisionResult = z.infer<typeof GateDecisionResult>;

/**
 * Resolve a run's gate — Approve or Reject on a stage that is paused for a human.
 *
 * IT USED TO POST TO `/runs/{id}/copilot/advance`, AND THAT ROUTE NO LONGER EXISTS.
 * Phase 5A deleted the Copilot: the BFF proxy is asserted gone by
 * `no-copilot-surface.test.ts` and the FastAPI route by `test_old_engines_are_gone.py`.
 * Nothing repointed this caller, so every Approve and Reject on the three screens that
 * use it — the approvals queue, the run drawer and the run conversation — was getting
 * Next's own 404.
 *
 * `POST /runs/{id}/approvals` is where the behaviour went. It re-checks the stage's
 * approve permission, enforces that whoever started a run cannot approve its output,
 * writes the audit event, and clears `gate_pending` so the gate leaves every queue.
 *
 * THE STAGE IS NOT SENT ANY MORE, deliberately. The server reads the run's own
 * `current_stage` and refuses a caller-supplied one — taking it from the body would let
 * a caller name a stage they can approve and record the decision against a run sitting
 * at a different one. The parameter is kept in the signature and ignored so the three
 * call sites did not all have to change in the same commit as the routing fix.
 *
 * `reason` is free text and carries a clarification's ANSWER as well as a rejection's
 * explanation; both land on the audit event.
 */
export const advanceCopilotRun = (
  id: RunId,
  input: { decision: "approved" | "rejected"; stage?: string; reason?: string },
) =>
  api(`/runs/${encodeURIComponent(id)}/approvals`, {
    method: "POST",
    body: { decision: input.decision, reason: input.reason },
    schema: GateDecisionResult,
  });
