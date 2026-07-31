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
