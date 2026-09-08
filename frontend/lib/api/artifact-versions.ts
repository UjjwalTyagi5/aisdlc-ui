/**
 * Frozen stage artifact versions, and the publication gate on them.
 *
 * THE SECOND ARTIFACT SHAPE. `lib/api/artifacts.ts` covers blob documents — DOCX, PDF,
 * diagrams — which carry their own approval. This covers the stage PAYLOAD, the JSONB
 * that agents actually hand to each other, which had no approval state at all: it was
 * written in place, so approving it meant nothing once the next run overwrote it.
 *
 * A version is frozen at creation (enforced by a database trigger), numbered per
 * project+stage, and published exactly once by the role that owns the stage.
 *
 * STAGE NAMES HERE ARE BACKEND NAMES. `code_review`, never the UI's `review` — the
 * route resolves `artifact:approve_<stage>` from this string, and the UI spelling
 * resolves to no permission and a 403. Use `toBackendStage` rather than passing a
 * `Phase` straight through.
 */

import { z } from "zod";

import type { ProjectId } from "@/lib/schemas";

import { api } from "./client";

export const ArtifactVersionStatus = z.enum([
  "draft",
  "published",
  "rejected",
  "superseded",
]);
export type ArtifactVersionStatus = z.infer<typeof ArtifactVersionStatus>;

export const ArtifactVersion = z.object({
  id: z.string(),
  stage: z.string(),
  version: z.number(),
  status: ArtifactVersionStatus,
  contentHash: z.string(),
  producedBy: z.string(),
  publishedBy: z.string().nullable().optional(),
  publishedAt: z.string().nullable().optional(),
  rejectionReason: z.string().nullable().optional(),
  covers: z.array(z.unknown()).default([]),
  createdAt: z.string().nullable().optional(),
  runId: z.string().nullable().optional(),
});
export type ArtifactVersion = z.infer<typeof ArtifactVersion>;

/** A single version, with the frozen payload. Only the detail reads carry it. */
export const ArtifactVersionDetail = ArtifactVersion.extend({
  payload: z.unknown().nullable().optional(),
});
export type ArtifactVersionDetail = z.infer<typeof ArtifactVersionDetail>;

/**
 * The UI calls the Code Review phase `review`; the backend stage is `code_review`.
 * Those two diverging is what left the owner map wrong for months, so the translation
 * happens in exactly one place.
 */
export function toBackendStage(phase: string): string {
  return phase === "review" ? "code_review" : phase;
}

const base = (projectId: ProjectId, phase: string) =>
  `/artifact-versions/${encodeURIComponent(projectId)}/stages/${encodeURIComponent(
    toBackendStage(phase),
  )}/versions`;

export const listStageVersions = (projectId: ProjectId, phase: string) =>
  api(base(projectId, phase), { schema: z.array(ArtifactVersion) });

/**
 * The version consumers will read once enforcement is on, or null.
 *
 * NULL IS A REAL ANSWER — "nothing approved yet" — and the caller must say so. Quietly
 * showing the draft instead is the one thing this whole feature exists to prevent.
 */
export const getPublishedVersion = (projectId: ProjectId, phase: string) =>
  api(`${base(projectId, phase)}/published`, {
    schema: ArtifactVersionDetail.nullable(),
  });

export const getStageVersion = (projectId: ProjectId, phase: string, version: number) =>
  api(`${base(projectId, phase)}/${version}`, { schema: ArtifactVersionDetail });

/** Freeze the stage's current working payload as the next version. */
export const snapshotStageVersion = (
  projectId: ProjectId,
  phase: string,
  body: { payload: unknown; runId?: string | null; covers?: string[] },
) =>
  api(base(projectId, phase), {
    method: "POST",
    body,
    schema: ArtifactVersion,
  });

export const publishStageVersion = (
  projectId: ProjectId,
  phase: string,
  version: number,
) =>
  api(`${base(projectId, phase)}/${version}/publish`, {
    method: "POST",
    schema: ArtifactVersion,
  });

export const rejectStageVersion = (
  projectId: ProjectId,
  phase: string,
  version: number,
  reason: string,
) =>
  api(`${base(projectId, phase)}/${version}/reject`, {
    method: "POST",
    body: { reason },
    schema: ArtifactVersion,
  });

/** How one consuming stage stands against one producing stage. */
export const ConsumptionState = z.enum([
  /** A published version exists — any consumer in the project may read it. */
  "open",
  /** Nothing published, but this consumer has an owner-issued grant for a version. */
  "granted",
  /** Nothing published and no grant. Ask the producing stage's owner. */
  "needs_request",
  /** A stage does not consume itself. */
  "self",
]);
export type ConsumptionState = z.infer<typeof ConsumptionState>;

export const MatrixRow = z.object({
  stage: z.string(),
  /** The role that signs this stage off — who a request goes to. */
  ownerRole: z.string().nullable(),
  publishedVersion: z.number().nullable(),
  publishedBy: z.string().nullable(),
  consumers: z.record(z.string(), ConsumptionState),
});
export type MatrixRow = z.infer<typeof MatrixRow>;

export const ConsumptionMatrix = z.object({
  /** Whether the gate is switched on. With it false every cell is academic — agents
   *  still read the working draft — so the UI must say so rather than implying rules
   *  that are not being applied. */
  enforced: z.boolean(),
  stages: z.array(MatrixRow),
});
export type ConsumptionMatrix = z.infer<typeof ConsumptionMatrix>;

export const getConsumptionMatrix = (projectId: ProjectId) =>
  api(`/artifact-versions/${encodeURIComponent(projectId)}/matrix`, {
    schema: ConsumptionMatrix,
  });

export const requestConsumptionAccess = (
  projectId: ProjectId,
  phase: string,
  version: number,
  body: { consumerStage: string; reason: string },
) =>
  api(`${base(projectId, phase)}/${version}/request-access`, {
    method: "POST",
    body,
    schema: z.object({
      requestId: z.string().nullable().optional(),
      approverRole: z.string().nullable().optional(),
    }),
  });

/** One thing a run read, with the hash of exactly what it read. */
export const RunEvidenceEntry = z.object({
  producingStage: z.string(),
  version: z.number(),
  consumerStage: z.string(),
  consumedBy: z.string().nullable(),
  consumedAt: z.string().nullable(),
  /** Allowed by an owner-issued grant rather than by publication. An exception shown
   *  like routine approved work is how a reviewer learns to stop reading the column. */
  viaGrant: z.boolean(),
  contentHash: z.string(),
  producedBy: z.string(),
  publishedBy: z.string().nullable(),
  /** The version's status NOW, which may have moved since it was read — a run that
   *  consumed something later superseded is the case people go looking for. */
  statusNow: z.string(),
});
export type RunEvidenceEntry = z.infer<typeof RunEvidenceEntry>;

export const RunEvidence = z.object({
  runId: z.string(),
  consumed: z.array(RunEvidenceEntry),
});
export type RunEvidence = z.infer<typeof RunEvidence>;

/** "What did this run build on." */
export const getRunEvidence = (projectId: ProjectId, runId: string) =>
  api(
    `/artifact-versions/${encodeURIComponent(projectId)}/runs/${encodeURIComponent(
      runId,
    )}/evidence`,
    { schema: RunEvidence },
  );

export const VersionConsumers = z.object({
  stage: z.string(),
  version: z.number(),
  status: z.string(),
  contentHash: z.string(),
  consumers: z.array(
    z.object({
      consumerStage: z.string(),
      consumerRunId: z.string().nullable(),
      consumedBy: z.string().nullable(),
      consumedAt: z.string().nullable(),
      viaGrant: z.boolean(),
    }),
  ),
});
export type VersionConsumers = z.infer<typeof VersionConsumers>;

/** "What was built on this version" — the blast radius of one signed artifact. */
export const getVersionConsumers = (
  projectId: ProjectId,
  phase: string,
  version: number,
) =>
  api(`${base(projectId, phase)}/${version}/consumers`, {
    schema: VersionConsumers,
  });
