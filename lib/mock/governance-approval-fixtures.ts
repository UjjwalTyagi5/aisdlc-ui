/**
 * Governance-tier approval queue — an in-memory store (mirrors the
 * mutable-array pattern in workspace-fixtures.ts / approval-fixtures.ts), so
 * project-creation and model-credential approvals persist for the life of
 * the dev process. Plain data + functions, server-safe (imported by both the
 * Next.js route handlers and the MSW handlers — see
 * [[msw-dual-runtime-mutation-rule]] for why both need it). This is the
 * DUMMY-DATA source; a real backend governance service replaces the
 * route-handler bodies, not these shapes.
 */
import type {
  GovernanceApproval,
  GovernanceApprovalDecision,
  GovernanceApprovalType,
} from "@/lib/schemas/governance-approval";

let nextId = 4;
/**
 * Seeded pending approvals — one per Business Unit that has a governance
 * approver persona, so the "a Business Unit Admin sees only their own unit's
 * approvals" rule is demonstrable in both directions: each admin has something
 * to act on, AND something to be correctly denied.
 *
 * `gov_1` matches mocks/fixtures.ts's "regional-alerts" project
 * (approvalStatus: "pending_approval") — see that fixture's comment for why a
 * static seed is needed rather than relying on a live cross-role create.
 *
 * NOTE — the previous version of this comment said the mock Business Unit Admin
 * "isn't tied to a specific BU, so its session defaults to workspaces[0]". That
 * is no longer true: each mock persona now signs in as a seeded identity with
 * real memberships (lib/auth/persona-identity.ts), and the Business Unit Admin
 * persona administers Platform Engineering. `gov_2` is the row that persona is
 * entitled to; `gov_1` (Lending) is the row it must never see.
 */
const GOVERNANCE_APPROVALS: GovernanceApproval[] = [
  {
    id: "gov_1",
    type: "project_creation",
    status: "pending",
    workspaceId: "ws_lending",
    workspaceName: "Lending",
    projectId: "regional-alerts",
    projectName: "Regional outage alerts",
    title: "New project: Regional outage alerts",
    summary:
      "Grace Hopper requested a new web app project on greenfield in Lending.",
    requestedBy: "Grace Hopper",
    requestedAt: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
    decidedBy: null,
    decidedAt: null,
    reason: null,
    targetRef: "regional-alerts",
  },
  {
    id: "gov_2",
    type: "model_credential",
    status: "pending",
    workspaceId: "ws_platform",
    workspaceName: "Platform Engineering",
    projectId: "recon-bots",
    projectName: "Reconciliation bots",
    title: "Model credential: Azure OpenAI (GPT-4o) for Reconciliation bots",
    summary:
      "Lena Fischer requested the shared Azure OpenAI credential be made available to the Reconciliation bots project.",
    requestedBy: "Lena Fischer",
    requestedAt: new Date(Date.now() - 5 * 60 * 60 * 1000).toISOString(),
    decidedBy: null,
    decidedAt: null,
    reason: null,
    targetRef: "azure-openai-gpt4o",
  },
  {
    id: "gov_3",
    type: "budget_increase",
    status: "pending",
    workspaceId: "ws_payments",
    workspaceName: "Payments",
    projectId: null,
    projectName: null,
    title: "Budget increase: Payments — $12,800 → $16,000/month",
    summary:
      "Payments is at 96% of its monthly cap with nine days left in the period. Marcus Reyes requested a $3,200 increase.",
    requestedBy: "Marcus Reyes",
    requestedAt: new Date(Date.now() - 26 * 60 * 60 * 1000).toISOString(),
    decidedBy: null,
    decidedAt: null,
    reason: null,
    targetRef: "ws_payments",
  },
];

export function listGovernanceApprovals(workspaceId?: string): GovernanceApproval[] {
  const items = workspaceId
    ? GOVERNANCE_APPROVALS.filter((a) => a.workspaceId === workspaceId)
    : GOVERNANCE_APPROVALS;
  return [...items].sort((a, b) => a.requestedAt.localeCompare(b.requestedAt));
}

export function getGovernanceApproval(id: string): GovernanceApproval | undefined {
  return GOVERNANCE_APPROVALS.find((a) => a.id === id);
}

export function createGovernanceApproval(input: {
  type: GovernanceApprovalType;
  workspaceId: string;
  workspaceName: string;
  projectId?: string | null;
  projectName?: string | null;
  title: string;
  summary: string;
  requestedBy: string;
  targetRef: string;
  payload?: Record<string, unknown> | null;
}): GovernanceApproval {
  const created: GovernanceApproval = {
    id: `gov_${nextId++}`,
    type: input.type,
    status: "pending",
    workspaceId: input.workspaceId,
    workspaceName: input.workspaceName,
    projectId: input.projectId ?? null,
    projectName: input.projectName ?? null,
    title: input.title,
    summary: input.summary,
    requestedBy: input.requestedBy,
    requestedAt: new Date().toISOString(),
    decidedBy: null,
    decidedAt: null,
    reason: null,
    targetRef: input.targetRef,
    payload: input.payload ?? null,
  };
  GOVERNANCE_APPROVALS.push(created);
  return created;
}

export function decideGovernanceApproval(
  id: string,
  decision: GovernanceApprovalDecision,
  decidedBy: string,
  reason?: string,
): GovernanceApproval | undefined {
  const item = GOVERNANCE_APPROVALS.find((a) => a.id === id);
  if (!item) return undefined;
  item.status = decision === "approve" ? "approved" : "rejected";
  item.decidedBy = decidedBy;
  item.decidedAt = new Date().toISOString();
  item.reason = reason ?? null;
  return item;
}
