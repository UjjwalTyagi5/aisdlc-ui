import { z } from "zod";

import {
  ProjectIntegration,
  ProjectIntegrationCredential,
  type ProjectIntegrationCredentialInput,
} from "@/lib/schemas/project-integration";

import { api } from "./client";

export type {
  ProjectIntegration,
  ProjectIntegrationCredential,
  ProjectIntegrationCredentialInput,
  ProjectIntegrationKind,
} from "@/lib/schemas/project-integration";

/** What this project may use, with whatever credential it holds against each. */
export const listProjectIntegrations = (projectId: string) =>
  api(`/projects/${encodeURIComponent(projectId)}/integrations`, {
    schema: z.array(ProjectIntegration),
  });

/** Create or replace the project's credential for one approved integration. */
export const saveProjectCredential = (
  projectId: string,
  input: ProjectIntegrationCredentialInput,
) =>
  api(`/projects/${encodeURIComponent(projectId)}/integrations`, {
    method: "PUT",
    body: input,
    schema: ProjectIntegrationCredential,
  });
