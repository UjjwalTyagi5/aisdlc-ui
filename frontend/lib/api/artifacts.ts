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

/** Accept a generated document into the project's shared record.
 *
 *  This is what MOVES THE BYTES: until approval they sit under the tenant's pending
 *  prefix and the artifact is listed but not downloadable. Only someone who runs the
 *  project can call it — the backend checks `approve` plus project administration.
 */
export const approveArtifact = (id: ArtifactId) =>
  api(`/artifacts/${encodeURIComponent(id)}/approve`, {
    method: "POST",
    schema: Artifact,
  });

/** Decline a generated document. Its pending bytes are deleted; the row is kept as
 *  the record that it was produced and refused. */
export const rejectArtifact = (id: ArtifactId, reason?: string) =>
  api(`/artifacts/${encodeURIComponent(id)}/reject`, {
    method: "POST",
    body: { reason },
    schema: Artifact,
  });
