import { z } from "zod";

/**
 * Agent Studio prompt-management schemas. Field casing mirrors the
 * capabilities sibling feature (snake_case, e.g. agent_id/disabled_curated) —
 * confirmed against shared/routers/agent_profiles.py, which documents the
 * same convention explicitly ("Serialization convention: snake_case in
 * responses, matching the sibling capabilities router").
 *
 * prompt_prepend / prompt_append / output_contract_extra are all PLAIN
 * STRINGS on the wire — the backend's DraftIn model declares them `str = ""`
 * (output_contract_extra is free-text, not a JSON object), and every response
 * builder (_version_dict, build_agent_summary) normalizes None -> "" before
 * serializing, so they are never null in a version/summary response.
 */

/**
 * Scope a profile version (or draft/preview) applies at. `"user"` is a
 * platform addition beyond the original three (org/workspace/project) —
 * every person's own personal default, the innermost tier of the cascade
 * (Org → Business Unit → Project → Personal). It is NOT the PRD's
 * `workstream` scope (lib/scope.ts::ScopeLevel) — that's a per-run/change
 * unit inside a project; this is a per-person one, always resolving to the
 * signed-in user regardless of which project/run they're in.
 */
export const ProfileScope = z.enum(["org", "workspace", "project", "user"]);
export type ProfileScope = z.infer<typeof ProfileScope>;

/** Provenance of a resolved prompt layer in a preview stack. */
export const ProfileLayerSource = z.enum(["vendor", "org", "workspace", "project", "user", "draft"]);
export type ProfileLayerSource = z.infer<typeof ProfileLayerSource>;

/** A single lint failure returned by the backend on draft-create/preview validation. */
export const LintViolation = z.object({
  field: z.string(),
  code: z.string(),
  message: z.string(),
});
export type LintViolation = z.infer<typeof LintViolation>;

/** One saved prompt-profile version for an agent at a given scope. */
export const AgentProfileVersion = z.object({
  id: z.string(),
  version: z.number().int(),
  is_active: z.boolean(),
  prompt_prepend: z.string(),
  prompt_append: z.string(),
  output_contract_extra: z.string(),
  created_by: z.string().nullable(),
  created_at: z.string().nullable(),
  updated_at: z.string().nullable(),
  /** Who made this version active and when — the tier owner when they
   *  publish directly, or the approver's name when it was published by
   *  resolving a governance approval (see agentDefaultApprovalType,
   *  lib/governance.ts). Distinct from created_by/created_at, which stay the
   *  original drafter's — a non-owner can draft content that someone else
   *  approves. Null until the version has been made active at least once. */
  published_by: z.string().nullable(),
  published_at: z.string().nullable(),
});
export type AgentProfileVersion = z.infer<typeof AgentProfileVersion>;

/** GET /agent-profiles/versions response. */
export const AgentProfileVersionList = z.object({
  versions: z.array(AgentProfileVersion),
});
export type AgentProfileVersionList = z.infer<typeof AgentProfileVersionList>;

/** The active version's editable content, embedded inline in a summary row. */
export const ActiveProfileContent = z.object({
  prompt_prepend: z.string(),
  prompt_append: z.string(),
  output_contract_extra: z.string(),
});
export type ActiveProfileContent = z.infer<typeof ActiveProfileContent>;

/** Per-agent row in the pipeline-order summary list. */
export const AgentProfileSummaryEntry = z.object({
  agent_id: z.string(),
  active_version: z.number().int().nullable(),
  latest_version: z.number().int().nullable(),
  draft_count: z.number().int(),
  updated_at: z.string().nullable(),
  active: ActiveProfileContent.nullable(),
  /** null = this tier has its own active version; otherwise the nearest
   *  ancestor tier `active` was resolved from (cascade: user → project →
   *  workspace → org). The requested scope simply hasn't published its own
   *  override yet, so it inherits — not "empty". */
  inherited_from: ProfileScope.nullable().optional(),
});
export type AgentProfileSummaryEntry = z.infer<typeof AgentProfileSummaryEntry>;

/** GET /agent-profiles/summary response — all 8 agents, pipeline order. */
export const AgentProfileSummary = z.object({
  agents: z.array(AgentProfileSummaryEntry),
});
export type AgentProfileSummary = z.infer<typeof AgentProfileSummary>;

/**
 * One resolved layer in a preview stack, outermost to innermost. `content` is
 * null only for the locked vendor layer — its prompt is never leaked to the
 * client (build_preview_layers / VENDOR_LAYER_NAME).
 */
export const ProfileLayer = z.object({
  name: z.string(),
  source: ProfileLayerSource,
  locked: z.boolean(),
  content: z.string().nullable(),
  chars: z.number().int(),
});
export type ProfileLayer = z.infer<typeof ProfileLayer>;

/** POST /agent-profiles/preview response. */
export const ProfilePreview = z.object({
  layers: z.array(ProfileLayer),
  warnings: z.array(LintViolation),
});
export type ProfilePreview = z.infer<typeof ProfilePreview>;

/**
 * Shared body shape for draft-create and preview requests — matches the
 * backend's `DraftIn` pydantic model exactly (plain-string fields defaulting
 * to "", scope_id optional/nullable, required only for workspace/project scope).
 */
export const AgentProfileDraftInput = z.object({
  agent_id: z.string(),
  scope: ProfileScope,
  scope_id: z.string().nullish(),
  prompt_prepend: z.string().optional(),
  prompt_append: z.string().optional(),
  output_contract_extra: z.string().optional(),
  /** Chain context so a preview call can resolve inheritance past the
   *  requested tier (draft-create ignores these — a draft only ever
   *  belongs to its own tier). */
  workspace_id: z.string().nullish(),
  project_id: z.string().nullish(),
  user_id: z.string().nullish(),
});
export type AgentProfileDraftInput = z.infer<typeof AgentProfileDraftInput>;

/**
 * Raw 422 body for lint failures on draft-create/preview — FastAPI's default
 * HTTPException wrapping of `detail={"violations": [...]}`, confirmed against
 * `create_draft` in shared/routers/agent_profiles.py. This backend has no
 * global exception handler reshaping errors into the app-wide
 * {code,message,details} ApiError envelope, so this shape reaches the wire
 * verbatim and must be read from ApiRequestError.rawBody, not `.details`.
 */
export const LintViolationError = z.object({
  detail: z.object({
    violations: z.array(LintViolation),
  }),
});
export type LintViolationError = z.infer<typeof LintViolationError>;
