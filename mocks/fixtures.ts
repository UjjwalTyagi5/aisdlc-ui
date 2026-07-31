import type {
  Artifact,
  AuditEvent,
  Connector,
  Project,
  ProjectId,
  Run,
  RunId,
  Step,
  TenantId,
  UserId,
} from "@/lib/schemas";
import { PHASE_LABEL } from "@/lib/agents";
import { agentsForTrack } from "@/lib/tracks";

const TENANT_ID = "ws_acme" as TenantId;
const USER_ID = "u_admin" as UserId;

function iso(daysAgo = 0, hoursAgo = 0, minutesAgo = 0): string {
  const d = new Date();
  d.setDate(d.getDate() - daysAgo);
  d.setHours(d.getHours() - hoursAgo);
  d.setMinutes(d.getMinutes() - minutesAgo);
  return d.toISOString();
}

const OWNERS = [
  { id: USER_ID, name: "Ada Lovelace", email: "ada@acme.test", initials: "AL" },
  { id: "u_member" as UserId, name: "Grace Hopper", email: "grace@acme.test", initials: "GH" },
];

/**
 * Projects across all five delivery tracks (PRD §6), named against the PRD's
 * own worked example — the "MegaBank" organization with Retail Banking,
 * Payments and Fraud business units (§12.1).
 *
 * Each project's `pipeline` uses only the stages its track actually runs, per
 * `lib/tracks.ts::agentsForTrack()` — a Track 4 project has no Design or Code
 * Review stage, and a Track 3 project has Discovery & Assessment and Strategy.
 */
export const PROJECTS: Project[] = [
  {
    id: "mobile-onboarding" as ProjectId,
    tenantId: TENANT_ID,
    name: "Mobile onboarding journey",
    slug: "mobile-onboarding",
    description:
      "Greenfield build of the retail current-account onboarding flow, including KYC capture and decisioning.",
    workspaceId: "ws_lending",
    approvalStatus: "active",
    deliveryStatus: "in_progress",
    template: "web_app",
    track: "greenfield",
    archived: false,
    owners: OWNERS,
    pipeline: [
      { phase: "requirements", status: "approved", updatedAt: iso(6) },
      { phase: "design", status: "approved", updatedAt: iso(4) },
      { phase: "development", status: "running", updatedAt: iso(0, 0, 15) },
      { phase: "review", status: "queued" },
      { phase: "security", status: "queued" },
      { phase: "testing", status: "queued" },
      { phase: "deployment", status: "queued" },
      { phase: "documentation", status: "queued" },
    ],
    monthlyBudgetUsd: 4000,
    monthlySpendUsd: 2680,
    lastActivityAt: iso(0, 0, 4),
    createdAt: iso(21),
  },
  {
    id: "payments-api" as ProjectId,
    tenantId: TENANT_ID,
    name: "Payments API — SCA exemption defect",
    slug: "payments-api",
    description:
      "Enhancement track: strong-customer-authentication exemption incorrectly applied to recurring mandates. Entered at Requirements for impact triage.",
    workspaceId: "ws_payments",
    approvalStatus: "active",
    deliveryStatus: "in_progress",
    template: "microservice",
    track: "enhancement",
    archived: false,
    owners: [OWNERS[0]!],
    pipeline: [
      { phase: "requirements", status: "approved", updatedAt: iso(2) },
      // Design is skipped — this change needs no design update (PRD §8).
      { phase: "development", status: "approved", updatedAt: iso(1) },
      { phase: "review", status: "approved", updatedAt: iso(0, 20) },
      { phase: "security", status: "awaiting_approval", updatedAt: iso(0, 3) },
      { phase: "testing", status: "queued" },
      { phase: "deployment", status: "queued" },
      { phase: "documentation", status: "queued" },
    ],
    monthlyBudgetUsd: 2500,
    monthlySpendUsd: 1140,
    lastActivityAt: iso(0, 3),
    createdAt: iso(12),
  },
  {
    id: "core-ledger" as ProjectId,
    tenantId: TENANT_ID,
    name: "Core ledger — Java 8 to 21",
    slug: "core-ledger",
    description:
      "Code modernization of the end-of-life core ledger: Java 8 → 21, Spring Boot 2 → 3, strangler-fig sequencing by module risk.",
    workspaceId: "ws_lending",
    approvalStatus: "active",
    // Parked at the strategy gate — the pipeline says "awaiting_approval",
    // which is a machine state; a human deciding the whole project waits is
    // what `on_hold` records. The two are independent on purpose.
    deliveryStatus: "on_hold",
    template: "microservice",
    track: "modernization",
    archived: false,
    owners: OWNERS,
    pipeline: [
      { phase: "requirements", status: "approved", updatedAt: iso(30) },
      { phase: "discovery", status: "approved", updatedAt: iso(24) },
      { phase: "design", status: "approved", updatedAt: iso(18) },
      { phase: "strategy", status: "awaiting_approval", updatedAt: iso(0, 5) },
      { phase: "development", status: "queued" },
      { phase: "review", status: "queued" },
      { phase: "security", status: "queued" },
      { phase: "testing", status: "queued" },
      { phase: "deployment", status: "queued" },
      { phase: "documentation", status: "queued" },
    ],
    monthlyBudgetUsd: 9000,
    monthlySpendUsd: 7420,
    lastActivityAt: iso(0, 5),
    createdAt: iso(64),
  },
  {
    id: "recon-bots" as ProjectId,
    tenantId: TENANT_ID,
    name: "Reconciliation bots — A360 to UiPath",
    slug: "recon-bots",
    description:
      "Wave 2 RPA-to-RPA migration of 14 nightly reconciliation bots from Automation Anywhere A360 to UiPath.",
    workspaceId: "ws_platform",
    approvalStatus: "active",
    deliveryStatus: "in_progress",
    template: "blank",
    track: "rpa_infra",
    archived: false,
    owners: [OWNERS[1]!],
    pipeline: [
      { phase: "requirements", status: "approved", updatedAt: iso(15) },
      { phase: "discovery", status: "approved", updatedAt: iso(11) },
      { phase: "migration_mapping", status: "approved", updatedAt: iso(6) },
      { phase: "development", status: "running", updatedAt: iso(0, 0, 40) },
      { phase: "security", status: "queued" },
      { phase: "validation", status: "queued" },
      { phase: "deployment", status: "queued" },
      { phase: "documentation", status: "queued" },
    ],
    monthlyBudgetUsd: 6000,
    monthlySpendUsd: 5880,
    lastActivityAt: iso(0, 0, 40),
    createdAt: iso(38),
  },
  {
    id: "fraud-features" as ProjectId,
    tenantId: TENANT_ID,
    name: "Fraud feature store pipeline",
    slug: "fraud-features",
    description:
      "Track 5 data engineering: near-real-time feature pipeline from the card-auth stream into Snowflake for the fraud scoring model.",
    workspaceId: "ws_payments",
    approvalStatus: "active",
    deliveryStatus: "in_progress",
    template: "data_pipeline",
    track: "data_engineering",
    archived: false,
    owners: OWNERS,
    pipeline: [
      { phase: "requirements", status: "approved", updatedAt: iso(9) },
      { phase: "data_engineering", status: "awaiting_approval", updatedAt: iso(0, 2) },
      { phase: "design", status: "queued" },
      { phase: "development", status: "queued" },
      { phase: "review", status: "queued" },
      { phase: "security", status: "queued" },
      { phase: "testing", status: "queued" },
      { phase: "deployment", status: "queued" },
      { phase: "documentation", status: "queued" },
    ],
    monthlyBudgetUsd: 5000,
    monthlySpendUsd: 1960,
    lastActivityAt: iso(0, 2),
    createdAt: iso(17),
  },
  {
    id: "branch-teller" as ProjectId,
    tenantId: TENANT_ID,
    name: "Branch teller portal (retired)",
    slug: "branch-teller",
    description: "Decommissioned following the 2025 branch estate consolidation.",
    workspaceId: "ws_lending",
    approvalStatus: "active",
    deliveryStatus: "completed",
    template: "blank",
    track: "greenfield",
    archived: true,
    owners: [OWNERS[0]!],
    pipeline: [{ phase: "requirements", status: "rejected", updatedAt: iso(120) }],
    lastActivityAt: iso(120),
    createdAt: iso(210),
  },
  {
    // Seeded so a fresh Business Unit Admin sign-in has a real project-creation
    // approval to act on without needing a live cross-role session (this mock
    // frontend has no backend — signing in as a different role is a full page
    // reload, which resets all in-memory fixture mutations, so a governance
    // approval created live by a Project Admin session is invisible once you
    // sign in as the approving BU Admin in a separate session/tab). Mirrors
    // exactly what `createProjectRecord` produces for a Project-Admin-created
    // project pending approval — see lib/mock/governance-approval-fixtures.ts's
    // matching seed entry below.
    id: "regional-alerts" as ProjectId,
    tenantId: TENANT_ID,
    name: "Regional outage alerts",
    slug: "regional-alerts",
    description: "New Project Admin-requested project awaiting Business Unit Admin approval.",
    workspaceId: "ws_lending",
    approvalStatus: "pending_approval",
    deliveryStatus: "not_started",
    approvalDecidedBy: null,
    approvalDecidedAt: null,
    approvalReason: null,
    template: "web_app",
    track: "greenfield",
    archived: false,
    owners: [OWNERS[1]!],
    pipeline: [{ phase: "requirements", status: "draft", updatedAt: iso(0, 2) }],
    lastActivityAt: iso(0, 2),
    createdAt: iso(0, 2),
  },
];

/**
 * Runs are generated from each project's own track roster (PRD §6), so a
 * Track 4 project never shows a Design run it cannot have, and a Track 3
 * project shows real Discovery & Assessment and Strategy runs.
 */

const PHASE_BY_AGENT: Record<Run["agent"], Run["phase"]> = {
  orchestrator: "requirements",
  requirements: "requirements",
  design: "design",
  development: "development",
  review: "review",
  security: "security",
  testing: "testing",
  deployment: "deployment",
  documentation: "documentation",
  discovery: "discovery",
  strategy: "strategy",
  migration_mapping: "migration_mapping",
  validation: "validation",
  data_engineering: "data_engineering",
};

const STATUSES: Run["status"][] = [
  "approved",
  "awaiting_approval",
  "running",
  "failed",
  "merged",
  "rejected",
];

function makeRun(
  i: number,
  projectId: ProjectId,
  agent: Run["agent"],
  projectName: string,
  overrides: Partial<Run> = {},
): Run {
  const status = STATUSES[i % STATUSES.length]!;
  const startedAt = iso(0, i, 0);
  const completed = status === "running" || status === "awaiting_approval";
  const inTok = 10_000 + i * 3_200;
  const outTok = 2_400 + i * 700;
  const usd = Number((inTok * 3e-6 + outTok * 1.5e-5).toFixed(3));
  return {
    id: `run_${2140 + i}` as RunId,
    projectId,
    title: `${PHASE_LABEL[PHASE_BY_AGENT[agent]]} — ${projectName}`,
    agent,
    phase: PHASE_BY_AGENT[agent],
    status,
    trigger: i % 3 === 0 ? "webhook" : "manual",
    startedBy: USER_ID,
    startedAt,
    completedAt: completed ? null : iso(0, i - 1, 30),
    durationMs: completed ? null : (30 + i * 7) * 1000,
    cost: { usd, inputTokens: inTok, outputTokens: outTok },
    ...overrides,
  };
}

/**
 * One run per stage the project has actually reached, drawn from its own
 * track roster — so every project id here is real and every agent is one the
 * project's track runs.
 */
export const RUNS: Run[] = (() => {
  let i = 0;
  return PROJECTS.filter((p) => !p.archived).flatMap((project) => {
    const roster = agentsForTrack(project.track);
    // Only stages that have started — queued stages have produced no run yet.
    const started = project.pipeline
      .filter((e) => e.status !== "queued")
      .map((e) => e.phase)
      .filter((phase) => roster.includes(phase));

    return started.map((phase) =>
      makeRun(i++, project.id, phase as Run["agent"], project.name),
    );
  });
})();

export const STEPS: Step[] = Array.from({ length: 6 }).map((_, i) => ({
  id: `step_${i + 1}` as Step["id"],
  runId: RUNS[0]!.id,
  index: i,
  kind: i === 5 ? "hitl_wait" : i % 2 === 0 ? "llm_call" : "tool_call",
  agent: "requirements",
  // The Requirements agent is owned by the BA (PRD §14.7) — the baseline
  // sign-off waits on them, with the Project Admin as fallback.
  title: ["Plan run", "Fetch Jira issue", "Classify scope", "Draft stories", "Write back sub-tasks", "Await BA sign-off"][i]!,
  status: i === 5 ? "awaiting_approval" : "approved",
  startedAt: iso(0, 0, 30 - i * 3),
  completedAt: i === 5 ? null : iso(0, 0, 29 - i * 3),
  durationMs: i === 5 ? null : (5 + i) * 1000,
  cost:
    i % 2 === 0
      ? { usd: 0.02 * (i + 1), inputTokens: 4000 + i * 800, outputTokens: 900 + i * 200 }
      : undefined,
  model:
    i % 2 === 0
      ? { provider: "anthropic", id: "claude-sonnet-4-6", version: "2026-01-15", promptVersion: "v3" }
      : undefined,
  summary:
    [
      "Built run plan with 5 steps",
      "Pulled 3 issues matching label `agent`",
      "No out-of-scope tickets detected",
      "Drafted 4 stories with acceptance criteria",
      "Created 4 Jira sub-tasks, dry-run mode",
      "Awaiting PM sign-off",
    ][i],
}));

export const ARTIFACTS: Artifact[] = [
  {
    id: "art_story_1" as Artifact["id"],
    projectId: "mobile-onboarding" as ProjectId,
    runId: RUNS[0]!.id,
    type: "story",
    phase: "requirements",
    title: "As a platform engineer I can replay a failed ingest batch",
    version: 2,
    contentHash: "a".repeat(64),
    status: "approved",
    body: {
      kind: "story",
      title: "Replay failed ingest batch",
      description:
        "Platform engineers need a one-click replay for batches that failed due to transient downstream errors.",
      acceptanceCriteria: [
        {
          given: "a batch in status `failed` due to `downstream_5xx`",
          when: "the engineer clicks Replay",
          then: "the batch is re-enqueued with idempotency key preserved",
        },
        {
          given: "a batch that has already succeeded",
          when: "the engineer attempts Replay",
          then: "the action is disabled and a tooltip explains why",
        },
      ],
      traceability: { jiraIssueKey: "ING-214" },
    },
    createdBy: "agent",
    createdAt: iso(0, 3),
    updatedAt: iso(0, 1),
  },
  {
    id: "art_story_2" as Artifact["id"],
    projectId: "mobile-onboarding" as ProjectId,
    runId: RUNS[0]!.id,
    type: "story",
    phase: "requirements",
    title: "Filter batches by status + time window",
    version: 1,
    contentHash: "e".repeat(64),
    status: "awaiting_approval",
    body: {
      kind: "story",
      title: "Filter batches by status + time window",
      description:
        "Operators need a fast way to narrow a huge batch list to the ones that actually need attention.",
      acceptanceCriteria: [
        {
          given: "more than 1,000 batches in the list",
          when: "the user sets status=failed and window=24h",
          then: "the list updates within 300ms",
        },
        {
          given: "a shared URL with filter params",
          when: "a teammate opens it",
          then: "the exact same filtered view renders",
        },
      ],
      traceability: { jiraIssueKey: "ING-215" },
    },
    createdBy: "agent",
    createdAt: iso(0, 2),
    updatedAt: iso(0, 0, 8),
  },
  {
    id: "art_story_3" as Artifact["id"],
    projectId: "mobile-onboarding" as ProjectId,
    runId: RUNS[0]!.id,
    type: "story",
    phase: "requirements",
    title: "Audit every replay with actor + reason",
    version: 1,
    contentHash: "f".repeat(64),
    status: "draft",
    body: {
      kind: "story",
      title: "Audit every replay with actor + reason",
      description:
        "Compliance requires a tamper-evident record of who replayed what and why.",
      acceptanceCriteria: [
        {
          given: "a replay is triggered",
          when: "the audit log is queried within 5s",
          then: "an entry with actor, batch id, reason, and signed-at timestamp exists",
        },
      ],
      traceability: { jiraIssueKey: "ING-216" },
    },
    createdBy: "agent",
    createdAt: iso(0, 1),
    updatedAt: iso(0, 0, 30),
  },
  {
    id: "art_c4_1" as Artifact["id"],
    projectId: "mobile-onboarding" as ProjectId,
    runId: RUNS[0]!.id,
    type: "c4_diagram",
    phase: "design",
    title: "Ingest service — container diagram",
    version: 1,
    contentHash: "b".repeat(64),
    status: "approved",
    body: {
      kind: "c4_diagram",
      source:
        "graph LR\n  Client-->Gateway\n  Gateway-->IngestAPI\n  IngestAPI-->Queue\n  Queue-->Worker\n  Worker-->DB[(Postgres)]",
    },
    createdBy: "agent",
    createdAt: iso(0, 2),
    updatedAt: iso(0, 2),
  },
  {
    id: "art_openapi_1" as Artifact["id"],
    projectId: "mobile-onboarding" as ProjectId,
    runId: RUNS[0]!.id,
    type: "openapi_spec",
    phase: "design",
    title: "Ingest API — v1.2 contract",
    version: 2,
    contentHash: "1".repeat(64),
    status: "awaiting_approval",
    body: {
      kind: "openapi_spec",
      yaml: `openapi: 3.1.0
info:
  title: Ingest API
  version: 1.2.0
  description: Idempotent replay endpoints for Acme's ingest pipeline.
servers:
  - url: https://api.acme.dev
    description: Production
paths:
  /batches:
    get:
      tags: [batches]
      summary: List batches
      parameters:
        - in: query
          name: status
          schema: { type: string }
      responses:
        '200': { description: OK }
  /batches/{id}/replay:
    post:
      tags: [batches]
      summary: Replay a failed batch
      parameters:
        - in: path
          name: id
          required: true
          schema: { type: string }
        - in: header
          name: Idempotency-Key
          required: true
          description: Prevents double-replay
          schema: { type: string }
      responses:
        '202': { description: Accepted }
        '409': { description: Already succeeded }`,
    },
    createdBy: "agent",
    createdAt: iso(0, 2),
    updatedAt: iso(0, 0, 45),
  },
  {
    id: "art_ddl_1" as Artifact["id"],
    projectId: "mobile-onboarding" as ProjectId,
    runId: RUNS[0]!.id,
    type: "db_schema",
    phase: "design",
    title: "Ingest DDL — batches + replays",
    version: 1,
    contentHash: "2".repeat(64),
    status: "approved",
    body: {
      kind: "db_schema",
      dialect: "postgres",
      sql: `-- Ingest DB — migration 0043
CREATE TABLE batches (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id),
  status        text NOT NULL
                CHECK (status IN ('queued','running','succeeded','failed')),
  payload_sha   bytea NOT NULL,
  submitted_at  timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX batches_tenant_status_idx
  ON batches (tenant_id, status, submitted_at DESC);

CREATE TABLE replays (
  id              bigserial PRIMARY KEY,
  batch_id        uuid NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
  idempotency_key text NOT NULL,
  requested_by    uuid NOT NULL REFERENCES users(id),
  reason          text,
  requested_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (batch_id, idempotency_key)
);`,
    },
    createdBy: "agent",
    createdAt: iso(0, 2),
    updatedAt: iso(0, 1),
  },
  {
    id: "art_adr_1" as Artifact["id"],
    projectId: "mobile-onboarding" as ProjectId,
    runId: RUNS[0]!.id,
    type: "adr",
    phase: "design",
    title: "ADR-0007: Idempotent batch replay via client key",
    version: 1,
    contentHash: "3".repeat(64),
    status: "approved",
    body: {
      kind: "adr",
      markdown: `# ADR-0007: Idempotent batch replay via client key

**Status:** Accepted · **Date:** 2026-04-14

## Context

Platform engineers need to replay failed ingest batches. Without a deduplication strategy, a distracted operator clicking Replay twice — or a retried request from a flaky proxy — will double-process the batch downstream.

## Decision

The replay endpoint **requires** an \`Idempotency-Key\` header. Keys are scoped per-batch and stored with a uniqueness constraint. Duplicate keys return \`409 Already succeeded\` without any side effect.

## Consequences

- Clients become responsible for generating and persisting the key.
- We add one index to \`replays(batch_id, idempotency_key)\`.
- The UI disables Replay while a request is in flight.

## Alternatives considered

1. **Server-generated dedupe window** — rejected; ambiguous semantics when clients retry across the window boundary.
2. **Distributed locks** — rejected; adds operational complexity for a problem solvable at the API layer.`,
    },
    createdBy: "agent",
    createdAt: iso(0, 2),
    updatedAt: iso(0, 1),
  },
  {
    id: "art_pr_1" as Artifact["id"],
    projectId: "core-ledger" as ProjectId,
    runId: RUNS[14]!.id,
    type: "pr",
    phase: "development",
    title: "feat(reporting): add cohort retention chart",
    version: 1,
    contentHash: "c".repeat(64),
    status: "merged",
    body: {
      kind: "pr",
      url: "https://github.com/acme/reporting/pull/412",
      branch: "feat/cohort-retention",
      title: "feat(reporting): add cohort retention chart",
      additions: 347,
      deletions: 28,
      filesChanged: 14,
      files: [],
      reviewComments: [],
      checks: [],
    },
    createdBy: "agent",
    createdAt: iso(2),
    updatedAt: iso(1),
  },
  {
    id: "art_pr_ingest_1" as Artifact["id"],
    projectId: "mobile-onboarding" as ProjectId,
    runId: RUNS[0]!.id,
    type: "pr",
    phase: "development",
    title: "feat(ingest): idempotent batch replay",
    version: 1,
    contentHash: "4".repeat(64),
    status: "awaiting_approval",
    body: {
      kind: "pr",
      url: "https://github.com/acme/ingest/pull/141",
      branch: "feat/replay-idempotency",
      title: "feat(ingest): idempotent batch replay",
      additions: 178,
      deletions: 42,
      filesChanged: 4,
      linkedTicket: {
        key: "ING-214",
        url: "https://acme.atlassian.net/browse/ING-214",
      },
      linkedDesignArtifactId: "art_openapi_1" as Artifact["id"],
      files: [
        {
          path: "src/ingest/replay.py",
          status: "added",
          additions: 94,
          deletions: 0,
          language: "python",
          original: "",
          modified: `from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, status

from ingest.db import replays, batches

router = APIRouter()


@router.post("/batches/{batch_id}/replay", status_code=status.HTTP_202_ACCEPTED)
async def replay_batch(
    batch_id: UUID,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
) -> dict[str, str]:
    existing = await replays.find_by_key(batch_id, idempotency_key)
    if existing is not None:
        batch = await batches.get(batch_id)
        if batch.status == "succeeded":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Batch already succeeded under this key.",
            )
        return {"replay_id": existing.id, "status": "already_enqueued"}

    replay = await replays.create(batch_id=batch_id, idempotency_key=idempotency_key)
    return {"replay_id": replay.id, "status": "enqueued"}
`,
        },
        {
          path: "src/ingest/db/replays.py",
          status: "modified",
          additions: 28,
          deletions: 6,
          language: "python",
          original: `async def create(*, batch_id, idempotency_key):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO replays (batch_id, idempotency_key) VALUES ($1, $2) RETURNING id",
            batch_id,
            idempotency_key,
        )
        return row
`,
          modified: `async def find_by_key(batch_id, idempotency_key):
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT id FROM replays WHERE batch_id = $1 AND idempotency_key = $2",
            batch_id,
            idempotency_key,
        )


async def create(*, batch_id, idempotency_key, requested_by=None, reason=None):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO replays (batch_id, idempotency_key, requested_by, reason)
            VALUES ($1, $2, $3, $4)
            RETURNING id
            """,
            batch_id, idempotency_key, requested_by, reason,
        )
        return row
`,
        },
        {
          path: "tests/test_replay.py",
          status: "added",
          additions: 48,
          deletions: 0,
          language: "python",
          original: "",
          modified: `import pytest


async def test_replay_requires_idempotency_key(client):
    resp = await client.post("/batches/00000000-0000-0000-0000-000000000001/replay")
    assert resp.status_code == 422


async def test_replay_is_idempotent(client, seed_failed_batch):
    key = "test-key-123"
    first = await client.post(
        f"/batches/{seed_failed_batch.id}/replay",
        headers={"Idempotency-Key": key},
    )
    assert first.status_code == 202

    second = await client.post(
        f"/batches/{seed_failed_batch.id}/replay",
        headers={"Idempotency-Key": key},
    )
    assert second.status_code == 202
    assert second.json()["status"] == "already_enqueued"
`,
        },
        {
          path: "src/ingest/api.py",
          status: "modified",
          additions: 8,
          deletions: 2,
          language: "python",
          original: `from fastapi import FastAPI

from ingest import batches

app = FastAPI()
app.include_router(batches.router)
`,
          modified: `from fastapi import FastAPI

from ingest import batches, replay

app = FastAPI(
    title="Ingest API",
    version="1.2.0",
)
app.include_router(batches.router)
app.include_router(replay.router)
`,
        },
      ],
      reviewComments: [
        {
          id: "rc_1",
          path: "src/ingest/replay.py",
          line: 23,
          side: "RIGHT",
          body: "The 409 path fires only when status is `succeeded`. For `running`, we still return 200 with `already_enqueued` — intentional?",
          severity: "suggestion",
          author: { kind: "agent", name: "Review Agent" },
          createdAt: iso(0, 1),
        },
        {
          id: "rc_2",
          path: "src/ingest/db/replays.py",
          line: 19,
          side: "RIGHT",
          body: "Add an index hint: query hits `replays_batch_id_idempotency_key_idx` — call that out in a doc-comment so future migrations don't drop it.",
          severity: "nit",
          author: { kind: "agent", name: "Review Agent" },
          createdAt: iso(0, 1),
        },
        {
          id: "rc_3",
          path: "tests/test_replay.py",
          line: 14,
          side: "RIGHT",
          body: "Missing: test that two *different* keys against the same batch both succeed. Add it before merge.",
          severity: "blocking",
          author: { kind: "agent", name: "Review Agent" },
          createdAt: iso(0, 0, 45),
        },
      ],
      checks: [
        { name: "ci/lint", status: "passing", summary: "ruff + mypy clean", durationMs: 12_000 },
        { name: "ci/tests", status: "passing", summary: "48 passed, 0 failed", durationMs: 84_000 },
        { name: "ci/build-image", status: "passing", durationMs: 41_000 },
        { name: "security/scan", status: "failing", summary: "1 high-severity dep vuln in aiohttp 3.9", url: "https://example.test/scan/123" },
      ],
    },
    createdBy: "agent",
    createdAt: iso(0, 2),
    updatedAt: iso(0, 0, 30),
  },
  {
    id: "art_tests_ingest_1" as Artifact["id"],
    projectId: "mobile-onboarding" as ProjectId,
    runId: RUNS[0]!.id,
    type: "test_set",
    phase: "testing",
    title: "Generated tests — replay endpoint + DB layer",
    version: 1,
    contentHash: "5".repeat(64),
    status: "awaiting_approval",
    body: {
      kind: "test_set",
      framework: "pytest",
      files: [
        "tests/test_replay.py",
        "tests/test_replays_db.py",
        "tests/e2e/test_replay_flow.py",
      ],
      coverageDelta: 8.4,
      tests: [
        {
          id: "t1",
          name: "test_replay_requires_idempotency_key",
          path: "tests/test_replay.py",
          suite: "unit",
          status: "passing",
          durationMs: 48,
          lastRunAt: iso(0, 0, 12),
          history: [true, true, true, true, true, true, true, true],
          source: `async def test_replay_requires_idempotency_key(client):
    resp = await client.post("/batches/00000000-0000-0000-0000-000000000001/replay")
    assert resp.status_code == 422`,
          language: "python",
        },
        {
          id: "t2",
          name: "test_replay_is_idempotent",
          path: "tests/test_replay.py",
          suite: "unit",
          status: "passing",
          durationMs: 102,
          lastRunAt: iso(0, 0, 12),
          history: [true, false, true, true, true, true, true, true],
          source: `async def test_replay_is_idempotent(client, seed_failed_batch):
    key = "test-key-123"
    first = await client.post(
        f"/batches/{seed_failed_batch.id}/replay",
        headers={"Idempotency-Key": key},
    )
    assert first.status_code == 202
    second = await client.post(
        f"/batches/{seed_failed_batch.id}/replay",
        headers={"Idempotency-Key": key},
    )
    assert second.status_code == 202
    assert second.json()["status"] == "already_enqueued"`,
          language: "python",
        },
        {
          id: "t3",
          name: "test_two_keys_one_batch_both_succeed",
          path: "tests/test_replay.py",
          suite: "unit",
          status: "failing",
          durationMs: 212,
          lastRunAt: iso(0, 0, 12),
          history: [true, true, true, false, false],
          failure: {
            message: "AssertionError: expected 202, got 409",
            stack: `Traceback (most recent call last):
  File "tests/test_replay.py", line 34, in test_two_keys_one_batch_both_succeed
    assert second.status_code == 202
AssertionError: expected 202, got 409
  first.status_code = 202
  second.status_code = 409  # key uniqueness scoped to batch only — check constraint`,
            lastPassingVersion: "v1.1.3",
            lastPassingAt: iso(2),
          },
          source: `async def test_two_keys_one_batch_both_succeed(client, seed_failed_batch):
    first = await client.post(
        f"/batches/{seed_failed_batch.id}/replay",
        headers={"Idempotency-Key": "key-a"},
    )
    second = await client.post(
        f"/batches/{seed_failed_batch.id}/replay",
        headers={"Idempotency-Key": "key-b"},
    )
    assert first.status_code == 202
    assert second.status_code == 202`,
          language: "python",
        },
        {
          id: "t4",
          name: "test_replays_unique_per_batch",
          path: "tests/test_replays_db.py",
          suite: "integration",
          status: "flaky",
          durationMs: 310,
          lastRunAt: iso(0, 0, 12),
          history: [true, false, true, false, true, true, false, true],
          source: `async def test_replays_unique_per_batch(db):
    await db.replays.create(batch_id=UUID("..."), idempotency_key="dup")
    with pytest.raises(UniqueViolationError):
        await db.replays.create(batch_id=UUID("..."), idempotency_key="dup")`,
          language: "python",
        },
        {
          id: "t5",
          name: "test_replay_end_to_end",
          path: "tests/e2e/test_replay_flow.py",
          suite: "e2e",
          status: "passing",
          durationMs: 2840,
          lastRunAt: iso(0, 0, 12),
          history: [true, true, true, true],
          language: "python",
        },
        {
          id: "t6",
          name: "test_replay_ui_smoke",
          path: "tests/e2e/test_replay_flow.py",
          suite: "e2e",
          status: "skipped",
          durationMs: 0,
          lastRunAt: iso(0, 0, 12),
          history: [],
          language: "python",
        },
      ],
      coverage: [
        { path: "src/ingest/api.py", lines: 48, covered: 47, percent: 97.9, delta: 2.1 },
        { path: "src/ingest/batches.py", lines: 140, covered: 126, percent: 90.0, delta: -1.4 },
        { path: "src/ingest/replay.py", lines: 62, covered: 58, percent: 93.5, delta: 93.5 },
        { path: "src/ingest/db/batches.py", lines: 88, covered: 71, percent: 80.7, delta: 0 },
        { path: "src/ingest/db/replays.py", lines: 52, covered: 49, percent: 94.2, delta: 8.4 },
        { path: "src/ingest/guardrails.py", lines: 34, covered: 12, percent: 35.3, delta: 0 },
        { path: "src/ingest/auth.py", lines: 72, covered: 58, percent: 80.6, delta: 0 },
        { path: "src/ingest/events/publisher.py", lines: 96, covered: 84, percent: 87.5, delta: 1.2 },
        { path: "src/ingest/events/retry.py", lines: 44, covered: 20, percent: 45.5, delta: -3.2 },
        { path: "src/ingest/metrics.py", lines: 28, covered: 0, percent: 0, delta: 0 },
        { path: "src/ingest/workers/consumer.py", lines: 188, covered: 144, percent: 76.6, delta: 0 },
        { path: "src/ingest/workers/retry_scheduler.py", lines: 64, covered: 34, percent: 53.1, delta: -2.1 },
        { path: "tests/conftest.py", lines: 56, covered: 56, percent: 100, delta: 0 },
      ],
    },
    createdBy: "agent",
    createdAt: iso(0, 1),
    updatedAt: iso(0, 0, 10),
  },
  {
    id: "art_pipeline_ingest_1" as Artifact["id"],
    projectId: "mobile-onboarding" as ProjectId,
    runId: RUNS[0]!.id,
    type: "pipeline",
    phase: "deployment",
    title: "deploy.yml — Dev → Staging → Prod (GitHub Actions)",
    version: 1,
    contentHash: "6".repeat(64),
    status: "awaiting_approval",
    body: {
      kind: "pipeline",
      provider: "github_actions",
      filename: ".github/workflows/deploy.yml",
      yaml: `name: deploy

on:
  push:
    branches: [main]

concurrency:
  group: deploy-\${{ github.ref }}
  cancel-in-progress: false

jobs:
  build:
    runs-on: ubuntu-22.04
    outputs:
      image: \${{ steps.meta.outputs.image }}
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - name: Build and push
        id: meta
        run: |
          IMAGE=ghcr.io/\${{ github.repository }}:\${{ github.sha }}
          docker build -t $IMAGE .
          docker push $IMAGE
          echo "image=$IMAGE" >> "$GITHUB_OUTPUT"

  deploy-dev:
    needs: build
    runs-on: ubuntu-22.04
    environment: dev
    steps:
      - run: echo "deploying \${{ needs.build.outputs.image }} to dev"

  deploy-staging:
    needs: deploy-dev
    runs-on: ubuntu-22.04
    environment: staging
    steps:
      - run: echo "deploying to staging"

  deploy-prod:
    needs: deploy-staging
    runs-on: ubuntu-22.04
    environment:
      name: prod
      url: https://app.acme.dev
    steps:
      - run: echo "deploying to prod — requires manual approval in GitHub env protection"
`,
    },
    createdBy: "agent",
    createdAt: iso(0, 0, 40),
    updatedAt: iso(0, 0, 8),
  },
  {
    id: "art_iac_ingest_1" as Artifact["id"],
    projectId: "mobile-onboarding" as ProjectId,
    runId: RUNS[0]!.id,
    type: "iac_diff",
    phase: "deployment",
    title: "Terraform — add replay SQS queue + alarm",
    version: 1,
    contentHash: "7".repeat(64),
    status: "awaiting_approval",
    body: {
      kind: "iac_diff",
      dialect: "terraform",
      filename: "infra/ingest/replay.tf",
      before: `resource "aws_sqs_queue" "ingest" {
  name                       = "ingest"
  visibility_timeout_seconds = 60
  message_retention_seconds  = 345600
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.ingest_dlq.arn
    maxReceiveCount     = 5
  })
}
`,
      after: `resource "aws_sqs_queue" "ingest" {
  name                       = "ingest"
  visibility_timeout_seconds = 60
  message_retention_seconds  = 345600
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.ingest_dlq.arn
    maxReceiveCount     = 5
  })
}

resource "aws_sqs_queue" "ingest_replay" {
  name                       = "ingest-replay"
  visibility_timeout_seconds = 120
  message_retention_seconds  = 345600
  kms_master_key_id          = aws_kms_key.ingest.id
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.ingest_dlq.arn
    maxReceiveCount     = 3
  })
  tags = {
    service = "ingest"
    purpose = "replay"
  }
}

resource "aws_cloudwatch_metric_alarm" "replay_backlog" {
  alarm_name          = "ingest-replay-backlog"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 60
  statistic           = "Average"
  threshold           = 500
  dimensions = {
    QueueName = aws_sqs_queue.ingest_replay.name
  }
  alarm_actions = [aws_sns_topic.oncall.arn]
}
`,
    },
    createdBy: "agent",
    createdAt: iso(0, 0, 40),
    updatedAt: iso(0, 0, 8),
  },
  {
    id: "art_deploy_plan_ingest_1" as Artifact["id"],
    projectId: "mobile-onboarding" as ProjectId,
    runId: RUNS[0]!.id,
    type: "deploy_plan",
    phase: "deployment",
    title: "Deploy plan — ingest v1.2.0",
    version: 1,
    contentHash: "8".repeat(64),
    status: "awaiting_approval",
    body: {
      kind: "deploy_plan",
      prUrl: "https://github.com/acme/ingest/pull/142",
      envs: [
        {
          name: "dev",
          status: "approved",
          requiresApproval: false,
          approvedBy: { id: USER_ID, name: "Ada Lovelace", initials: "AL" },
          approvedAt: iso(0, 0, 35),
          commitSha: "a1b2c3d",
          runId: RUNS[0]!.id,
          deployedAt: iso(0, 0, 30),
        },
        {
          name: "staging",
          status: "awaiting_approval",
          requiresApproval: true,
          commitSha: "a1b2c3d",
        },
        {
          name: "prod",
          status: "queued",
          requiresApproval: true,
          commitSha: "a1b2c3d",
        },
      ],
      rollback: {
        markdown: `## Rollback plan

If post-deploy metrics regress on the replay endpoint:

1. **Freeze** the replay feature flag:
   \`\`\`
   ff set ingest.replay.enabled=false --env prod
   \`\`\`
2. **Redeploy** the previous image:
   \`\`\`
   gh workflow run deploy.yml \\
     --ref main \\
     -f image=ghcr.io/acme/ingest:v1.1.3
   \`\`\`
3. **Terraform** — apply the reverse migration to drop the replay SQS queue after 24h idle.
4. Re-open ING-214 with the failure signature attached.`,
      },
    },
    createdBy: "agent",
    createdAt: iso(0, 0, 35),
    updatedAt: iso(0, 0, 5),
  },
];

/**
 * Connectors span two onboarding levels (PRD §34.3, `ConnectorScope`).
 *
 * Jira, GitHub and Azure DevOps are `organization`-scoped — onboarded once by
 * an Org Admin and inherited by every Business Unit. Slack and GitHub Actions
 * are `business_unit`-scoped: Payments runs its own Slack for approvals and
 * Platform Engineering its own CI, and neither is visible to the other unit.
 * That split is what makes the inheritance visible in the UI — a flat list
 * would render the cascade indistinguishable from no cascade at all.
 */
export const CONNECTORS: Connector[] = [
  {
    id: "conn_jira" as Connector["id"],
    tenantId: TENANT_ID,
    kind: "jira",
    name: "Jira Cloud — Acme",
    installed: true,
    health: "healthy",
    capabilities: [
      { key: "issues.read", description: "Read issues and comments", mode: "read" },
      { key: "issues.write", description: "Create issues, comments, transitions", mode: "write" },
      { key: "webhooks", description: "Receive issue events", mode: "event" },
    ],
    lastCheckedAt: iso(0, 0, 3),
    account: "acme.atlassian.net",
    scope: "organization",
    workspaceId: null,
  },
  {
    id: "conn_github" as Connector["id"],
    tenantId: TENANT_ID,
    kind: "github",
    name: "GitHub — acme org",
    installed: true,
    health: "healthy",
    capabilities: [
      { key: "repo.read", description: "Read repos, branches, files", mode: "read" },
      { key: "pr.write", description: "Open PRs, commits, check-runs", mode: "write" },
      { key: "app.events", description: "Receive app webhooks", mode: "event" },
    ],
    lastCheckedAt: iso(0, 0, 4),
    account: "acme",
    scope: "organization",
    workspaceId: null,
  },
  {
    id: "conn_slack" as Connector["id"],
    tenantId: TENANT_ID,
    kind: "slack",
    name: "Slack — Acme workspace",
    installed: true,
    health: "degraded",
    capabilities: [
      { key: "chat.write", description: "Post to channels + threads", mode: "write" },
      { key: "interactive", description: "Receive button approvals", mode: "event" },
    ],
    lastCheckedAt: iso(0, 1),
    account: "acme.slack.com",
    scope: "business_unit",
    workspaceId: "ws_payments",
  },
  {
    id: "conn_ado" as Connector["id"],
    tenantId: TENANT_ID,
    kind: "azure_devops",
    name: "Azure DevOps",
    installed: false,
    health: "disconnected",
    capabilities: [],
    lastCheckedAt: null,
    scope: "organization",
    workspaceId: null,
  },
  {
    id: "conn_gha" as Connector["id"],
    tenantId: TENANT_ID,
    kind: "github_actions",
    name: "GitHub Actions",
    installed: false,
    health: "disconnected",
    capabilities: [],
    lastCheckedAt: null,
    scope: "business_unit",
    workspaceId: "ws_platform",
  },
];

export const AUDIT_EVENTS: AuditEvent[] = Array.from({ length: 24 }).map((_, i) => ({
  id: `audit_${i + 1}` as AuditEvent["id"],
  tenantId: TENANT_ID,
  // Spread across EVERY seeded project, not just two. The audit trail is
  // scope-filtered by project (lib/mock/access-scope.ts), so a project with no
  // events is indistinguishable from a filter that silently drops everything —
  // a Business Unit Admin whose unit happened to own none of the rows would see
  // an empty tab and reasonably conclude the page was broken. Every 7th row is
  // organization-level (`null`), which a scoped viewer correctly never sees.
  projectId: (i % 7 === 6
    ? null
    : ["mobile-onboarding", "payments-api", "core-ledger", "recon-bots", "fraud-features"][
        i % 5
      ]) as AuditEvent["projectId"],
  action: (
    [
      "run.started",
      "run.completed",
      "artifact.created",
      "run.approved",
      "connector.installed",
      "settings.updated",
    ] as const
  )[i % 6]!,
  actor: {
    id: i % 4 === 0 ? "agent" : (USER_ID as AuditEvent["actor"]["id"]),
    name: i % 4 === 0 ? "Requirements Agent" : "Ada Lovelace",
  },
  resource: {
    type: i % 2 === 0 ? "run" : "artifact",
    id: `res_${i + 1}`,
    name: `Resource ${i + 1}`,
  },
  at: iso(0, i * 2, (i * 13) % 60),
  ip: i % 3 === 0 ? "10.2.3.4" : undefined,
}));
