import { z } from "zod";

/** Matches `lib/auth/types.ts`. */
export const Role = z.enum(["admin", "member", "viewer"]);
export type Role = z.infer<typeof Role>;

/** Matches `components/ui/status-badge.tsx`. Source of truth for run/artifact state. */
export const Status = z.enum([
  "draft",
  "queued",
  "running",
  "awaiting_approval",
  "awaiting_clarification",
  "approved",
  "rejected",
  "failed",
  "cancelled",
  "merged",
  "paused",
]);
export type Status = z.infer<typeof Status>;

/**
 * The full agent portfolio across all five delivery tracks (+ orchestrator),
 * per PRD Part V §20.1. The first eight are the shared Track 1 roster; the
 * remaining five are the track-specific additions:
 *
 *  - `discovery`         Discovery & Assessment — Tracks 3 & 4 (PRD §23.2, §24.2)
 *  - `strategy`          Strategy               — Track 3    (PRD §23.4)
 *  - `migration_mapping` Migration Mapping      — Track 4    (PRD §24.3)
 *  - `validation`        Validation             — Track 4    (PRD §24.6)
 *  - `data_engineering`  Data Engineering       — Track 5    (PRD §25.2)
 *
 * Which of these a given project actually shows is driven by its
 * `DeliveryTrack` — see `lib/tracks.ts::agentsForTrack`.
 */
export const AgentType = z.enum([
  "orchestrator",
  "requirements",
  "design",
  "plan", // Project Manager agent — sits between design and development
  "development",
  "review", // Code Review agent
  "security",
  "testing",
  "deployment",
  "documentation",
  "discovery",
  "strategy",
  "migration_mapping",
  "validation",
  "data_engineering",
]);
export type AgentType = z.infer<typeof AgentType>;

/** SDLC phase. Separate from `AgentType` because one agent may drive multiple phases. */
export const Phase = z.enum([
  "requirements",
  "design",
  "plan",
  "development",
  "review", // Code Review
  "security",
  "testing",
  "deployment",
  "documentation",
  "discovery",
  "strategy",
  "migration_mapping",
  "validation",
  "data_engineering",
]);
export type Phase = z.infer<typeof Phase>;

/**
 * The five delivery tracks (PRD §6). A track is a configurable delivery
 * template — not a separate product: it selects the agent roster, required
 * artifacts, gates and metrics, over one shared control plane (PRD §6
 * "Track design principle").
 */
export const DeliveryTrack = z.enum([
  "greenfield", // Track 1 — Greenfield implementation (PRD §7)
  "enhancement", // Track 2 — Enhancement & support / Brownfield (PRD §8)
  "modernization", // Track 3 — Code modernization (PRD §9)
  "rpa_infra", // Track 4 — RPA & infrastructure migration (PRD §10)
  "data_engineering", // Track 5 — Data engineering (PRD §11)
]);
export type DeliveryTrack = z.infer<typeof DeliveryTrack>;

/**
 * Capability classification (PRD §13, §32.2). Every agent action is exactly
 * one of these three — there is no fourth class and no sub-tiering.
 *
 *  - `safe`          Reads/drafts/analysis inside the platform → runs immediately
 *  - `consequential` External write or irreversible action     → halts at a gate
 *  - `signoff`       Formal human acceptance for governance    → explicit decision
 */
export const CapabilityClass = z.enum(["safe", "consequential", "signoff"]);
export type CapabilityClass = z.infer<typeof CapabilityClass>;

export const ArtifactType = z.enum([
  "story",
  "acceptance_criteria",
  "hld",
  "lld",
  "c4_diagram",
  "openapi_spec",
  "db_schema",
  "adr",
  "pr",
  "test_set",
  "coverage_report",
  "review_comment",
  "pipeline",
  "iac_diff",
  "deploy_plan",
  // Generated-file artifacts produced from an agent chat (docx/ppt/png) — see
  // backend shared/services/chat_artifacts.py. Body is a raw download link.
  "document",
  "presentation",
  "diagram",
]);
export type ArtifactType = z.infer<typeof ArtifactType>;

export const ConnectorKind = z.enum([
  "jira",
  "azure_devops",
  "github",
  "azure_repos",
  // Probed by the backend since the deployment agent's Azure Pipelines connector
  // landed. Its absence here failed the WHOLE `GET /connectors` array — one
  // unrecognised kind, and the Integrations page rendered nothing but
  // SCHEMA_MISMATCH. Like `azure_repos`, it is Azure DevOps plumbing rather than a
  // tile of its own, so it belongs here (the set the API accepts) and NOT in
  // CONNECTOR_CATALOG_KINDS (the set the product presents).
  "azure_pipelines",
  "github_actions",
  "slack",
  "ms_teams",
  "sharepoint",
  "figma",
  "confluence",
  "sonarqube",
  "sso_okta",
  "sso_entra",
]);
export type ConnectorKind = z.infer<typeof ConnectorKind>;

export const ConnectorHealth = z.enum(["healthy", "degraded", "disconnected"]);
export type ConnectorHealth = z.infer<typeof ConnectorHealth>;

export const AuditAction = z.enum([
  "project.created",
  "project.archived",
  "run.started",
  "run.completed",
  "run.failed",
  "run.approved",
  "run.rejected",
  "artifact.created",
  "artifact.updated",
  "connector.installed",
  "connector.revoked",
  "user.invited",
  "user.removed",
  "settings.updated",
]);
export type AuditAction = z.infer<typeof AuditAction>;

export const ApprovalDecision = z.enum(["approve", "reject", "retry"]);
export type ApprovalDecision = z.infer<typeof ApprovalDecision>;

export const ModelTier = z.enum(["critical", "balanced", "draft"]);
export type ModelTier = z.infer<typeof ModelTier>;

export const LlmProvider = z.enum([
  "anthropic",
  "openai",
  "azure_openai",
  "bedrock",
  "vertex",
  "fireworks",
]);
export type LlmProvider = z.infer<typeof LlmProvider>;
