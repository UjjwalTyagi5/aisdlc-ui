/**
 * Agent Studio skills store — a REAL per-tier store, same shape as
 * agent-profile-fixtures.ts (Behavior tab). This is the DUMMY-DATA source
 * for the `/agent-skills` router; the backend skills service replaces the
 * route-handler bodies, not these shapes (see [[msw-dual-runtime-mutation-rule]]).
 *
 * Only "custom" skills are stored here — this frontend ships no vendor
 * (built-in) skills yet, so the merged list is always custom-only until a
 * real catalogue exists.
 */
import type {
  SkillDetail,
  SkillListItem,
  SkillScope,
  SkillVersion,
} from "@/lib/schemas/agent-skills";

interface SkillVersionRecord {
  version: number;
  displayName: string;
  description: string;
  whenToUse: string;
  body: string;
  createdBy: string;
  createdAt: string;
}

interface SkillRecord {
  skillKey: string;
  agentId: string;
  scope: SkillScope;
  scopeId: string | null;
  enabled: boolean;
  deleted: boolean;
  activeVersion: number;
  versions: SkillVersionRecord[]; // newest first
  createdBy: string;
  createdAt: string;
  updatedAt: string | null;
}

const SKILLS: SkillRecord[] = [];

const scopeKeyId = (scopeId: string | null | undefined) => scopeId ?? "";

function findSkill(
  skillKey: string,
  agentId: string,
  scope: SkillScope,
  scopeId: string | null,
): SkillRecord | undefined {
  return SKILLS.find(
    (s) =>
      s.skillKey === skillKey &&
      s.agentId === agentId &&
      s.scope === scope &&
      scopeKeyId(s.scopeId) === scopeKeyId(scopeId) &&
      !s.deleted,
  );
}

function activeContent(record: SkillRecord): SkillVersionRecord {
  return record.versions.find((v) => v.version === record.activeVersion) ?? record.versions[0]!;
}

function toListItem(record: SkillRecord): SkillListItem {
  const active = activeContent(record);
  return {
    origin: "custom",
    skill_key: record.skillKey,
    agent_id: record.agentId,
    display_name: active.displayName,
    description: active.description || null,
    when_to_use: active.whenToUse || null,
    runtime: "llm",
    enabled: record.enabled,
    editable: true,
    deletable: true,
    version: record.activeVersion,
    active_version: record.activeVersion,
    origin_scope: record.scope,
  };
}

function toDetail(record: SkillRecord): SkillDetail {
  const active = activeContent(record);
  return {
    ...toListItem(record),
    body: active.body,
    created_by: record.createdBy,
    created_at: record.createdAt,
    updated_at: record.updatedAt,
  };
}

export function listAgentSkills(
  agentId: string,
  scope: SkillScope,
  scopeId: string | null,
): SkillListItem[] {
  return SKILLS.filter(
    (s) =>
      s.agentId === agentId &&
      s.scope === scope &&
      scopeKeyId(s.scopeId) === scopeKeyId(scopeId) &&
      !s.deleted,
  ).map(toListItem);
}

export function getAgentSkill(
  skillKey: string,
  agentId: string,
  scope: SkillScope,
  scopeId: string | null,
): SkillDetail | undefined {
  const record = findSkill(skillKey, agentId, scope, scopeId);
  return record ? toDetail(record) : undefined;
}

export function createAgentSkill(input: {
  agentId: string;
  scope: SkillScope;
  scopeId: string | null;
  skillKey: string;
  displayName: string;
  description: string;
  whenToUse: string;
  body: string;
  createdBy: string;
}): SkillDetail {
  const now = new Date().toISOString();
  const record: SkillRecord = {
    skillKey: input.skillKey,
    agentId: input.agentId,
    scope: input.scope,
    scopeId: input.scopeId,
    enabled: true,
    deleted: false,
    activeVersion: 1,
    versions: [
      {
        version: 1,
        displayName: input.displayName,
        description: input.description,
        whenToUse: input.whenToUse,
        body: input.body,
        createdBy: input.createdBy,
        createdAt: now,
      },
    ],
    createdBy: input.createdBy,
    createdAt: now,
    updatedAt: null,
  };
  SKILLS.push(record);
  return toDetail(record);
}

export function updateAgentSkill(
  skillKey: string,
  agentId: string,
  scope: SkillScope,
  scopeId: string | null,
  input: { displayName: string; description: string; whenToUse: string; body: string; updatedBy: string },
): SkillDetail | undefined {
  const record = findSkill(skillKey, agentId, scope, scopeId);
  if (!record) return undefined;
  const now = new Date().toISOString();
  const version = record.versions.reduce((max, v) => Math.max(max, v.version), 0) + 1;
  record.versions.unshift({
    version,
    displayName: input.displayName,
    description: input.description,
    whenToUse: input.whenToUse,
    body: input.body,
    createdBy: input.updatedBy,
    createdAt: now,
  });
  record.activeVersion = version;
  record.updatedAt = now;
  return toDetail(record);
}

export function toggleAgentSkill(
  skillKey: string,
  agentId: string,
  scope: SkillScope,
  scopeId: string | null,
  enabled: boolean,
): boolean {
  const record = findSkill(skillKey, agentId, scope, scopeId);
  if (!record) return false;
  record.enabled = enabled;
  return true;
}

export function deleteAgentSkill(
  skillKey: string,
  agentId: string,
  scope: SkillScope,
  scopeId: string | null,
): boolean {
  const record = findSkill(skillKey, agentId, scope, scopeId);
  if (!record) return false;
  record.deleted = true;
  return true;
}

export function listAgentSkillVersions(
  skillKey: string,
  agentId: string,
  scope: SkillScope,
  scopeId: string | null,
): SkillVersion[] {
  const record = findSkill(skillKey, agentId, scope, scopeId);
  if (!record) return [];
  return record.versions
    .slice()
    .sort((a, b) => b.version - a.version)
    .map((v) => ({
      version: v.version,
      is_active: v.version === record.activeVersion,
      display_name: v.displayName,
      created_by: v.createdBy,
      created_at: v.createdAt,
    }));
}

// ───────── Seed data ─────────
//
// The Skills tab is Business-Unit-scoped (see SkillsTab's SCOPE = "workspace"
// constant). Seeded only into Lending — the worked example lives in one
// business unit, not duplicated across every one, so it's obvious the data
// is per-BU rather than a global default.

const SEED_WORKSPACES = ["ws_lending"];
const SEED_CREATED_BY = "Ada Lovelace";
const SEED_CREATED_AT = "2026-06-02T09:00:00.000Z";

function seedSkill(
  workspaceId: string,
  agentId: string,
  skillKey: string,
  displayName: string,
  description: string,
  whenToUse: string,
  body: string,
) {
  SKILLS.push({
    skillKey,
    agentId,
    scope: "workspace",
    scopeId: workspaceId,
    enabled: true,
    deleted: false,
    activeVersion: 1,
    versions: [
      {
        version: 1,
        displayName,
        description,
        whenToUse,
        body,
        createdBy: SEED_CREATED_BY,
        createdAt: SEED_CREATED_AT,
      },
    ],
    createdBy: SEED_CREATED_BY,
    createdAt: SEED_CREATED_AT,
    updatedAt: null,
  });
}

const REQUIREMENTS_BODY = `# Azure DevOps story format

Every user story this agent creates in Azure DevOps must follow this structure, so stories are consistent, estimable, and traceable back to the epic they belong to.

## Title
\`<Epic short name> — <concise outcome, verb-first>\`

Example: \`Onboarding — Capture customer KYC documents\`

## User story
As a **<role>**
I want **<capability>**
So that **<business outcome>**

## Acceptance criteria
At least two criteria, each written as Given/When/Then:

- Given <context>, when <action>, then <expected result>

## Definition of done
- [ ] Acceptance criteria met and demoed
- [ ] Unit + integration tests added
- [ ] No new Security or Compliance findings
- [ ] Documentation updated if behavior changed

## Required fields

| Field | Rule |
|---|---|
| Area Path | The owning team's Azure DevOps area |
| Iteration | Current or next sprint only — never backlog-only |
| Story Points | Fibonacci (1, 2, 3, 5, 8, 13) |
| Priority | 1 (Critical) – 4 (Low) |
| Tags | At least one of: \`frontend\`, \`backend\`, \`infra\`, \`data\` |
`;

const DESIGN_BODY = `# Design doc structure

Every feature design must be captured as a short design document before development starts, using this structure.

## 1. Context
What problem are we solving, and why now? Link back to the requirement or story it traces to.

## 2. Goals / non-goals
- Goals: what this design must achieve
- Non-goals: what's explicitly out of scope

## 3. Proposed design
- Components/services touched and how they connect
- Data model changes
- API contract changes (new/changed request and response shapes)
- Key sequence — what calls what, in order

## 4. Alternatives considered
At least one alternative approach, and why it was rejected.

## 5. Risks & mitigations
A short table: risk → likelihood → mitigation.

## 6. Rollout plan
- Feature flag / staged rollout approach
- Backward-compatibility notes
- Rollback plan

## When to use
Use this structure for any design that introduces a new API, a new data model, or changes how two existing services talk to each other. Skip it for pure UI copy or styling changes.
`;

const DEVELOPMENT_BODY = `# Commit & PR conventions

## Commit messages
Use Conventional Commits: \`<type>(<scope>): <summary>\`

Types: \`feat\`, \`fix\`, \`refactor\`, \`test\`, \`docs\`, \`chore\`

Example: \`feat(onboarding): add KYC document upload step\`

- Summary in the imperative mood, under 72 characters
- Body explains *why*, not *what* — the diff already shows what changed

## Pull request description
Every PR must include:

### Summary
1–3 bullet points describing the change and why.

### Test plan
A checklist of what was tested manually, and which automated tests cover it.

### Screenshots
Before/after screenshots for any visual change.

## Branch naming
\`<type>/<short-description>\` — e.g. \`feat/kyc-upload\`, \`fix/session-timeout\`

## When to use
Apply these conventions to every commit and PR this agent raises, regardless of the size of the change.
`;

for (const workspaceId of SEED_WORKSPACES) {
  seedSkill(
    workspaceId,
    "requirements",
    "azure-devops-story-format",
    "Azure DevOps story format",
    "The required structure for user stories pushed to Azure DevOps.",
    "When creating or splitting a user story that will be pushed to Azure DevOps as a work item.",
    REQUIREMENTS_BODY,
  );
  seedSkill(
    workspaceId,
    "design",
    "design-doc-structure",
    "Design doc structure",
    "The standard sections a feature design document must cover.",
    "Before development starts on any feature introducing a new API, data model, or service-to-service change.",
    DESIGN_BODY,
  );
  seedSkill(
    workspaceId,
    "development",
    "commit-pr-conventions",
    "Commit & PR conventions",
    "Conventional-commit message format and the required PR description sections.",
    "On every commit and pull request this agent raises.",
    DEVELOPMENT_BODY,
  );
}
