import { z } from "zod";

import type { ApprovalDecision, ArtifactId, RunId } from "@/lib/schemas";

import { api } from "./client";

const SignalAck = z.object({
  accepted: z.boolean(),
  signalName: z.string(),
  runId: z.string(),
  idempotencyKey: z.string(),
});
export type SignalAck = z.infer<typeof SignalAck>;

export interface SendSignalInput {
  runId: RunId;
  /**
   * Temporal signal name — `hitl.decision` is the standard for approval
   * gates; `within_agent_clarification` answers a durable mid-run agent
   * question (REQ-M10-06, milestone-10.2-02 signal endpoint).
   */
  name: "hitl.decision" | "within_agent_clarification" | (string & {});
  payload?: Record<string, unknown>;
  /** Required. Same key replays without producing a duplicate decision. */
  idempotencyKey: string;
}

export const sendRunSignal = ({
  runId,
  name,
  payload,
  idempotencyKey,
}: SendSignalInput) =>
  api(`/runs/${encodeURIComponent(runId)}/signals/${encodeURIComponent(name)}`, {
    method: "POST",
    body: { payload, idempotencyKey },
    schema: SignalAck,
  });

/** Convenience helper for the common "approval gate" signal shape. */
export const sendApprovalSignal = (input: {
  runId: RunId;
  artifactId: ArtifactId;
  decision: ApprovalDecision;
  reason?: string;
  idempotencyKey: string;
}) =>
  sendRunSignal({
    runId: input.runId,
    name: "hitl.decision",
    payload: {
      artifactId: input.artifactId,
      decision: input.decision,
      reason: input.reason,
    },
    idempotencyKey: input.idempotencyKey,
  });

/**
 * Convenience helper for answering a durable within-agent clarification
 * (REQ-M10-06). Sends a Temporal SIGNAL — not an ephemeral WebSocket chat
 * message (D-M10-03) — through the existing BFF
 * `/api/runs/[id]/signals/[name]` route. Payload key casing
 * (`clarification_id`, `answer`) matches what the milestone-10.2-02
 * `within_agent_clarification` workflow signal handler reads.
 */
export const sendClarificationAnswer = (input: {
  runId: RunId;
  clarificationId: string;
  answer: string;
  idempotencyKey: string;
}) =>
  sendRunSignal({
    runId: input.runId,
    name: "within_agent_clarification",
    payload: {
      clarification_id: input.clarificationId,
      answer: input.answer,
    },
    idempotencyKey: input.idempotencyKey,
  });
