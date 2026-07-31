import { z } from "zod";

export { AdoPr } from "@/lib/schemas/code-review";

export const PrepareScanResult = z.object({
  status: z.string(),
  mode: z.enum(["branch", "pr"]),
  repo_name: z.string(),
  ado_project: z.string(),
  branch: z.string(),
  pr_id: z.string().nullable().optional(),
  pr_title: z.string().nullable().optional(),
  head_sha: z.string(),
});
export type PrepareScanResult = z.infer<typeof PrepareScanResult>;

export const RiskScore = z.enum(["critical", "high", "medium", "low", "none"]);
export type RiskScore = z.infer<typeof RiskScore>;

export const SignoffDecision = z.enum(["pass", "fail", "conditional"]);
export type SignoffDecision = z.infer<typeof SignoffDecision>;

export const ScanSummaryRow = z.object({
  id: z.string(),
  label: z.string(),
  repo_name: z.string(),
  risk_score: RiskScore,
  signoff: SignoffDecision,
  findings_count: z.number(),
  critical: z.number(),
  created_at: z.string(),
});
export type ScanSummaryRow = z.infer<typeof ScanSummaryRow>;

export const Severity = z.enum(["critical", "high", "medium", "low", "info"]);
export type Severity = z.infer<typeof Severity>;

export const SecurityFinding = z.object({
  id: z.string(),
  severity: Severity,
  category: z.string(),
  title: z.string(),
  cve: z.string().nullable().optional(),
  file: z.string().default(""),
  line: z.number().default(0),
  package: z.string().nullable().optional(),
  reachability: z.string().default("unknown"),
  triage: z.string().default("unconfirmed"),
  description: z.string().default(""),
  remediation: z.string().default(""),
  autofix_patch: z.string().nullable().optional(),
  compliance: z.array(z.string()).default([]),
});
export type SecurityFinding = z.infer<typeof SecurityFinding>;

export const SbomComponent = z.object({
  name: z.string(),
  version: z.string().default(""),
  license: z.string().nullable().optional(),
  vulnerabilities: z.number().default(0),
});

export const SecurityArtifact = z.object({
  id: z.string().optional(),
  created_at: z.string().optional(),
  context: z.object({
    repo_name: z.string().default(""),
    ado_project: z.string().default(""),
    mode: z.enum(["branch", "pr"]).default("branch"),
    branch: z.string().default(""),
    pr_id: z.string().nullable().optional(),
    pr_title: z.string().nullable().optional(),
    head_sha: z.string().default(""),
  }),
  summary: z.string().default(""),
  risk_score: RiskScore,
  signoff: z.object({
    decision: SignoffDecision,
    rationale: z.string().default(""),
  }),
  findings: z.array(SecurityFinding).default([]),
  sbom: z.array(SbomComponent).default([]),
  supply_chain: z
    .array(z.object({ package: z.string(), risk: z.string().default("low"), note: z.string().default("") }))
    .default([]),
  remediation_plan: z.string().default(""),
  suppression_log: z
    .array(z.object({ finding_id: z.string(), reason: z.string().default("") }))
    .default([]),
  compliance_frameworks: z.array(z.string()).default([]),
  metrics: z.object({
    critical: z.number().default(0),
    high: z.number().default(0),
    medium: z.number().default(0),
    low: z.number().default(0),
    total: z.number().default(0),
  }),
  status: z.string().default("scanned"),
});
export type SecurityArtifact = z.infer<typeof SecurityArtifact>;
