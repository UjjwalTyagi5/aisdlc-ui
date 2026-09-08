import { z } from "zod";

export const DeployConnector = z.object({
  kind: z.string(),
  label: z.string(),
  available: z.boolean(),
});
export type DeployConnector = z.infer<typeof DeployConnector>;

export const PrepareDeployResult = z.object({
  status: z.string(),
  mode: z.enum(["branch", "pr"]),
  repo_name: z.string(),
  ado_project: z.string(),
  branch: z.string(),
  pr_id: z.string().nullable().optional(),
  pr_title: z.string().nullable().optional(),
  head_sha: z.string(),
  environment: z.string(),
  deploy_via: z.string(),
  image_registry: z.string().default(""),
  image_name: z.string().default(""),
  namespace: z.string().default(""),
});
export type PrepareDeployResult = z.infer<typeof PrepareDeployResult>;

export const RiskScore = z.enum(["critical", "high", "medium", "low", "none"]);
export type RiskScore = z.infer<typeof RiskScore>;

export const GeneratedFile = z.object({
  path: z.string(),
  language: z.string().default("yaml"),
  contents: z.string().default(""),
});

export const DeploymentArtifact = z.object({
  context: z.object({
    repo_name: z.string().default(""),
    ado_project: z.string().default(""),
    mode: z.enum(["branch", "pr"]).default("branch"),
    source_branch: z.string().default(""),
    pr_id: z.string().nullable().optional(),
    head_sha: z.string().default(""),
    environment: z.string().default("staging"),
    deploy_via: z.string().default("unknown"),
  }),
  summary: z.string().default(""),
  readiness: z.enum(["ready", "blocked", "conditional"]).default("conditional"),
  risk_score: RiskScore,
  risk_rationale: z.string().default(""),
  gate_summary: z
    .array(z.object({ name: z.string(), status: z.string(), note: z.string().default("") }))
    .default([]),
  generated_files: z.array(GeneratedFile).default([]),
  deploy_runbook: z.string().default(""),
  rollback_runbook: z.string().default(""),
  iac_findings: z
    .array(z.object({
      file: z.string().default(""), severity: z.string().default("low"),
      rule: z.string().default(""), description: z.string().default(""), remediation: z.string().default(""),
    }))
    .default([]),
  compliance_evidence: z.object({
    captured_at: z.string().default(""),
    gate_approvals: z.array(z.string()).default([]),
    test_summary: z.string().default(""),
    security_summary: z.string().default(""),
    sbom_present: z.boolean().default(false),
    notes: z.string().default(""),
  }),
  release_decision: z.enum(["go", "no_go", "conditional"]).default("conditional"),
  release_justification: z.string().default(""),
  pr_url: z.string().nullable().optional(),
  pr_title: z.string().nullable().optional(),
  status: z.string().default("assessed"),
});
export type DeploymentArtifact = z.infer<typeof DeploymentArtifact>;

export const ReleaseResponse = z.object({
  release: DeploymentArtifact.nullable(),
  staged_files: z.array(z.object({ path: z.string(), language: z.string().default("yaml") })).default([]),
});
export type ReleaseResponse = z.infer<typeof ReleaseResponse>;

/**
 * A requested deployment action and the human decision about it (backend phase 1).
 *
 * `approvalStatus` and `executionStatus` are separate on purpose: an approved
 * deployment that failed is not a rejected one, and collapsing the two loses the fact
 * that somebody said yes.
 */
export const DeploymentAction = z.enum([
  "create_pipeline",
  "run_pipeline",
  "direct_apply",
]);
export type DeploymentAction = z.infer<typeof DeploymentAction>;

export const DeploymentRequest = z.object({
  id: z.string(),
  projectId: z.string(),
  runId: z.string().nullable().optional(),
  action: DeploymentAction,
  targetKind: z.string(),
  environment: z.string(),
  request: z.record(z.string(), z.unknown()).default({}),
  requestedBy: z.string(),
  requestedAt: z.string().nullable().optional(),
  approvalStatus: z.enum(["pending", "approved", "rejected"]),
  approvedBy: z.string().nullable().optional(),
  approvedAt: z.string().nullable().optional(),
  rejectionReason: z.string().nullable().optional(),
  executionStatus: z.enum([
    "not_started", "running", "succeeded", "failed", "canceled", "error",
  ]),
  executedAt: z.string().nullable().optional(),
  externalId: z.string().nullable().optional(),
  externalUrl: z.string().nullable().optional(),
  outcome: z.record(z.string(), z.unknown()).nullable().optional(),
});
export type DeploymentRequest = z.infer<typeof DeploymentRequest>;

/** One stage of a pipeline run that failed, as the backend timeline reports it. */
export const FailedStage = z.object({
  name: z.string().nullable().optional(),
  type: z.string().nullable().optional(),
  result: z.string().nullable().optional(),
  issues: z.array(z.object({
    type: z.string().nullable().optional(),
    message: z.string().nullable().optional(),
  })).default([]),
});
export type FailedStage = z.infer<typeof FailedStage>;

/** What POST .../execute and .../refresh answer. Loose on purpose — both return a
 *  handful of shapes (unchanged, running, failed-with-stages) and the UI branches on
 *  executionStatus, not on the envelope. */
export const DeploymentActionResult = z.object({
  deployment_id: z.string().optional(),
  execution_status: z.string().optional(),
  external_url: z.string().nullable().optional(),
  unchanged: z.boolean().optional(),
  detail: z.string().optional(),
  failed_stages: z.array(FailedStage).optional(),
});
export type DeploymentActionResult = z.infer<typeof DeploymentActionResult>;

/** What GET /deploy/prepared answers. Identical to PrepareDeployResult except that
 *  `status` is null when nothing is prepared — the honest answer after a backend
 *  restart, since the prepared session lives in memory. */
export const PreparedDeployState = z.object({
  status: z.string().nullable(),
  mode: z.enum(["branch", "pr"]).optional(),
  repo_name: z.string().optional(),
  ado_project: z.string().optional(),
  branch: z.string().optional(),
  pr_id: z.string().nullable().optional(),
  pr_title: z.string().nullable().optional(),
  head_sha: z.string().optional(),
  environment: z.string().optional(),
  deploy_via: z.string().optional(),
  image_registry: z.string().optional(),
  image_name: z.string().optional(),
  namespace: z.string().optional(),
});
export type PreparedDeployState = z.infer<typeof PreparedDeployState>;
