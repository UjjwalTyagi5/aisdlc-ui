import { z } from "zod";

import {
  Artifact,
  type ArtifactBody,
  type ArtifactId,
  type ProjectId,
  type Status,
} from "@/lib/schemas";

import { api } from "./client";

export const listArtifacts = (projectId: ProjectId, query?: { phase?: string }) =>
  api(`/projects/${encodeURIComponent(projectId)}/artifacts`, {
    query,
    schema: z.array(Artifact),
  });

export const getArtifact = (id: ArtifactId) =>
  api(`/artifacts/${encodeURIComponent(id)}`, { schema: Artifact });

export interface ArtifactPatch {
  title?: string;
  body?: ArtifactBody;
  status?: Status;
}

export const updateArtifact = (id: ArtifactId, patch: ArtifactPatch) =>
  api(`/artifacts/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: patch,
    schema: Artifact,
  });

/** Permanently delete an artifact and its stored file. Irreversible.
 *
 * Returns nothing: the backend answers 204. Callers must invalidate the artifact list
 * query — the deleted row will otherwise stay on screen until the next refetch, which
 * looks exactly like the delete having failed.
 */
export const deleteArtifact = (id: ArtifactId) =>
  api(`/artifacts/${encodeURIComponent(id)}`, {
    method: "DELETE",
    schema: z.unknown(),
  });
