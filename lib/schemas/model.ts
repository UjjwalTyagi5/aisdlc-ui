import { z } from "zod";

import { GrantVisibility } from "./grant";

/** Curated presets with first-class UX (logo, known models). */
export const KnownProviderKind = z.enum(["anthropic", "openai", "google"]);
export type KnownProviderKind = z.infer<typeof KnownProviderKind>;

/** Onboarding is dynamic: a provider is any LiteLLM slug, not a closed set.
 *  Presets are accelerators, never a limiter. */
export const ModelProviderKind = z.string();
export type ModelProviderKind = z.infer<typeof ModelProviderKind>;

export const ModelProviderStatus = z.enum(["unverified", "valid", "invalid"]);
export type ModelProviderStatus = z.infer<typeof ModelProviderStatus>;

/**
 * A provider+model pair with no credentials attached — the unit every tier of
 * the cascade below deals in.
 */
export const ModelAllowEntry = z.object({
  provider: ModelProviderKind,
  model_id: z.string(),
});
export type ModelAllowEntry = z.infer<typeof ModelAllowEntry>;

/**
 * One model the Organization Admin has approved for the organization, and how
 * far that approval reaches.
 *
 * This is the ONLY place a model enters the platform's catalogue. A `global`
 * grant lands in every Business Unit automatically; a `specific` one lands
 * only in the units named on it, which the Org Admin picks when creating or
 * administering that unit. A Business Unit Admin cannot widen either — what
 * their unit may use is a consequence of these grants, never a list they
 * curate. (This replaced a per-BU allow-list that BU Admins edited themselves:
 * it let a unit's own admin decide what that unit could use, which is the
 * opposite of what "the Org Admin governs the catalogue" means.)
 *
 * Credentials are a separate axis and deliberately not on this record — see
 * `ModelAvailability.centrallyCredentialed`.
 */
export const OrgModelGrant = z.object({
  provider: ModelProviderKind,
  model_id: z.string(),
  visibility: GrantVisibility.default("global"),
  /** Meaningful only when `visibility === "specific"`. */
  businessUnitIds: z.array(z.string()).default([]),
});
export type OrgModelGrant = z.infer<typeof OrgModelGrant>;

/**
 * What one Business Unit may actually use, resolved from the org grants, plus
 * whether anyone still has to supply a key for it.
 *
 * The two credential flags are the answer to "does this unit have to do
 * anything?": a model the Org Admin credentialed centrally is usable the
 * moment it is granted, and a BU or Project Admin must not be shown a
 * credentials form for it. One with no central key is granted but inert until
 * the unit (or a project inside it) onboards its own.
 */
export const ModelAvailability = z.object({
  provider: ModelProviderKind,
  model_id: z.string(),
  visibility: GrantVisibility,
  /** An org-wide, active provider connection already offers this model. */
  centrallyCredentialed: z.boolean(),
  /** This business unit has onboarded its own active credentials for it. */
  locallyCredentialed: z.boolean(),
});
export type ModelAvailability = z.infer<typeof ModelAvailability>;

/**
 * What one project actually uses, and where its options came from.
 *
 * The org grants say what a project *may* use; this says what it
 * *does*. `usingDefaults` distinguishes "inherits everything the BU allows"
 * (a project that has never customised) from "selected exactly these", which
 * happen to be the same set — the UI must not present the first as a choice
 * someone made.
 */
export const ProjectModelSelection = z.object({
  inherited: z.array(ModelAllowEntry),
  inheritedFrom: z.object({ id: z.string(), name: z.string() }).nullable(),
  selected: z.array(ModelAllowEntry),
  usingDefaults: z.boolean(),
  defaultKey: z.string().nullable(),
});
export type ProjectModelSelection = z.infer<typeof ProjectModelSelection>;

export const ModelApprovalStatus = z.enum(["active", "pending_approval", "rejected"]);
export type ModelApprovalStatus = z.infer<typeof ModelApprovalStatus>;

export const CatalogModel = z.object({
  model_id: z.string(),
  label: z.string(),
  tier_hint: z.string().optional(),
  /** USD per 1M tokens, auto-derived from LiteLLM's cost map when known. */
  input_price_per_million: z.number().nullable().optional(),
  output_price_per_million: z.number().nullable().optional(),
});
export const CatalogProvider = z.object({
  provider: ModelProviderKind,
  label: z.string(),
  models: z.array(CatalogModel),
});
export type CatalogProvider = z.infer<typeof CatalogProvider>;

export const ModelOffering = z.object({
  id: z.string(),
  provider_id: z.string(),
  model_id: z.string(),
  enabled: z.boolean(),
  is_default: z.boolean(),
  /** USD per 1M tokens. Set for custom models; null for catalog models. */
  input_price_per_million: z.number().nullable().optional(),
  output_price_per_million: z.number().nullable().optional(),
  /** Per-model usage limits (null = no limit). */
  rpm_limit: z.number().int().nullable().optional(),
  tpm_limit: z.number().int().nullable().optional(),
  cost_limit_usd: z.number().nullable().optional(),
});
export type ModelOffering = z.infer<typeof ModelOffering>;

export const ModelProvider = z.object({
  id: z.string(),
  provider: ModelProviderKind,
  display_name: z.string(),
  status: ModelProviderStatus,
  /** Custom endpoint (OpenAI-compatible / self-hosted); null for default. */
  api_base: z.string().nullable().optional(),
  /** True for a non-preset provider onboarded via the custom flow. */
  is_custom: z.boolean().optional(),
  last_verified_at: z.string().nullable(),
  created_at: z.string().nullable(),
  offerings: z.array(ModelOffering),
  /** null = onboarded org-wide by an Org Admin; set = scoped to that business
   *  unit, onboarded by its BU Admin (active immediately) or a Project Admin
   *  within it (pending_approval until the BU Admin signs off). */
  workspaceId: z.string().nullable().default(null),
  approvalStatus: ModelApprovalStatus.default("active"),
  approvalDecidedBy: z.string().nullable().default(null),
  approvalDecidedAt: z.string().nullable().default(null),
  approvalReason: z.string().nullable().default(null),
});
export type ModelProvider = z.infer<typeof ModelProvider>;

export const VerifyResult = z.object({ id: z.string(), status: ModelProviderStatus });

export const ModelOption = z.object({
  /** Stable id of the exact provider connection + model. The unit of selection. */
  offering_id: z.string(),
  provider_id: z.string(),
  /** Provider connection name (unique per tenant) — disambiguates two keys. */
  display_name: z.string(),
  provider: ModelProviderKind,
  model_id: z.string(),
  is_default: z.boolean(),
  input_price_per_million: z.number().nullable().optional(),
  output_price_per_million: z.number().nullable().optional(),
});
export type ModelOption = z.infer<typeof ModelOption>;

export const ModelOptions = z.object({
  options: z.array(ModelOption),
  default_offering_id: z.string().nullable(),
  default_model_id: z.string().nullable(),
});
export type ModelOptions = z.infer<typeof ModelOptions>;
