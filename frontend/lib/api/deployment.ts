import { z } from "zod";

import type { ProjectId } from "@/lib/schemas";
import { AdoPr } from "@/lib/schemas/code-review";
import {
  DeployConnector,
  DeploymentActionResult,
  DeploymentRequest,
  PrepareDeployResult,
  PreparedDeployState,
  ReleaseResponse,
} from "@/lib/schemas/deployment";

import { api } from "./client";

const enc = encodeURIComponent;

// ADO project/repo/branch cascade reused from the dev-workspace client.

export const listDeployConnectors = (projectId: ProjectId) =>
  api(`/deployment/${enc(projectId)}/connectors`, {
    schema: z.object({ connectors: z.array(DeployConnector) }),
  });

export const listOpenPrs = (projectId: ProjectId, adoProject: string, repo: string) =>
  api(`/deployment/${enc(projectId)}/ado/repos/${enc(adoProject)}/${enc(repo)}/prs`, {
    schema: z.array(AdoPr),
  });

export interface PrepareDeployBody {
  mode: "branch" | "pr";
  ado_project: string;
  repo_name: string;
  branch?: string;
  pr_id?: string;
  environment: string;
  deploy_via?: string;
  image_registry?: string;
  image_name?: string;
  namespace?: string;
}

export const prepareDeploy = (projectId: ProjectId, body: PrepareDeployBody) =>
  api(`/deployment/${enc(projectId)}/deploy/prepare`, {
    method: "POST",
    body,
    schema: PrepareDeployResult,
  });

export const getRelease = (projectId: ProjectId, sessionId: string) =>
  api(`/deployment/${enc(projectId)}/release/${enc(sessionId)}`, {
    schema: ReleaseResponse,
  });

// ── The approval gate (backend phases 1–4) ───────────────────────────────────

export const listDeployments = (projectId: ProjectId, pendingOnly = false) =>
  api(
    `/deployment/${enc(projectId)}/deployments${pendingOnly ? "?pending_only=true" : ""}`,
    { schema: z.array(DeploymentRequest) },
  );

export const approveDeployment = (projectId: ProjectId, deploymentId: string) =>
  api(`/deployment/${enc(projectId)}/deployments/${enc(deploymentId)}/approve`, {
    method: "POST",
    schema: DeploymentRequest,
  });

export const rejectDeployment = (
  projectId: ProjectId, deploymentId: string, reason?: string,
) =>
  api(`/deployment/${enc(projectId)}/deployments/${enc(deploymentId)}/reject`, {
    method: "POST",
    body: { reason: reason ?? "" },
    schema: DeploymentRequest,
  });

/** Performs the deployment. Separate from approval because it is a network call to
 *  Azure DevOps that can be slow and can fail — see the backend route's note. */
export const executeDeployment = (projectId: ProjectId, deploymentId: string) =>
  api(`/deployment/${enc(projectId)}/deployments/${enc(deploymentId)}/execute`, {
    method: "POST",
    schema: DeploymentActionResult,
  });

export const refreshDeployment = (projectId: ProjectId, deploymentId: string) =>
  api(`/deployment/${enc(projectId)}/deployments/${enc(deploymentId)}/refresh`, {
    method: "POST",
    schema: DeploymentActionResult,
  });

/** The target already prepared for this project, if any.
 *
 *  The page used to keep this in React state alone, so a refresh discarded it and the
 *  screen offered to set up a deployment that was already set up — with Chat disabled
 *  the whole time, because it is gated on the same state. */
export const getPreparedDeploy = (projectId: ProjectId) =>
  api(`/deployment/${enc(projectId)}/deploy/prepared`, { schema: PreparedDeployState });
