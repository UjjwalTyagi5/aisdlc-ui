import { z } from "zod";

/**
 * Track 3 (Code Modernization) — the shapes its first two agents record.
 *
 * Mirrors `backend/shared/models/artifacts.py` (MigrationIntentArtifact,
 * DiscoveryArtifact) and `discovery_agent/analysis/assessment.py`. Tolerant on
 * purpose — every collection defaults to empty — because these are read back from a
 * JSONB column a newer agent may have written with an extra field or an older one
 * without a newer field; a strict parse would blank the whole page over one of them.
 */

const strings = z.array(z.string()).default([]);
const text = z.string().default("");

/* Version 2 of the brief (structured sections). Every part defaults to empty, so a
   version-1 brief parses and renders through the same view. */
const BriefDriver = z.object({ category: z.string().default("other"), title: text, detail: text });
const BriefLayer = z.object({
  layer: text, current: text, current_status: text, target: text, change_type: text, modules: strings,
});
const BriefRecommendation = z.object({
  summary: text,
  recommended_by: z.string().default("agent"),
  rationale: strings,
  alternatives: z.array(z.object({ option: text, why_not: text })).default([]),
});
const BriefModuleChange = z.object({
  module: text, path: text, current: text, current_status: text, target: text,
  change_type: text, changes: strings, effort: text,
});
const BriefTradeOff = z.object({ decision: text, gain: text, cost: text });
const BriefMilestone = z.object({ date: text, label: text, kind: z.string().default("other") });
const BriefMeasure = z.object({ metric: text, current: text, target: text });

export const MigrationIntentBrief = z.object({
  system_name: z.string().default(""),
  goal: text,
  business_drivers: strings,
  drivers: z.array(BriefDriver).default([]),
  layers: z.array(BriefLayer).default([]),
  recommendation: BriefRecommendation.nullable().default(null),
  module_changes: z.array(BriefModuleChange).default([]),
  trade_offs: z.array(BriefTradeOff).default([]),
  deadline: text,
  budget: text,
  milestones: z.array(BriefMilestone).default([]),
  success_measures: z.array(BriefMeasure).default([]),
  current_state: z.object({ stack: z.string().default(""), description: z.string().default("") })
    .default({ stack: "", description: "" }),
  target_state: z.object({ stack: z.string().default(""), description: z.string().default("") })
    .default({ stack: "", description: "" }),
  in_scope: strings,
  out_of_scope: strings,
  constraints: strings,
  success_criteria: strings,
  stakeholders: z.array(z.object({ name: z.string(), role: z.string().default("") })).default([]),
  assumptions: strings,
  risks: strings,
  open_questions: strings,
  legacy_repository: z
    .object({
      provider: z.string().default(""),
      project: z.string().default(""),
      name: z.string().default(""),
      url: z.string().default(""),
    })
    .nullable()
    .default(null),
  recorded_at: z.string().nullable().default(null),
});
export type MigrationIntentBrief = z.infer<typeof MigrationIntentBrief>;
export type BriefDriver = z.infer<typeof BriefDriver>;
export type BriefLayer = z.infer<typeof BriefLayer>;
export type BriefModuleChange = z.infer<typeof BriefModuleChange>;
export type BriefMilestone = z.infer<typeof BriefMilestone>;

export const MigrationTier = z.enum(["mechanical", "llm_assisted", "manual"]);
export type MigrationTier = z.infer<typeof MigrationTier>;

export const RuntimeStatus = z.enum(["eol", "approaching", "legacy", "supported", "unknown"]);
export type RuntimeStatus = z.infer<typeof RuntimeStatus>;

const Vulnerability = z.object({
  cve: z.string().nullable().optional(),
  severity: z.string().nullable().optional(),
  package: z.string().nullable().optional(),
  installed_version: z.string().nullable().optional(),
  fixed_version: z.string().nullable().optional(),
  title: z.string().nullable().optional(),
});

export const AssessedDependency = z.object({
  name: z.string(),
  version: z.string().default(""),
  kind: z.string().default("package"),
  status: z.string().default("ok"),
  note: z.string().default(""),
  vulnerabilities: z.array(Vulnerability).default([]),
});
export type AssessedDependency = z.infer<typeof AssessedDependency>;

export const RiskFactor = z.object({
  factor: z.string(),
  points: z.number(),
  detail: z.string().default(""),
});

export const AssessedModule = z.object({
  name: z.string(),
  path: z.string(),
  ecosystem: z.string(),
  loc: z.number().default(0),
  files: z.number().default(0),
  /** Third-party front-end files (jQuery, Bootstrap, bundles): counted as files, not LOC. */
  vendored_files: z.number().default(0),
  has_tests: z.boolean().default(false),
  runtime: z.object({
    name: z.string().default(""),
    version: z.string().default(""),
    status: RuntimeStatus.catch("unknown"),
    eol_date: z.string().nullable().default(null),
    note: z.string().default(""),
  }),
  dependencies: z.array(AssessedDependency).default([]),
  depends_on: strings,
  dependents: strings,
  blockers: strings,
  risk: z.object({
    score: z.number(),
    tier: MigrationTier,
    factors: z.array(RiskFactor).default([]),
  }),
});
export type AssessedModule = z.infer<typeof AssessedModule>;

export const DiscoveryAssessment = z.object({
  schema_version: z.number(),
  generated_at: z.string(),
  as_of: z.string().default(""),
  target_stack: z.string().default(""),
  repository: z.object({
    url: z.string().default(""),
    branch: z.string().default(""),
    commit: z.string().default(""),
    provider: z.string().default(""),
    name: z.string().default(""),
  }),
  summary: z.object({
    module_count: z.number(),
    file_count: z.number().default(0),
    loc: z.number().default(0),
    vendored_files: z.number().default(0),
    languages: z.record(z.string(), z.number()).default({}),
    ecosystems: strings,
    tier_counts: z.object({
      mechanical: z.number().default(0),
      llm_assisted: z.number().default(0),
      manual: z.number().default(0),
    }),
    risk: z.object({ average: z.number().default(0), max: z.number().default(0) }),
    flag_counts: z.object({
      eol: z.number().default(0),
      deprecated: z.number().default(0),
      vulnerable: z.number().default(0),
    }),
  }),
  modules: z.array(AssessedModule),
  flags: z.object({
    eol: z.array(z.object({
      module: z.string(), runtime: z.string(), status: z.string(),
      eol_date: z.string().nullable().default(null), note: z.string().default(""),
    })).default([]),
    deprecated: z.array(z.object({
      module: z.string(), package: z.string(), version: z.string().default(""), reason: z.string(),
    })).default([]),
    vulnerable: z.array(z.object({
      module: z.string(), package: z.string().nullable().optional(),
      version: z.string().nullable().optional(), cve: z.string().nullable().optional(),
      severity: z.string().nullable().optional(), fixed_version: z.string().nullable().optional(),
      title: z.string().nullable().optional(),
    })).default([]),
  }),
  scanners: z.object({ trivy: z.string().default("skipped"), note: z.string().default("") })
    .default({ trivy: "skipped", note: "" }),
  golden_master: z.object({ status: z.string(), note: z.string().default("") })
    .default({ status: "not_captured", note: "" }),
});
export type DiscoveryAssessment = z.infer<typeof DiscoveryAssessment>;

/** The read endpoints' envelope: the newest run holding a value, or nulls. */
export function stagePayload<T extends z.ZodTypeAny>(payload: T) {
  return z.object({
    projectId: z.string(),
    runId: z.string().nullable(),
    updatedAt: z.string().nullable(),
    payload: payload.nullable(),
  });
}

export const MigrationIntentResponse = stagePayload(MigrationIntentBrief);
export type MigrationIntentResponse = z.infer<typeof MigrationIntentResponse>;
export const DiscoveryResponse = stagePayload(DiscoveryAssessment);
export type DiscoveryResponse = z.infer<typeof DiscoveryResponse>;

/* ── The project's pulled legacy code (both Track 3 pages) ─────────────────── */

export const LegacyCodeStatus = z.enum(["none", "pulling", "ready", "failed"]);
export type LegacyCodeStatus = z.infer<typeof LegacyCodeStatus>;

export const LegacyCodeModule = z.object({
  name: z.string(),
  path: z.string().default(""),
  ecosystem: z.string().default(""),
  runtime: z.string().default(""),
  runtimeStatus: z.string().default("unknown"),
  eolDate: z.string().nullable().default(null),
  loc: z.number().default(0),
  files: z.number().default(0),
  hasTests: z.boolean().default(false),
  platformFeatures: strings,
  dependsOn: strings,
  packageCount: z.number().default(0),
});
export type LegacyCodeModule = z.infer<typeof LegacyCodeModule>;

/** The last GOOD pull — what the project's checkout holds right now. */
export const LegacyCodePull = z.object({
  url: z.string(),
  branch: z.string().default(""),
  commit: z.string().default(""),
  provider: z.string().default(""),
  name: z.string().default(""),
  pulledBy: z.string().default(""),
  pulledAt: z.string().default(""),
  profile: z
    .object({
      summary: z
        .object({
          modules: z.number().default(0),
          files: z.number().default(0),
          loc: z.number().default(0),
          vendoredFiles: z.number().default(0),
          languages: z.record(z.string(), z.number()).default({}),
          ecosystems: strings,
          endOfLife: z.number().default(0),
          deprecatedPackages: z.number().default(0),
        })
        .partial()
        .default({}),
      modules: z.array(LegacyCodeModule).default([]),
    })
    .nullable()
    .default(null),
});
export type LegacyCodePull = z.infer<typeof LegacyCodePull>;

/** `status` is the latest ATTEMPT; `pull` is the last good one (kept when a re-pull fails). */
export const LegacyCodeRecord = z.object({
  projectId: z.string(),
  status: LegacyCodeStatus.catch("none"),
  request: z
    .object({
      url: z.string().default(""),
      branch: z.string().default(""),
      requestedBy: z.string().default(""),
      startedAt: z.string().nullable().default(null),
    })
    .nullable()
    .default(null),
  error: z.string().default(""),
  pull: LegacyCodePull.nullable().default(null),
});
export type LegacyCodeRecord = z.infer<typeof LegacyCodeRecord>;

/** What the project's Azure DevOps / GitHub connection can see, for the Pull dialog. */
export const LegacyRepositories = z.object({
  provider: z.string().default(""),
  projects: z.array(z.string()).optional(),
  repositories: z
    .array(z.object({ name: z.string(), url: z.string().default(""), defaultBranch: z.string().default("") }))
    .optional(),
  problem: z.string().default(""),
});
export type LegacyRepositories = z.infer<typeof LegacyRepositories>;
