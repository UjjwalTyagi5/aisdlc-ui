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
