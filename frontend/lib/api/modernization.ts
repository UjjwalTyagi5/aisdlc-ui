import type { ProjectId } from "@/lib/schemas";
import {
  DiscoveryResponse,
  LegacyCodeRecord,
  LegacyRepositories,
  MigrationIntentResponse,
} from "@/lib/schemas/modernization";

import { api } from "./client";

/** The project's current migration-intent brief (Track 3 Requirements agent). */
export const getMigrationIntent = (id: ProjectId) =>
  api(`/projects/${encodeURIComponent(id)}/modernization/migration-intent`, {
    schema: MigrationIntentResponse,
  });

/** The project's current Discovery & Assessment. */
export const getDiscoveryAssessment = (id: ProjectId) =>
  api(`/projects/${encodeURIComponent(id)}/modernization/discovery`, {
    schema: DiscoveryResponse,
  });

/* ── Pulled legacy code, and version downloads ──────────────────────────────── */

export type Track3Stage = "requirements_modernization" | "discovery";

/** URL segment of each stage's page data on the backend. */
export const KIND_FOR_STAGE: Record<Track3Stage, "migration-intent" | "discovery"> = {
  requirements_modernization: "migration-intent",
  discovery: "discovery",
};

/** The project's legacy code: the latest pull attempt, and the checkout it holds. */
export const getLegacyCode = (id: ProjectId, stage: Track3Stage) =>
  api(`/projects/${encodeURIComponent(id)}/modernization/legacy-code?stage=${stage}`, {
    schema: LegacyCodeRecord,
  });

/** Start pulling a repository (read-only). Returns at once with `status: pulling`. */
export const pullLegacyCode = (
  id: ProjectId,
  body: { url: string; branch?: string; stage: Track3Stage },
) =>
  api(`/projects/${encodeURIComponent(id)}/modernization/legacy-code`, {
    method: "POST",
    body,
    schema: LegacyCodeRecord,
  });

/** Repositories the project's connection can see — Azure DevOps lists projects first. */
export const listLegacyRepositories = (id: ProjectId, stage: Track3Stage, adoProject = "") =>
  api(
    `/projects/${encodeURIComponent(id)}/modernization/legacy-code/repositories?stage=${stage}` +
      (adoProject ? `&ado_project=${encodeURIComponent(adoProject)}` : ""),
    { schema: LegacyRepositories },
  );

/** A plain link that downloads one frozen version as .docx or .pdf (through the BFF). */
export const versionExportHref = (
  id: ProjectId,
  stage: Track3Stage,
  version: number,
  format: "docx" | "pdf",
) =>
  `/api/projects/${encodeURIComponent(id)}/modernization/${KIND_FOR_STAGE[stage]}/versions/${version}/export?format=${format}`;
