/**
 * Dummy model catalogue for the model picker — plain data, server-safe
 * (imported by the app/api/model/options route handler). This is the
 * DUMMY-DATA source; the LiteLLM-backed catalog (Phase 2) replaces the route
 * handler body, not these shapes.
 *
 * Also the DUMMY-DATA source for the model cascade:
 *
 *   full catalog
 *     → an Org Admin's grants (which models the organization may use, each
 *       reaching every unit or only named ones — see `OrgModelGrant`)
 *     → what one Business Unit may use (derived, never curated locally)
 *     → what one project selected from that (`PROJECT_SELECTION`)
 *
 * Credentials run alongside, not through, that chain: an Org Admin may
 * credential a granted model centrally, in which case nothing below needs a
 * key; where they haven't, a BU Admin (active immediately) or a Project Admin
 * (pending BU approval — see lib/mock/governance-approval-fixtures.ts) may
 * onboard their own for granted models only.
 */
import type {
  CatalogProvider,
  ModelAllowEntry,
  ModelAvailability,
  ModelOffering,
  ModelOption,
  ModelProvider,
  ModelProviderStatus,
  OrgModelGrant,
} from "@/lib/schemas/model";
import { grantReaches, type GrantVisibility } from "@/lib/schemas/grant";
import { createGovernanceApproval } from "@/lib/mock/governance-approval-fixtures";
import { getWorkspace } from "@/lib/mock/workspace-fixtures";
import type { PlatformRole } from "@/lib/roles";

export const MODEL_OPTIONS: ModelOption[] = [
  {
    offering_id: "off_anthropic_sonnet",
    provider_id: "prov_anthropic",
    display_name: "Anthropic",
    provider: "anthropic",
    model_id: "claude-sonnet-4-6",
    is_default: true,
    input_price_per_million: 3,
    output_price_per_million: 15,
  },
  {
    offering_id: "off_anthropic_opus",
    provider_id: "prov_anthropic",
    display_name: "Anthropic",
    provider: "anthropic",
    model_id: "claude-opus-4-7",
    is_default: false,
    input_price_per_million: 15,
    output_price_per_million: 75,
  },
  {
    offering_id: "off_anthropic_haiku",
    provider_id: "prov_anthropic",
    display_name: "Anthropic",
    provider: "anthropic",
    model_id: "claude-haiku-4-5",
    is_default: false,
    input_price_per_million: 0.8,
    output_price_per_million: 4,
  },
];

export const DEFAULT_OFFERING_ID = "off_anthropic_sonnet";
export const DEFAULT_MODEL_ID = "claude-sonnet-4-6";

// ───────────────────────────────────────────────────────────────────────
//  Catalogue cascade
// ───────────────────────────────────────────────────────────────────────

/** The full catalog — any provider LiteLLM supports is onboardable in
 *  principle; this mock ships a handful of presets plus pricing. */
export const CATALOG: CatalogProvider[] = [
  {
    provider: "anthropic",
    label: "Anthropic",
    models: [
      { model_id: "claude-sonnet-4-6", label: "Claude Sonnet 4.6", input_price_per_million: 3, output_price_per_million: 15 },
      { model_id: "claude-opus-4-7", label: "Claude Opus 4.7", input_price_per_million: 15, output_price_per_million: 75 },
      { model_id: "claude-haiku-4-5", label: "Claude Haiku 4.5", input_price_per_million: 0.8, output_price_per_million: 4 },
    ],
  },
  {
    provider: "openai",
    label: "OpenAI",
    models: [
      { model_id: "gpt-5.1", label: "GPT-5.1", input_price_per_million: 5, output_price_per_million: 20 },
      { model_id: "gpt-5.1-mini", label: "GPT-5.1 Mini", input_price_per_million: 1, output_price_per_million: 4 },
    ],
  },
  {
    provider: "google",
    label: "Google",
    models: [
      { model_id: "gemini-3-pro", label: "Gemini 3 Pro", input_price_per_million: 4, output_price_per_million: 16 },
      { model_id: "gemini-3-flash", label: "Gemini 3 Flash", input_price_per_million: 0.5, output_price_per_million: 2 },
    ],
  },
];

export function getModelCatalog(): CatalogProvider[] {
  return CATALOG;
}

/** A grant needs both halves of the pair to name anything. It is deliberately
 *  NOT checked against CATALOG: self-hosted and brand-new models are onboarded
 *  by hand (see the custom-model rows in the add-provider dialog), and a
 *  catalog check would silently drop the grant for exactly those. */
function isNamedEntry(entry: ModelAllowEntry): boolean {
  return entry.provider.trim().length > 0 && entry.model_id.trim().length > 0;
}

/**
 * The Org Admin's grants — the single source of truth for what exists in the
 * organization's catalogue and who gets it.
 *
 * Seeded with both visibilities on purpose: with everything `global` the
 * per-unit grant UI never renders a selected unit and a working feature reads
 * as a dead one.
 */
let ORG_GRANTS: OrgModelGrant[] = [
  { provider: "anthropic", model_id: "claude-sonnet-4-6", visibility: "global", businessUnitIds: [] },
  { provider: "anthropic", model_id: "claude-haiku-4-5", visibility: "global", businessUnitIds: [] },
  {
    provider: "anthropic",
    model_id: "claude-opus-4-7",
    visibility: "specific",
    businessUnitIds: ["ws_payments"],
  },
  {
    provider: "openai",
    model_id: "gpt-5.1",
    visibility: "specific",
    businessUnitIds: ["ws_lending", "ws_payments"],
  },
];

const entryKey = (e: { provider: string; model_id: string }) => `${e.provider}::${e.model_id}`;

export function getOrgModelGrants(): OrgModelGrant[] {
  return ORG_GRANTS.map((g) => ({ ...g, businessUnitIds: [...g.businessUnitIds] }));
}

/** Replace the whole grant list. Duplicates collapse, and a `global` grant's
 *  unit list is cleared — carrying stale unit ids on a grant that ignores them
 *  is how "why is Payments still listed?" bugs start. */
export function setOrgModelGrants(grants: OrgModelGrant[]): OrgModelGrant[] {
  const seen = new Set<string>();
  ORG_GRANTS = grants.filter(isNamedEntry).flatMap((g) => {
    const k = entryKey(g);
    if (seen.has(k)) return [];
    seen.add(k);
    return [
      {
        provider: g.provider,
        model_id: g.model_id,
        visibility: g.visibility,
        businessUnitIds: g.visibility === "specific" ? [...new Set(g.businessUnitIds)] : [],
      },
    ];
  });
  return getOrgModelGrants();
}

/**
 * What a Business Unit may use: derived from the grants, never stored.
 *
 * Nothing writes a per-BU allow-list any more, so a unit's set cannot drift
 * out of sync with the org's — revoking a grant removes it from every unit in
 * the same instant, including from projects that had selected it (see
 * `getProjectModelSelection`, which clamps against this).
 */
export function getBuAllowedModels(workspaceId: string): ModelAllowEntry[] {
  return ORG_GRANTS.filter((g) => grantReaches(g, workspaceId)).map((g) => ({
    provider: g.provider,
    model_id: g.model_id,
  }));
}

/**
 * Grant a Business Unit exactly `entries` — the Org Admin's per-unit control,
 * used when creating a unit and from its management page.
 *
 * Only `specific` grants are touched. A `global` model reaches every unit by
 * definition, so it can be neither granted nor revoked here; unchecking one
 * would have to either silently do nothing or quietly demote it for the whole
 * organization, and both are worse than the UI simply not offering it (which
 * it doesn't — global models render as already-included, not as choices).
 *
 * An entry naming a model with no grant at all is ignored: a unit cannot be
 * given something the organization has not approved.
 */
export function setBuModelGrants(workspaceId: string, entries: ModelAllowEntry[]): ModelAllowEntry[] {
  const wanted = new Set(entries.map(entryKey));
  for (const grant of ORG_GRANTS) {
    if (grant.visibility !== "specific") continue;
    const has = grant.businessUnitIds.includes(workspaceId);
    const want = wanted.has(entryKey(grant));
    if (want && !has) grant.businessUnitIds.push(workspaceId);
    else if (!want && has) {
      grant.businessUnitIds = grant.businessUnitIds.filter((id) => id !== workspaceId);
    }
  }
  return getBuAllowedModels(workspaceId);
}

/**
 * The BU-facing view: what this unit has, and what (if anything) still needs a
 * key before it can be used. See `ModelAvailability`.
 */
export function getBuModelAvailability(workspaceId: string): ModelAvailability[] {
  return ORG_GRANTS.filter((g) => grantReaches(g, workspaceId)).map((g) => ({
    provider: g.provider,
    model_id: g.model_id,
    visibility: g.visibility,
    centrallyCredentialed: hasCredentialFor(null, g.provider, g.model_id),
    locallyCredentialed: hasCredentialFor(workspaceId, g.provider, g.model_id),
  }));
}

/**
 * Is there a usable credential for this provider+model at the given level?
 *
 * "Usable" excludes a connection still pending its BU Admin's approval and one
 * whose offering is disabled — both are onboarded but neither can serve a run,
 * and reporting them as covered would hide the very credential gap this
 * answers.
 */
function hasCredentialFor(
  workspaceId: string | null,
  provider: string,
  modelId: string,
): boolean {
  return PROVIDERS.some(
    (p) =>
      p.workspaceId === workspaceId &&
      p.approvalStatus === "active" &&
      p.provider === provider &&
      p.offerings.some((o) => o.enabled && o.model_id === modelId),
  );
}

// ───────────────────────────────────────────────────────────────────────
//  Project selection — the fourth tier
// ───────────────────────────────────────────────────────────────────────

/**
 * What a project actually *uses*, chosen by its Project Admin from the models
 * its Business Unit inherited.
 *
 * The three tiers above are governance: they decide what a project is
 * *permitted* to use. This one is operational — an allow-list of five models
 * a team never touches is noise, and a run has to resolve exactly one default.
 * Org and BU Admins never edit this; Project Admins never edit the tiers above
 * it.
 *
 * Nothing is stored until a Project Admin makes a choice. Absence means
 * "inherit everything the BU allows", which is the sane default for a new
 * project and keeps this store empty for projects that never customise —
 * see `getProjectModelSelection`.
 */
interface ProjectSelectionRow {
  projectId: string;
  selected: ModelAllowEntry[];
  /** `provider::model_id` of the project's default, or null to use the first. */
  defaultKey: string | null;
}

const PROJECT_SELECTION: ProjectSelectionRow[] = [];

const modelKey = (e: ModelAllowEntry) => `${e.provider}::${e.model_id}`;

export interface ProjectModelSelection {
  /** Everything the project's Business Unit permits — the inherited set. */
  inherited: ModelAllowEntry[];
  /** The Business Unit the inherited set came from, for attribution. */
  inheritedFrom: { id: string; name: string } | null;
  /** The subset the Project Admin turned on. */
  selected: ModelAllowEntry[];
  /** True while the project has made no choice and is using all of `inherited`. */
  usingDefaults: boolean;
  /** `provider::model_id` of the project default, or null when nothing is selected. */
  defaultKey: string | null;
}

/**
 * A project's effective model selection. `workspaceId` is the project's
 * parent Business Unit — the caller resolves it, so this module keeps its
 * one-way dependency on workspace-fixtures and never imports project data.
 */
export function getProjectModelSelection(
  projectId: string,
  workspaceId: string | null,
): ProjectModelSelection {
  const inherited = workspaceId ? getBuAllowedModels(workspaceId) : [];
  const workspace = workspaceId ? getWorkspace(workspaceId) : undefined;
  const inheritedFrom = workspace ? { id: workspace.id, name: workspace.displayName } : null;

  const row = PROJECT_SELECTION.find((r) => r.projectId === projectId);
  const inheritedKeys = new Set(inherited.map(modelKey));

  // A model the BU has since revoked must drop out of the project's selection
  // even though the stored row still names it — the tier above always wins.
  const selected = row ? row.selected.filter((e) => inheritedKeys.has(modelKey(e))) : inherited;

  const storedDefault = row?.defaultKey;
  const defaultKey =
    storedDefault && selected.some((e) => modelKey(e) === storedDefault)
      ? storedDefault
      : (selected[0] ? modelKey(selected[0]) : null);

  return { inherited, inheritedFrom, selected, usingDefaults: !row, defaultKey };
}

/** Persist a Project Admin's choice, clamped to what the BU permits. */
export function setProjectModelSelection(
  projectId: string,
  workspaceId: string | null,
  input: { selected: ModelAllowEntry[]; defaultKey?: string | null },
): ProjectModelSelection {
  const inheritedKeys = new Set(getBuAllowedModels(workspaceId ?? "").map(modelKey));
  const selected = input.selected.filter((e) => inheritedKeys.has(modelKey(e)));

  const row = PROJECT_SELECTION.find((r) => r.projectId === projectId);
  const defaultKey =
    input.defaultKey !== undefined ? input.defaultKey : (row?.defaultKey ?? null);

  if (row) {
    row.selected = selected;
    row.defaultKey = defaultKey;
  } else {
    PROJECT_SELECTION.push({ projectId, selected, defaultKey });
  }
  return getProjectModelSelection(projectId, workspaceId);
}

// ───────────────────────────────────────────────────────────────────────
//  Onboarded providers (credentialed connections)
// ───────────────────────────────────────────────────────────────────────

let nextProviderSeq = 2;
const PROVIDERS: ModelProvider[] = [
  {
    id: "prov_anthropic",
    provider: "anthropic",
    display_name: "Anthropic",
    status: "valid",
    api_base: null,
    is_custom: false,
    last_verified_at: new Date(Date.now() - 3 * 24 * 3600 * 1000).toISOString(),
    created_at: new Date(Date.now() - 30 * 24 * 3600 * 1000).toISOString(),
    workspaceId: null,
    approvalStatus: "active",
    approvalDecidedBy: null,
    approvalDecidedAt: null,
    approvalReason: null,
    offerings: [
      { id: "off_anthropic_sonnet", provider_id: "prov_anthropic", model_id: "claude-sonnet-4-6", enabled: true, is_default: true, input_price_per_million: 3, output_price_per_million: 15, rpm_limit: null, tpm_limit: null, cost_limit_usd: null },
      { id: "off_anthropic_opus", provider_id: "prov_anthropic", model_id: "claude-opus-4-7", enabled: true, is_default: false, input_price_per_million: 15, output_price_per_million: 75, rpm_limit: null, tpm_limit: null, cost_limit_usd: null },
      { id: "off_anthropic_haiku", provider_id: "prov_anthropic", model_id: "claude-haiku-4-5", enabled: true, is_default: false, input_price_per_million: 0.8, output_price_per_million: 4, rpm_limit: null, tpm_limit: null, cost_limit_usd: null },
    ],
  },
];

export interface ModelProviderCreator {
  role: PlatformRole | null;
  displayName: string;
}

export interface ModelSpecInput {
  model_id: string;
  input_price_per_million?: number | null;
  output_price_per_million?: number | null;
  rpm_limit?: number | null;
  tpm_limit?: number | null;
  cost_limit_usd?: number | null;
}

export interface CreateModelProviderInput {
  provider: string;
  display_name: string;
  api_base?: string;
  models?: ModelSpecInput[];
  enabled_models?: string[];
  /** null = org-wide (only meaningful for an Org Admin's own onboarding). */
  workspaceId: string | null;
  /**
   * How far the models onboarded here should reach. Org-wide onboarding only:
   * an Org Admin bringing a key is also deciding who may use what it unlocks,
   * so the grant is written in the same act rather than left as a second step
   * someone forgets — a credentialed model nobody was granted is invisible
   * everywhere, which reads as the credential having failed.
   *
   * Omitted (or set on a unit-scoped onboarding) leaves the grants untouched.
   */
  visibility?: GrantVisibility;
  businessUnitIds?: string[];
}

/** Governance mirrors createProjectRecord (lib/mock/project-fixtures.ts):
 *  Org Admin and BU Admin onboard directly; a Project Admin's onboarding
 *  needs that BU's Admin to approve it before its offerings count as usable. */
export function createModelProvider(
  input: CreateModelProviderInput,
  creator: ModelProviderCreator,
): ModelProvider {
  const id = `prov_${nextProviderSeq++}`;
  const now = new Date().toISOString();
  const requested: ModelSpecInput[] =
    input.models && input.models.length > 0
      ? input.models
      : (input.enabled_models ?? []).map((model_id) => ({ model_id }));

  // A unit-scoped onboarding may only credential models the Org Admin granted
  // that unit. The dialogs already restrict what can be picked, but the rule
  // belongs here too: a hand-rolled request must not be able to smuggle in a
  // model the organization never approved. Org-wide onboarding (workspaceId
  // null) is the Org Admin defining the catalogue and is not clamped.
  const specs = input.workspaceId
    ? (() => {
        const granted = new Set(getBuAllowedModels(input.workspaceId).map(entryKey));
        return requested.filter((m) => granted.has(`${input.provider}::${m.model_id}`));
      })()
    : requested;

  const offerings: ModelOffering[] = specs.map((m, i) => ({
    id: `off_${id}_${i}`,
    provider_id: id,
    model_id: m.model_id,
    enabled: true,
    is_default: false,
    input_price_per_million: m.input_price_per_million ?? null,
    output_price_per_million: m.output_price_per_million ?? null,
    rpm_limit: m.rpm_limit ?? null,
    tpm_limit: m.tpm_limit ?? null,
    cost_limit_usd: m.cost_limit_usd ?? null,
  }));

  const needsApproval = creator.role === "project_admin";

  const created: ModelProvider = {
    id,
    provider: input.provider,
    display_name: input.display_name,
    status: "unverified",
    api_base: input.api_base ?? null,
    is_custom: !CATALOG.some((c) => c.provider === input.provider),
    last_verified_at: null,
    created_at: now,
    offerings,
    workspaceId: input.workspaceId,
    approvalStatus: needsApproval ? "pending_approval" : "active",
    approvalDecidedBy: null,
    approvalDecidedAt: null,
    approvalReason: null,
  };
  PROVIDERS.push(created);

  // Org-wide onboarding also grants what it credentialed, at the visibility
  // the admin chose. An existing grant is respected rather than overwritten:
  // re-keying a provider is not a decision about who may use its models.
  if (input.workspaceId === null && input.visibility) {
    const next = getOrgModelGrants();
    for (const spec of specs) {
      const key = `${input.provider}::${spec.model_id}`;
      if (next.some((g) => entryKey(g) === key)) continue;
      next.push({
        provider: input.provider,
        model_id: spec.model_id,
        visibility: input.visibility,
        businessUnitIds: input.visibility === "specific" ? (input.businessUnitIds ?? []) : [],
      });
    }
    setOrgModelGrants(next);
  }

  if (needsApproval) {
    const workspace = input.workspaceId ? getWorkspace(input.workspaceId) : undefined;
    createGovernanceApproval({
      type: "model_credential",
      workspaceId: input.workspaceId ?? "",
      workspaceName: workspace?.displayName ?? input.workspaceId ?? "",
      projectId: null,
      projectName: null,
      title: `New model credential: ${created.display_name}`,
      summary: `${creator.displayName} requested to onboard ${created.display_name} (${specs
        .map((s) => s.model_id)
        .join(", ")})${workspace ? ` for ${workspace.displayName}` : ""}.`,
      requestedBy: creator.displayName,
      targetRef: created.id,
    });
  }

  return created;
}

/** null = org-wide providers only; a string = that BU's scoped providers. */
export function listModelProviders(workspaceId: string | null): ModelProvider[] {
  return PROVIDERS.filter((p) => p.workspaceId === workspaceId);
}

/**
 * Every provider connection in the organization, org-wide and unit-scoped
 * together. `listModelProviders` filters to exactly one scope (`workspaceId ===
 * x`, where null means org-wide), which is right for the Model Management page
 * but cannot answer "how many does this organization have" — that needs both
 * tiers at once. Optionally narrowed to the units the caller may read.
 */
export function listAllModelProviders(allowedWorkspaceIds?: string[] | null): ModelProvider[] {
  if (allowedWorkspaceIds == null) return [...PROVIDERS];
  // Org-wide connections (workspaceId === null) belong to everyone in the org.
  return PROVIDERS.filter(
    (p) => p.workspaceId == null || allowedWorkspaceIds.includes(String(p.workspaceId)),
  );
}

export function getModelProvider(id: string): ModelProvider | undefined {
  return PROVIDERS.find((p) => p.id === id);
}

export function updateModelProvider(
  id: string,
  patch: { display_name?: string; enabled_models?: string[] },
): ModelProvider | undefined {
  const provider = PROVIDERS.find((p) => p.id === id);
  if (!provider) return undefined;
  if (patch.display_name !== undefined) provider.display_name = patch.display_name;
  if (patch.enabled_models !== undefined) {
    const allow = new Set(patch.enabled_models);
    for (const o of provider.offerings) o.enabled = allow.has(o.model_id);
  }
  return provider;
}

export function deleteModelProvider(id: string): boolean {
  const idx = PROVIDERS.findIndex((p) => p.id === id);
  if (idx === -1) return false;
  PROVIDERS.splice(idx, 1);
  return true;
}

export function verifyModelProvider(id: string): { id: string; status: ModelProviderStatus } | undefined {
  const provider = PROVIDERS.find((p) => p.id === id);
  if (!provider) return undefined;
  provider.status = "valid";
  provider.last_verified_at = new Date().toISOString();
  return { id: provider.id, status: provider.status };
}

export function setModelDefaultOffering(offeringId: string): void {
  for (const provider of PROVIDERS) {
    for (const offering of provider.offerings) {
      offering.is_default = offering.id === offeringId;
    }
  }
}

export function activateModelProvider(id: string, decidedBy: string): ModelProvider | undefined {
  const provider = PROVIDERS.find((p) => p.id === id);
  if (!provider) return undefined;
  provider.approvalStatus = "active";
  provider.approvalDecidedBy = decidedBy;
  provider.approvalDecidedAt = new Date().toISOString();
  return provider;
}

export function rejectModelProvider(id: string, decidedBy: string, reason?: string): ModelProvider | undefined {
  const provider = PROVIDERS.find((p) => p.id === id);
  if (!provider) return undefined;
  provider.approvalStatus = "rejected";
  provider.approvalDecidedBy = decidedBy;
  provider.approvalDecidedAt = new Date().toISOString();
  provider.approvalReason = reason ?? null;
  return provider;
}
