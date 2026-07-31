/**
 * Agent Studio prompt-profile store — a REAL per-tier store (was previously
 * a flat, scope-blind array of permanent nulls; every route reading/writing
 * it was also still on bffProxy/bffFetch, so none of this worked at all
 * without a live backend). Plain data + functions, server-safe (imported by
 * both the Next.js route handlers and the MSW handlers — see
 * [[msw-dual-runtime-mutation-rule]]). This is the DUMMY-DATA source; the
 * backend prompt-management service replaces the route-handler bodies, not
 * these shapes.
 *
 * Four-tier cascade (Org → Business Unit → Project → Personal): each tier
 * may publish its own active version per agent; a tier with none inherits
 * the nearest ancestor's active version (see resolveEffective). "Personal"
 * (`scope: "user"`) is a platform addition — every person's own override,
 * always editable immediately by them alone; the other three tiers are each
 * owned by exactly one role (lib/governance.ts::AGENT_DEFAULT_OWNER_ROLE) —
 * editing a tier you don't own creates a governance approval instead of
 * publishing directly (see lib/mock/governance-approval-fixtures.ts and the
 * `agent_default_*` GovernanceApprovalType values).
 */
import type {
  AgentProfileSummaryEntry,
  AgentProfileVersion,
  ProfileScope,
} from "@/lib/schemas/agent-profiles";

// Mirrors components/agent-studio/agents.ts AGENT_ORDER — all 13 agents
// Agent Studio manages prompts for (`code_review`, not the `review` phase
// enum), including the 5 track-specific ones (Tracks 3/4/5).
export const AGENT_IDS = [
  "requirements",
  "design",
  "development",
  "code_review",
  "security",
  "testing",
  "deployment",
  "documentation",
  "discovery",
  "strategy",
  "migration_mapping",
  "validation",
  "data_engineering",
] as const;

interface ProfileVersionRecord {
  id: string;
  scope: ProfileScope;
  /** null only for scope "org" (org-wide, singleton). */
  scopeId: string | null;
  agentId: string;
  version: number;
  isActive: boolean;
  promptPrepend: string;
  promptAppend: string;
  outputContractExtra: string;
  createdBy: string;
  createdAt: string;
  updatedAt: string | null;
  publishedBy: string | null;
  publishedAt: string | null;
}

let nextId = 1;
const VERSIONS: ProfileVersionRecord[] = [];

const scopeKeyId = (scopeId: string | null | undefined) => scopeId ?? "";

function versionsFor(agentId: string, scope: ProfileScope, scopeId: string | null): ProfileVersionRecord[] {
  return VERSIONS.filter(
    (v) => v.agentId === agentId && v.scope === scope && scopeKeyId(v.scopeId) === scopeKeyId(scopeId),
  );
}

function activeVersionFor(
  agentId: string,
  scope: ProfileScope,
  scopeId: string | null,
): ProfileVersionRecord | undefined {
  return versionsFor(agentId, scope, scopeId).find((v) => v.isActive);
}

function toApi(v: ProfileVersionRecord): AgentProfileVersion {
  return {
    id: v.id,
    version: v.version,
    is_active: v.isActive,
    prompt_prepend: v.promptPrepend,
    prompt_append: v.promptAppend,
    output_contract_extra: v.outputContractExtra,
    created_by: v.createdBy,
    created_at: v.createdAt,
    updated_at: v.updatedAt,
    published_by: v.publishedBy,
    published_at: v.publishedAt,
  };
}

/** The chain a tier inherits through, nearest first — e.g. requesting
 *  "user" scope with a full context walks user → project → workspace → org.
 *  A tier missing from `ctx` (e.g. no project selected) just stops there. */
export interface ProfileChainContext {
  workspaceId?: string | null;
  projectId?: string | null;
  userId?: string | null;
}

function chainFor(
  scope: ProfileScope,
  scopeId: string | null,
  ctx: ProfileChainContext,
): { scope: ProfileScope; scopeId: string | null }[] {
  const chain: { scope: ProfileScope; scopeId: string | null }[] = [];
  const order: ProfileScope[] = ["user", "project", "workspace", "org"];
  const startIdx = order.indexOf(scope);
  for (let i = startIdx; i < order.length; i++) {
    const s = order[i]!;
    if (i === startIdx) {
      chain.push({ scope: s, scopeId });
      continue;
    }
    if (s === "org") {
      chain.push({ scope: "org", scopeId: null });
    } else if (s === "workspace") {
      if (!ctx.workspaceId) break;
      chain.push({ scope: "workspace", scopeId: ctx.workspaceId });
    } else if (s === "project") {
      if (!ctx.projectId) break;
      chain.push({ scope: "project", scopeId: ctx.projectId });
    } else if (s === "user") {
      if (!ctx.userId) break;
      chain.push({ scope: "user", scopeId: ctx.userId });
    }
  }
  return chain;
}

/** Walks the chain from the requested tier upward; returns the nearest
 *  ancestor's active content, or null if nothing in the chain has published
 *  anything (the agent runs on its pure vendor prompt everywhere). */
function resolveEffective(
  agentId: string,
  scope: ProfileScope,
  scopeId: string | null,
  ctx: ProfileChainContext,
): { active: ProfileVersionRecord | undefined; inheritedFrom: ProfileScope | null } {
  const chain = chainFor(scope, scopeId, ctx);
  for (const link of chain) {
    const active = activeVersionFor(agentId, link.scope, link.scopeId);
    if (active) return { active, inheritedFrom: link.scope === scope ? null : link.scope };
  }
  return { active: undefined, inheritedFrom: null };
}

export function getAgentProfileSummary(
  scope: ProfileScope,
  scopeId: string | null,
  ctx: ProfileChainContext,
): AgentProfileSummaryEntry[] {
  return AGENT_IDS.map((agent_id) => {
    const own = versionsFor(agent_id, scope, scopeId);
    const latest = own.reduce((max, v) => Math.max(max, v.version), 0);
    const { active, inheritedFrom } = resolveEffective(agent_id, scope, scopeId, ctx);
    return {
      agent_id,
      active_version: activeVersionFor(agent_id, scope, scopeId)?.version ?? null,
      latest_version: latest || null,
      draft_count: own.filter((v) => !v.isActive).length,
      updated_at: own.length ? (own[own.length - 1]!.updatedAt ?? own[own.length - 1]!.createdAt) : null,
      active: active
        ? {
            prompt_prepend: active.promptPrepend,
            prompt_append: active.promptAppend,
            output_contract_extra: active.outputContractExtra,
          }
        : null,
      inherited_from: inheritedFrom,
    };
  });
}

export function listVersions(agentId: string, scope: ProfileScope, scopeId: string | null): AgentProfileVersion[] {
  return versionsFor(agentId, scope, scopeId)
    .slice()
    .sort((a, b) => b.version - a.version)
    .map(toApi);
}

export function createDraft(input: {
  agentId: string;
  scope: ProfileScope;
  scopeId: string | null;
  promptPrepend: string;
  promptAppend: string;
  outputContractExtra: string;
  createdBy: string;
}): AgentProfileVersion {
  const existing = versionsFor(input.agentId, input.scope, input.scopeId);
  const version = existing.reduce((max, v) => Math.max(max, v.version), 0) + 1;
  const now = new Date().toISOString();
  const record: ProfileVersionRecord = {
    id: `profile_${nextId++}`,
    scope: input.scope,
    scopeId: input.scopeId,
    agentId: input.agentId,
    version,
    isActive: false,
    promptPrepend: input.promptPrepend,
    promptAppend: input.promptAppend,
    outputContractExtra: input.outputContractExtra,
    createdBy: input.createdBy,
    createdAt: now,
    updatedAt: null,
    publishedBy: null,
    publishedAt: null,
  };
  VERSIONS.push(record);
  return toApi(record);
}

/** `publishedBy` is the acting user — the tier owner when they publish their
 *  own draft directly, or the approver's name when this resolves a
 *  governance approval (see agentDefaultApprovalType, lib/governance.ts). */
export function publishVersion(id: string, publishedBy?: string): AgentProfileVersion | undefined {
  const record = VERSIONS.find((v) => v.id === id);
  if (!record) return undefined;
  for (const v of versionsFor(record.agentId, record.scope, record.scopeId)) {
    v.isActive = v.id === id;
  }
  const now = new Date().toISOString();
  record.updatedAt = now;
  if (publishedBy) {
    record.publishedBy = publishedBy;
    record.publishedAt = now;
  }
  return toApi(record);
}

export function unpublishVersion(id: string): AgentProfileVersion | undefined {
  const record = VERSIONS.find((v) => v.id === id);
  if (!record) return undefined;
  record.isActive = false;
  record.updatedAt = new Date().toISOString();
  return toApi(record);
}

/**
 * The resolved layer stack for the Composition Preview panel — vendor base
 * (locked) → each ancestor tier that has an active version, outermost
 * (org) to innermost → the currently-edited (unsaved) draft content last,
 * since it's the most specific and always wins at run time once published.
 */
export function buildPreview(
  agentId: string,
  scope: ProfileScope,
  scopeId: string | null,
  ctx: ProfileChainContext,
  draft: { promptPrepend: string; promptAppend: string; outputContractExtra: string },
): { layers: { name: string; source: string; locked: boolean; content: string | null; chars: number }[] } {
  const chain = chainFor(scope, scopeId, ctx).slice().reverse(); // org..→..requested tier
  const layers: { name: string; source: string; locked: boolean; content: string | null; chars: number }[] = [
    { name: "Vendor base prompt", source: "vendor", locked: true, content: null, chars: 0 },
  ];
  for (const link of chain) {
    const active = activeVersionFor(agentId, link.scope, link.scopeId);
    if (!active) continue;
    const content = [active.promptPrepend, active.promptAppend].filter(Boolean).join("\n\n");
    layers.push({
      name: link.scope,
      source: link.scope,
      locked: false,
      content,
      chars: content.length,
    });
  }
  const draftContent = [draft.promptPrepend, draft.promptAppend].filter(Boolean).join("\n\n");
  if (draftContent.trim().length > 0) {
    layers.push({ name: "draft", source: "draft", locked: false, content: draftContent, chars: draftContent.length });
  }
  return { layers };
}
