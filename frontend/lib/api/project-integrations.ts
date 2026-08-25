import { z } from "zod";

import {
  ProjectIntegration,
  ProjectIntegrationCredential,
  ProjectIntegrationCredentialTestResult,
  ProjectIntegrationInstance,
  type ProjectIntegrationCredentialInput,
  type ProjectIntegrationCredentialTestInput,
  type ProjectIntegrationInstanceInput,
} from "@/lib/schemas/project-integration";

import { api } from "./client";

export type {
  ProjectIntegration,
  ProjectIntegrationCredential,
  ProjectIntegrationCredentialInput,
  ProjectIntegrationCredentialTestInput,
  ProjectIntegrationCredentialTestResult,
  ProjectIntegrationInstance,
  ProjectIntegrationInstanceInput,
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

/**
 * Pin WHICH instance this project's integration talks to.
 *
 * Separate from the credential above, and gated differently: a credential is
 * the caller's own identity and every delivery role sets theirs, while the
 * instance is where that identity gets SENT — a governance decision the server
 * restricts to whoever administers the project.
 */
export const setProjectIntegrationInstance = (
  projectId: string,
  input: ProjectIntegrationInstanceInput,
) =>
  api(`/projects/${encodeURIComponent(projectId)}/integrations/instance`, {
    method: "PUT",
    body: input,
    schema: ProjectIntegrationInstance,
  });

/**
 * Try a credential live, before saving it. Never written anywhere — the
 * value lives only for the one connector call this makes.
 *
 * The target URL is NOT sent: the probe uses the instance the project is
 * pinned to, resolved server-side.
 */
export const testProjectCredential = (
  projectId: string,
  input: ProjectIntegrationCredentialTestInput,
) =>
  api(`/projects/${encodeURIComponent(projectId)}/integrations/test-connection`, {
    method: "POST",
    body: input,
    schema: ProjectIntegrationCredentialTestResult,
  });
