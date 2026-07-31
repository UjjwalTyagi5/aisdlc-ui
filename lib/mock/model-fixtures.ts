/**
 * Dummy model catalogue for the model picker — plain data, server-safe
 * (imported by the app/api/model/options route handler). This is the
 * DUMMY-DATA source; the LiteLLM-backed catalog (Phase 2) replaces the route
 * handler body, not these shapes.
 *
 * Also the DUMMY-DATA source for the three-tier model cascade: the full
 * catalog below → an Org Admin's org-wide allow-list subset → a BU Admin's
 * further subset for their business unit → an actual credentialed
 * ModelProvider a Project Admin onboards from that BU subset (pending BU
 * Admin approval — the governance-approval plumbing this reuses is in
 * lib/mock/governance-approval-fixtures.ts).
 */
import type {
  CatalogProvider,
  ModelAllowEntry,
  ModelOffering,
  ModelOption,
  ModelProvider,
  ModelProviderStatus,
} from "@/lib/schemas/model";
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
//  Three-tier catalogue cascade
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

function inCatalog(entry: ModelAllowEntry): boolean {
  const p = CATALOG.find((c) => c.provider === entry.provider);
  return !!p?.models.some((m) => m.model_id === entry.model_id);
}

/** Org-wide allow-list, curated by an Org Admin — seeded with a starter set. */
let ORG_ALLOWED: ModelAllowEntry[] = [
  { provider: "anthropic", model_id: "claude-sonnet-4-6" },
  { provider: "anthropic", model_id: "claude-opus-4-7" },
  { provider: "anthropic", model_id: "claude-haiku-4-5" },
  { provider: "openai", model_id: "gpt-5.1" },
];

export function getOrgAllowedModels(): ModelAllowEntry[] {
  return ORG_ALLOWED;
}

export function setOrgAllowedModels(entries: ModelAllowEntry[]): ModelAllowEntry[] {
  ORG_ALLOWED = entries.filter(inCatalog);
  return ORG_ALLOWED;
}

/** Per-BU allow-list, curated by that BU's Admin — must stay a subset of
 *  ORG_ALLOWED (enforced on write, see setBuAllowedModels). */
let BU_ALLOWED: { workspaceId: string; provider: string; model_id: string }[] = [
  { workspaceId: "ws_lending", provider: "anthropic", model_id: "claude-sonnet-4-6" },
  { workspaceId: "ws_lending", provider: "anthropic", model_id: "claude-haiku-4-5" },
];

export function getBuAllowedModels(workspaceId: string): ModelAllowEntry[] {
  return BU_ALLOWED.filter((e) => e.workspaceId === workspaceId).map((e) => ({
    provider: e.provider,
    model_id: e.model_id,
  }));
}

export function setBuAllowedModels(workspaceId: string, entries: ModelAllowEntry[]): ModelAllowEntry[] {
  const orgAllowedSet = new Set(ORG_ALLOWED.map((e) => `${e.provider}::${e.model_id}`));
  const next = entries.filter((e) => orgAllowedSet.has(`${e.provider}::${e.model_id}`));
  BU_ALLOWED = [...BU_ALLOWED.filter((e) => e.workspaceId !== workspaceId), ...next.map((e) => ({ workspaceId, ...e }))];
  return getBuAllowedModels(workspaceId);
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
  const specs: ModelSpecInput[] =
    input.models && input.models.length > 0
      ? input.models
      : (input.enabled_models ?? []).map((model_id) => ({ model_id }));

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
