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
import { emitNotification } from "@/lib/mock/notification-fixtures";
import {
  agentAccessApprover,
  initialApproverRole,
  nextAgentAccessStage,
  nextApproverRole,
} from "@/lib/requests/routing";
import { ROLE_META, type PlatformRole } from "@/lib/roles";
import type { Phase } from "@/lib/schemas/enums";
import type {
  GovernanceApproval,
  GovernanceApprovalDecision,
  GovernanceApprovalType,
  RequestAttachment,
  RequestEventKind,
  RequestPriority,
  RequestTimelineEvent,
} from "@/lib/schemas/governance-approval";

let nextId = 4;
let nextEventId = 1;

/** Append-only: every state change leaves a row, nothing is ever rewritten. */
function event(
  kind: RequestEventKind,
  actor: string,
  extra?: Partial<Pick<RequestTimelineEvent, "actorRole" | "toRole" | "note">>,
  at?: string,
): RequestTimelineEvent {
  return {
    id: `ev_${nextEventId++}`,
    kind,
    at: at ?? new Date().toISOString(),
    actor,
    actorRole: extra?.actorRole ?? null,
    toRole: extra?.toRole ?? null,
    note: extra?.note ?? null,
  };
}

/** The opening two entries every seeded request shares. */
function seedTimeline(
  actor: string,
  actorRole: PlatformRole,
  toRole: PlatformRole,
  atIso: string,
): RequestTimelineEvent[] {
  return [
    event("created", actor, { actorRole }, atIso),
    event("submitted", actor, { actorRole }, atIso),
    event("assigned", "System", { toRole }, atIso),
  ];
}
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
    status: "pending_review",
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
    requestedById: "idn_grace",
    requestedByRole: "project_admin",
    description:
      "Lending needs a standalone greenfield web app for regional outage alerting, separate from the existing ledger work. Track 1, eight agents.",
    priority: "normal",
    attachments: [],
    currentApproverRole: "bu_admin",
    escalationCount: 0,
    timeline: seedTimeline(
      "Grace Hopper",
      "project_admin",
      "bu_admin",
      new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
    ),
  },
  {
    id: "gov_2",
    type: "model_credential",
    status: "pending_review",
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
    requestedById: "idn_lena",
    requestedByRole: "project_admin",
    description:
      "Reconciliation bots needs GPT-4o for the document-extraction stage. The shared Azure credential is already onboarded at the org; this asks for it to reach the project.",
    priority: "high",
    attachments: [
      { name: "extraction-benchmark.pdf", sizeBytes: 512_000, contentType: "application/pdf" },
    ],
    currentApproverRole: "bu_admin",
    escalationCount: 0,
    timeline: seedTimeline(
      "Lena Fischer",
      "project_admin",
      "bu_admin",
      new Date(Date.now() - 5 * 60 * 60 * 1000).toISOString(),
    ),
  },
  {
    id: "gov_3",
    type: "budget_increase",
    status: "pending_review",
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
    requestedById: "idn_marcus",
    requestedByRole: "bu_admin",
    description:
      "Payments will breach its cap before the period closes. The overrun is driven by the SCA exemption defect triage, which was not in the original forecast.",
    priority: "urgent",
    attachments: [
      { name: "payments-spend-forecast.xlsx", sizeBytes: 88_400, contentType: "application/vnd.ms-excel" },
    ],
    currentApproverRole: "org_admin",
    escalationCount: 0,
    timeline: seedTimeline(
      "Marcus Reyes",
      "bu_admin",
      "org_admin",
      new Date(Date.now() - 26 * 60 * 60 * 1000).toISOString(),
    ),
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
  // ── Hand-raised requests supply these; side-effect creates do not ─────────
  requestedById?: string | null;
  requestedByRole?: PlatformRole | null;
  description?: string | null;
  priority?: RequestPriority;
  attachments?: RequestAttachment[];
}): GovernanceApproval {
  const requesterRole = input.requestedByRole ?? null;
  // Routing decides the approver — never the caller. A create path that could
  // name its own approver would be the hole every other rule here is guarding.
  const approver = initialApproverRole(input.type, requesterRole);
  const now = new Date().toISOString();
  const created: GovernanceApproval = {
    id: `gov_${nextId++}`,
    type: input.type,
    status: "pending_review",
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
    requestedById: input.requestedById ?? null,
    requestedByRole: requesterRole,
    description: input.description ?? null,
    priority: input.priority ?? "normal",
    attachments: input.attachments ?? [],
    currentApproverRole: approver,
    // Only agent access is answered twice; everything else has a null stage,
    // which is what `nextAgentAccessStage` reads as "this one closes on the
    // first decision".
    approvalStage: input.type === "agent_access" ? "project_admin" : null,
    escalationCount: 0,
    timeline: [
      event("created", input.requestedBy, { actorRole: requesterRole }, now),
      event("submitted", input.requestedBy, { actorRole: requesterRole }, now),
      event("assigned", "System", { toRole: approver }, now),
    ],
  };
  GOVERNANCE_APPROVALS.push(created);

  // Emitted HERE rather than in the route handlers, so the Next route and the
  // MSW mirror cannot drift into notifying differently — the transition and
  // its notification are one thing.
  emitNotification({
    kind: "request_created",
    title: "Request raised",
    body: created.title,
    href: "/approvals",
    identityId: created.requestedById,
  });
  if (approver) {
    emitNotification({
      kind: "request_approval_required",
      title: "Approval required",
      body: `${created.requestedBy} raised "${created.title}".`,
      href: "/approvals",
      role: approver,
    });
  }
  return created;
}

/**
 * Withdraw a request you raised.
 *
 * Only the initiator, and only while it is still open — a decided request is
 * part of the record, and letting someone cancel one after the fact would make
 * the audit trail editable by whoever the decision went against.
 */
export function cancelGovernanceApproval(
  id: string,
  actor: string,
  actorIdentityId: string | null,
  reason?: string,
): GovernanceApproval | undefined | "forbidden" {
  const item = GOVERNANCE_APPROVALS.find((a) => a.id === id);
  if (!item) return undefined;
  if (item.status === "approved" || item.status === "rejected" || item.status === "cancelled") {
    return "forbidden";
  }
  if (item.requestedById && actorIdentityId && item.requestedById !== actorIdentityId) {
    return "forbidden";
  }
  item.status = "cancelled";
  item.currentApproverRole = null;
  item.reason = reason ?? null;
  item.timeline = [...item.timeline, event("cancelled", actor, { note: reason ?? null })];
  return item;
}

/**
 * Send the request one tier up.
 *
 * The climb is the request lane's whole fallback story (PRD §33.2: "None — it
 * climbs until a tier can grant it"), so the only thing that stops it is
 * running out of ladder. Refused at the top rather than silently no-op'd: an
 * Org Admin pressing escalate deserves to be told there is nobody above them.
 *
 * Status becomes `escalated`, not `pending_review` — a queue that showed a
 * climbed request as ordinary pending would hide that its first approver never
 * answered, which is the one thing an escalation exists to make visible.
 */
export function escalateGovernanceApproval(
  id: string,
  actor: string,
  reason?: string,
): GovernanceApproval | undefined | "top" {
  const item = GOVERNANCE_APPROVALS.find((a) => a.id === id);
  if (!item) return undefined;
  const next = nextApproverRole((item.currentApproverRole as PlatformRole | null) ?? null);
  if (next === null) return "top";

  item.status = "escalated";
  item.currentApproverRole = next;
  item.escalationCount += 1;
  item.timeline = [
    ...item.timeline,
    event("escalated", actor, { toRole: next, note: reason ?? null }),
  ];

  emitNotification({
    kind: "request_escalated",
    title: "Request escalated",
    body: `"${item.title}" moved to the ${ROLE_META[next].label}.`,
    href: "/approvals",
    identityId: item.requestedById,
  });
  emitNotification({
    kind: "request_assigned",
    title: "Request assigned to you",
    body: `"${item.title}" was escalated to your queue.`,
    href: "/approvals",
    role: next,
  });
  return item;
}

export function decideGovernanceApproval(
  id: string,
  decision: GovernanceApprovalDecision,
  decidedBy: string,
  reason?: string,
): GovernanceApproval | undefined {
  const item = GOVERNANCE_APPROVALS.find((a) => a.id === id);
  if (!item) return undefined;

  // A multi-stage request that is approved at a non-final stage ADVANCES; only
  // the last stage closes it. Rejection closes it at any stage — one no is
  // enough, and carrying a rejected request to the next approver would ask
  // them to overturn a decision that was theirs to defer to.
  const phase = (item.payload?.phase as Phase | undefined) ?? "development";
  const nextStage =
    decision === "approve" ? nextAgentAccessStage(item.approvalStage ?? null, phase) : null;
  if (nextStage) {
    const nextApprover = agentAccessApprover(nextStage, phase);
    item.approvalStage = nextStage;
    item.currentApproverRole = nextApprover;
    item.status = "pending_review";
    item.timeline = [
      ...item.timeline,
      event("approved", decidedBy, { note: reason ?? null }),
      // `assigned`, not `escalated`: the first approver answered, and the
      // request moving on is the process working rather than stalling.
      event("assigned", "System", { toRole: nextApprover }),
    ];
    emitNotification({
      kind: "request_approved",
      title: "Request approved — now with the agent's owner",
      body: `${decidedBy} approved "${item.title}". It now needs the agent owner's sign-off.`,
      href: "/approvals",
      identityId: item.requestedById,
    });
    return item;
  }

  item.approvalStage = null;
  item.status = decision === "approve" ? "approved" : "rejected";
  item.decidedBy = decidedBy;
  item.decidedAt = new Date().toISOString();
  item.reason = reason ?? null;
  // Nobody holds it any more — a decided request with a live approver would
  // keep showing up in that role's queue.
  item.currentApproverRole = null;
  item.timeline = [
    ...item.timeline,
    event(decision === "approve" ? "approved" : "rejected", decidedBy, {
      note: reason ?? null,
    }),
  ];

  // The outcome goes to whoever raised it — "the outcome of something you
  // raised" is one of the three things PRD §33.2 says lands in your queue.
  emitNotification({
    kind: decision === "approve" ? "request_approved" : "request_rejected",
    title: decision === "approve" ? "Request approved" : "Request rejected",
    body: `${decidedBy} ${decision === "approve" ? "approved" : "rejected"} "${item.title}".`,
    href: "/approvals",
    identityId: item.requestedById,
  });
  return item;
}
