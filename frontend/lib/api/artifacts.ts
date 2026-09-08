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

/** Delete the document if you own it; otherwise ask the person who does.
 *
 * TWO OUTCOMES, and the caller must say the right thing about each:
 *
 *   204  you own this agent — the file and its row are GONE
 *   202  you do not — a request was raised and nothing was destroyed
 *
 * A Project Admin owns every agent on their project, and a stage's own role owns its
 * agent. Sending those people through an approval would ask them for permission they
 * already hold, and it could not complete anyway: self-approval is blocked, so the
 * request escalates away from the only person entitled to answer it.
 *
 * Resolves to `{ deleted }` so the UI can pick its wording. The BFF states that
 * explicitly rather than leaving it to be inferred from an empty body: `bffFetch`
 * collapses a 204 to `undefined`, so "deleted" and "the backend returned nothing" would
 * otherwise look identical, and a wrong guess would report a request raised while the
 * file was actually gone.
 *
 * A REASON IS REQUIRED and the backend 422s without one.
 *
 * `deleteArtifact` below still deletes outright with no ownership test. It is the
 * administrator's break-glass path, not what any list calls.
 */
export const requestArtifactDeletion = async (
  id: ArtifactId,
  reason: string,
): Promise<{ deleted: boolean }> => {
  const res = (await api(`/artifacts/${encodeURIComponent(id)}/deletion-request`, {
    method: "POST",
    body: { reason },
    schema: z.object({ deleted: z.boolean() }).passthrough(),
  })) as { deleted: boolean };
  return { deleted: res.deleted };
};

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

/**
 * Upload a document to a project by hand.
 *
 * `stage` omitted means PROJECT-LEVEL — a policy or standard that is not one agent's
 * output. Present, it must name a real backend stage.
 *
 * Uses fetch directly rather than `api()`: that helper sends JSON, and setting a
 * Content-Type here would send a multipart boundary that does not match the body.
 * The browser lets fetch derive it from the FormData.
 *
 * The uploaded document is PENDING — listed but not downloadable until somebody with
 * the stage's approve permission (or project administration) accepts it.
 */
export async function uploadArtifact(
  projectId: ProjectId,
  file: File,
  opts: { stage?: string | null; artifactType?: string } = {},
): Promise<Artifact> {
  const form = new FormData();
  form.append("file", file);
  if (opts.stage) form.append("stage", opts.stage);
  if (opts.artifactType) form.append("artifact_type", opts.artifactType);

  const res = await fetch(
    `/api/projects/${encodeURIComponent(projectId)}/artifacts/upload`,
    { method: "POST", body: form },
  );
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    // The backend says WHY — a rejected extension, an oversized file, an unknown
    // stage. Replacing that with a generic message would leave the user guessing at
    // which of the three it was.
    const detail =
      (body as { detail?: string } | null)?.detail ?? "Upload failed";
    throw new Error(detail);
  }
  return Artifact.parse(body);
}
