import { http, HttpResponse, delay } from "msw";

import { ARTIFACTS, AUDIT_EVENTS, CONNECTORS, PROJECTS, RUNS, STEPS } from "./fixtures";
import {
  archiveWorkspace as fxArchiveWorkspace,
  createWorkspace as fxCreateWorkspace,
  findOrCreateIdentityBySsoSubject as fxFindOrCreateIdentityBySsoSubject,
  getIdentity as fxGetIdentity,
  getWorkspace as fxGetWorkspace,
  listMembers as fxListMembers,
  listWorkspaces as fxListWorkspaces,
  patchWorkspace as fxPatchWorkspace,
  removeMembership as fxRemoveMembership,
  setBusinessUnitAdmin as fxSetBusinessUnitAdmin,
  setMembershipRole as fxSetMembershipRole,
} from "@/lib/mock/workspace-fixtures";
import { sseHandler } from "./sse";
import { workspaceStreamHandler } from "./workspace-stream";
import { chatHandler } from "./chat";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";
import { ROLE_PERMISSIONS } from "@/lib/auth/role-permissions";
import {
  ACCESS_MEMBERS,
  ACCESS_WORKSPACES,
  ORG_MEMBERS,
  addAccessMember,
  mockInitials,
} from "@/lib/mock/access-fixtures";
import {
  onboardingScopeFor,
  recordConnectorCredentials,
  visibleConnectorsForScope,
} from "@/lib/mock/connector-scope";
import {
  connectorGrantsForWorkspace,
  connectorGrantsForWorkspaces,
  listConnectorGrants,
  permittedConnectorKinds,
  setBuConnectorGrants,
  setConnectorGrants,
  grantConnectorToUnit,
  revokeConnectorGrant,
} from "@/lib/mock/connector-grants";
import {
  listIntegrationAccess,
  revokeProjectIntegration,
} from "@/lib/mock/integration-access";
import { grantMcpToUnit, revokeMcpGrant } from "@/lib/mock/mcp-fixtures";
import {
  listRolePermissions,
  resetRolePermissions,
  setRolePermissions,
} from "@/lib/mock/role-permission-overrides";
import {
  listProjectIntegrations,
  upsertProjectCredential,
} from "@/lib/mock/project-integration-fixtures";
import { ProjectIntegrationCredentialInput } from "@/lib/schemas/project-integration";
import { buildOrgOverview } from "@/lib/mock/org-overview-fixtures";
import { buildSpendSeries } from "@/lib/mock/cost-fixtures";
import {
  activateProject,
  createProjectRecord,
  getProjectById as fxGetProjectById,
  rejectProjectCreation,
  requestOrArchiveProject,
  setProjectArchived,
  updateProjectRecord,
  type ProjectUpdatePatch,
} from "@/lib/mock/project-fixtures";
import {
  cancelGovernanceApproval,
  createGovernanceApproval,
  decideGovernanceApproval,
  escalateGovernanceApproval,
  getGovernanceApproval,
  listGovernanceApprovals,
} from "@/lib/mock/governance-approval-fixtures";
import {
  listNotifications,
  markNotificationsRead,
} from "@/lib/mock/notification-fixtures";
import { canDecideRequest, canRaiseRequest, canRaiseType } from "@/lib/requests/routing";
import { ROLE_META, type PlatformRole } from "@/lib/roles";
import {
  REQUEST_TYPE_LABEL,
  RequestCreateInput,
} from "@/lib/schemas/governance-approval";
import type { GovernanceApprovalDecisionInput } from "@/lib/schemas/governance-approval";
import { MOCK_COOKIE_NAME, decodeSession } from "@/lib/auth/mock";
import { resolveSessionScope } from "@/lib/auth/access-scope";
import {
  canManageBusinessUnit,
  canManageProject,
  canReadBusinessUnit,
  canReadGovernanceApproval,
  canReadProject,
  filterByProject,
  notificationViewer,
} from "@/lib/mock/access-scope";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import { canCreateProject } from "@/lib/auth/permissions";

/** Mirrors `viewerId` in app/api/projects/[id]/integrations/route.ts — a
 *  credential belongs to a person, and both runtimes must agree on which. */
const mswViewerId = (s: { user?: { id?: string; email?: string; name?: string } }): string =>
  s.user?.id ?? s.user?.email ?? s.user?.name ?? "unknown";
import type { ProjectCreateInput } from "@/lib/schemas/project";
import {
  activateModelProvider,
  createModelProvider,
  deleteModelProvider,
  getBuAllowedModels,
  getBuModelAvailability,
  getModelCatalog,
  getModelGrantMatrix,
  listAllModelProviders,
  getOrgModelGrants,
  getProjectModelSelection,
  listModelProviders,
  probeModelProvider,
  rejectModelProvider,
  setBuModelGrants,
  setProjectModelSelection,
  setModelDefaultOffering,
  setOrgModelGrants,
  updateModelProvider,
  verifyModelProvider,
  type CreateModelProviderInput,
} from "@/lib/mock/model-fixtures";
import type { ModelAllowEntry, OrgModelGrant } from "@/lib/schemas/model";
import type { ConnectorGrant } from "@/lib/schemas/connector";
import {
  listOverridesForProject,
  removeOverride,
  setOverride,
} from "@/lib/mock/agent-access-override-fixtures";
import type { AgentAccessOverrideInput, InvolvementLevel } from "@/lib/schemas/agent-access";
import type { Phase } from "@/lib/schemas/enums";
import {
  createMcpServer as createMcpServerRecord,
  listMcpServersForScope,
} from "@/lib/mock/mcp-fixtures";
import type { McpServer } from "@/lib/schemas/mcp";
import { getProjectCapabilitiesData, setCuratedDisabled } from "@/lib/mock/capabilities-fixtures";
import {
  canWriteCustomRole as fxCanWriteCustomRole,
  createCustomRole as fxCreateCustomRole,
  deleteCustomRole as fxDeleteCustomRole,
  getCustomRole as fxGetCustomRole,
  listCustomRoles as fxListCustomRoles,
  resolveRoleOwner as fxResolveRoleOwner,
  updateCustomRole as fxUpdateCustomRole,
} from "@/lib/mock/custom-role-fixtures";
import type { CustomRoleScope } from "@/lib/api/roles";
import {
  getUserDetail as fxGetUserDetail,
  scopeUserDirectory as fxScopeUserDirectory,
} from "@/lib/mock/user-directory-fixtures";
import {
  assignBusinessUnitRole as fxAssignBusinessUnitRole,
  changeOrgAppointment as fxChangeOrgAppointment,
  onboardIntoOrganization as fxOnboardIntoOrganization,
} from "@/lib/mock/onboarding";
import {
  applyCrossBuAssignment as fxApplyCrossBuAssignment,
  requestCrossBuAssignment as fxRequestCrossBuAssignment,
} from "@/lib/mock/cross-bu";
import {
  listCrossBuGrants as fxListCrossBuGrants,
  revokeCrossBuGrant as fxRevokeCrossBuGrant,
} from "@/lib/mock/cross-bu-fixtures";
import {
  addProjectMember as fxAddProjectMember,
  projectMembershipBlock as fxProjectMembershipBlock,
  removeProjectMembershipsInWorkspace as fxRemoveProjectMembershipsInWorkspace,
  listProjectMembers as fxListProjectMembers,
  removeProjectMember as fxRemoveProjectMember,
  updateProjectMemberRole as fxUpdateProjectMemberRole,
} from "@/lib/mock/project-membership-fixtures";
import {
  buildPreview,
  createDraft,
  getAgentProfileSummary,
  listVersions,
  publishVersion,
  unpublishVersion,
  versionScope,
} from "@/lib/mock/agent-profile-fixtures";
import { agentDefaultApprovalType, canPublishAtTier } from "@/lib/governance";
import type { AgentProfileDraftInput, ProfileScope } from "@/lib/schemas/agent-profiles";
import {
  createAgentSkill as fxCreateAgentSkill,
  deleteAgentSkill as fxDeleteAgentSkill,
  getAgentSkill as fxGetAgentSkill,
  listAgentSkills as fxListAgentSkills,
  listAgentSkillVersions as fxListAgentSkillVersions,
  toggleAgentSkill as fxToggleAgentSkill,
  updateAgentSkill as fxUpdateAgentSkill,
} from "@/lib/mock/agent-skill-fixtures";
import type { SkillScope } from "@/lib/schemas/agent-skills";

/**
 * Read the signed-in session from MSW's parsed cookie jar — MSW handlers run
 * in a Service Worker, and the Fetch spec strips the `Cookie` header from any
 * request a Service Worker intercepts (a browser-enforced restriction, not an
 * MSW gap), so `request.headers.get("cookie")` is always empty there. MSW
 * works around this by parsing `document.cookie` on the page side and handing
 * it to the resolver as `cookies` — use that instead of the request headers.
 */
function sessionFromCookies(cookies: Record<string, string>) {
  const raw = cookies[MOCK_COOKIE_NAME];
  if (!raw) return null;
  return decodeSession(raw);
}

/**
 * The viewer's Business Unit / project boundary, resolved the same way the Next
 * route handlers resolve it — same function, same fixtures, so the two runtimes
 * cannot disagree about who sees what ([[msw-dual-runtime-mutation-rule]]).
 *
 * Every handler below that returns scope-bearing rows MUST filter through this.
 * MSW intercepts client-side fetches only, so a handler that skips the filter
 * silently re-opens the leak in the browser while the server route stays
 * correct — the hardest version of this bug to see, because the page looks fine
 * until MSW is disabled.
 */
function scopeFromCookies(cookies: Record<string, string>) {
  return resolveSessionScope(sessionFromCookies(cookies));
}

const NOT_PERMITTED =
  "Your Organization Admin hasn't permitted this connector for your business unit.";

/**
 * May this viewer touch this connector kind at all?
 *
 * Org-wide viewers write the grants, so nothing is withheld from them. Anyone
 * else is permitted a kind if any unit they belong to was granted it — the
 * union, not the intersection, because a person in two units legitimately acts
 * in whichever one permits the connector they're reaching for.
 */
function kindIsPermitted(
  scope: ReturnType<typeof scopeFromCookies>,
  kind: string,
): boolean {
  if (scope.isOrgWide) return true;
  return scope.businessUnitIds.some((id) =>
    (permittedConnectorKinds(id) as string[]).includes(kind),
  );
}

/**
 * Toggle simulated network latency via NEXT_PUBLIC_MOCK_LATENCY_MS
 * (clamped 0 – 2000ms). Useful for testing loading states.
 */
const LATENCY_MS = Math.min(
  Math.max(Number(process.env.NEXT_PUBLIC_MOCK_LATENCY_MS) || 120, 0),
  2000,
);

async function lag() {
  if (LATENCY_MS > 0) await delay(LATENCY_MS);
}

function page<T>(items: T[], query: URLSearchParams) {
  const page = Number(query.get("page") ?? "1");
  const pageSize = Math.min(Number(query.get("pageSize") ?? "20"), 200);
  const start = (page - 1) * pageSize;
  return {
    items: items.slice(start, start + pageSize),
    pagination: { page, pageSize, total: items.length },
  };
}

// ───── Access / RBAC mock state (mirrors backend shared/authz/permissions.py) ─────
/**
 * The assignable role catalogue — the platform's twelve roles (PRD §33.1 plus
 * Scrum Master, a platform addition),
 * with the permissions each holds per the role × permission matrix (§14.11).
 *
 * Governance tier (Organization Admin, Business Unit Admin) never holds
 * agent-invoke or approval permissions; the Developer never holds an approval
 * permission at all, which is what makes self-approval structurally
 * impossible rather than merely discouraged (§14.10).
 */
const ACCESS_ROLES = [
  { name: "org_admin", label: "Organization Admin", description: "Governance only. Creates business units and appoints their admins; sets the organization budget and org-wide policy. Never builds, never approves delivery work.", permissions: [...ROLE_PERMISSIONS.org_admin] },
  { name: "bu_admin", label: "Business Unit Admin", description: "Governance only. Runs one business unit: its budget, connections, members and project creation.", permissions: [...ROLE_PERMISSIONS.bu_admin] },
  { name: "project_admin", label: "Project Admin", description: "Runs one project; selects its connections; fallback approver on every agent.", permissions: [...ROLE_PERMISSIONS.project_admin] },
  { name: "ba", label: "BA (Business Analyst)", description: "Owns the Requirements agent.", permissions: [...ROLE_PERMISSIONS.ba] },
  { name: "architect", label: "Architect", description: "Owns Design; approves Development and Code Review.", permissions: [...ROLE_PERMISSIONS.architect] },
  { name: "developer", label: "Developer", description: "Builds in Development; requests code review. Never self-approves — the Architect approves its push and PR.", permissions: [...ROLE_PERMISSIONS.developer] },
  { name: "qa", label: "QA / Tester", description: "Owns the Testing agent.", permissions: [...ROLE_PERMISSIONS.qa] },
  { name: "security_engineer", label: "Security Engineer", description: "Owns the Security agent; the one contributor role with standing trace and audit access on its projects.", permissions: [...ROLE_PERMISSIONS.security_engineer] },
  { name: "devops_engineer", label: "DevOps Engineer", description: "Owns the Deployment agent; requests tooling in Development.", permissions: [...ROLE_PERMISSIONS.devops_engineer] },
  { name: "data_engineer", label: "Data Engineer", description: "Owns the Data Engineering agent (Track 5).", permissions: [...ROLE_PERMISSIONS.data_engineer] },
  { name: "scrum_master", label: "Scrum Master", description: "Coordinates the team's flow across every agent stage; observes but owns no single gate — never holds an approval permission.", permissions: [...ROLE_PERMISSIONS.scrum_master] },
];

// ACCESS_WORKSPACES / ACCESS_MEMBERS / ORG_MEMBERS / mockInitials moved to
// lib/mock/access-fixtures.ts so app/api/onboarding/route.ts can write into
// the same store — a person onboarded from Users or Roles & Access must show
// up in both places, not just here.

export const handlers = [
  // ───── Projects ─────
  http.get("/api/projects", async ({ request, cookies }) => {
    await lag();
    const url = new URL(request.url);
    const archived = url.searchParams.get("archived");
    const search = url.searchParams.get("search")?.toLowerCase();
    // Scope first, then archive/search/page — mirrors app/api/projects/route.ts
    // exactly, including the ordering, so `pagination.total` describes a set the
    // viewer can actually open.
    const scope = scopeFromCookies(cookies);
    let list = PROJECTS.filter((p) => canReadProject(scope, String(p.id)));
    if (archived !== null) list = list.filter((p) => p.archived === (archived === "true"));
    else list = list.filter((p) => !p.archived);
    if (search) list = list.filter((p) => p.name.toLowerCase().includes(search));
    return HttpResponse.json(page(list, url.searchParams));
  }),

  // 404 (not 403) for an unauthorized id — a 403 would confirm the project
  // exists, which is itself the cross-project fact being withheld.
  http.get("/api/projects/:id", async ({ params, cookies }) => {
    await lag();
    const project = PROJECTS.find((p) => p.id === params.id);
    if (!project || !canReadProject(scopeFromCookies(cookies), String(params.id))) {
      return HttpResponse.json(
        { code: "not_found", message: "Project not found" },
        { status: 404 },
      );
    }
    return HttpResponse.json(project);
  }),

  // Mirrors app/api/projects/route.ts's POST exactly (same lib/mock
  // functions) — see [[msw-dual-runtime-mutation-rule]].
  http.post("/api/projects", async ({ request, cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) {
      return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });
    }
    const body = (await request.json()) as ProjectCreateInput;
    if (!body?.name || !body?.workspaceId) {
      return HttpResponse.json(
        { code: "invalid_input", message: "name and workspaceId are required" },
        { status: 422 },
      );
    }
    const role = effectivePlatformRole(session);
    // Mirrors app/api/projects/route.ts. In the browser THIS handler answers,
    // so a check only in the Next route would not be a check at all.
    if (!canCreateProject(role)) {
      return HttpResponse.json(
        { code: "forbidden", message: "Your role cannot create projects." },
        { status: 403 },
      );
    }
    const created = createProjectRecord(body, {
      role,
      displayName: session.user.name,
      userRef: {
        id: session.user.id,
        name: session.user.name,
        email: session.user.email,
        initials: session.user.initials,
      },
    });
    return HttpResponse.json(created, { status: 201 });
  }),

  http.post("/api/projects/:id/archive", async ({ params, cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });
    const role = effectivePlatformRole(session);
    const result = requestOrArchiveProject(String(params.id), { role, displayName: session.user.name });
    if (!result) {
      return HttpResponse.json({ code: "not_found", message: "not found" }, { status: 404 });
    }
    return HttpResponse.json(result.project);
  }),

  http.post("/api/projects/:id/restore", async ({ params }) => {
    await lag();
    const project = setProjectArchived(String(params.id), false);
    if (!project) {
      return HttpResponse.json({ code: "not_found", message: "not found" }, { status: 404 });
    }
    return HttpResponse.json(project);
  }),

  http.patch("/api/projects/:id", async ({ params, request, cookies }) => {
    await lag();
    const patch = (await request.json()) as ProjectUpdatePatch;
    // Delivery status and the cost cap need MANAGE, not read — mirrors
    // app/api/projects/[id]/route.ts.
    const needsManage =
      "deliveryStatus" in patch ||
      "monthlyBudgetUsd" in patch ||
      "budgetStartDate" in patch ||
      "budgetEndDate" in patch;
    if (needsManage) {
      const scope = scopeFromCookies(cookies);
      const existing = fxGetProjectById(String(params.id));
      const managed =
        canManageProject(scope, String(params.id)) ||
        canManageBusinessUnit(scope, existing?.workspaceId);
      if (!managed) {
        return HttpResponse.json(
          {
            code: "forbidden",
            message: "Only a Project, Business Unit or Organization Admin can change this",
          },
          { status: 403 },
        );
      }
    }
    const project = updateProjectRecord(String(params.id), patch);
    if (!project) {
      return HttpResponse.json({ code: "not_found", message: "not found" }, { status: 404 });
    }
    return HttpResponse.json(project);
  }),

  // Per-project agent-access overrides. Mirrors
  // app/api/projects/:id/agent-access-overrides/route.ts exactly — see
  // [[msw-dual-runtime-mutation-rule]].
  http.get("/api/projects/:id/agent-access-overrides", async ({ params }) => {
    await lag();
    return HttpResponse.json(listOverridesForProject(String(params.id)));
  }),
  http.put("/api/projects/:id/agent-access-overrides", async ({ params, request, cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });
    const body = (await request.json()) as AgentAccessOverrideInput;
    if (!body?.role || !body?.phase || !body?.involvement) {
      return HttpResponse.json(
        { code: "invalid_input", message: "role, phase and involvement are required" },
        { status: 422 },
      );
    }
    const created = setOverride(String(params.id), body.role, body.phase, body.involvement, session.user.name);
    return HttpResponse.json(created, { status: 201 });
  }),
  http.delete("/api/projects/:id/agent-access-overrides", async ({ params, request }) => {
    await lag();
    const url = new URL(request.url);
    const role = url.searchParams.get("role");
    const phase = url.searchParams.get("phase");
    if (!role || !phase) {
      return HttpResponse.json({ code: "invalid_input", message: "role and phase are required" }, { status: 422 });
    }
    const ok = removeOverride(String(params.id), role, phase as Phase);
    if (!ok) return HttpResponse.json({ code: "not_found" }, { status: 404 });
    return new HttpResponse(null, { status: 204 });
  }),

  // ───── Governance approvals (project creation, model credentials) ─────
  // Mirrors app/api/governance-approvals/** exactly — see
  // [[msw-dual-runtime-mutation-rule]].
  http.get("/api/governance-approvals", async ({ request, cookies }) => {
    await lag();
    const url = new URL(request.url);
    // The query param is the caller's narrowing choice; the viewer's own scope
    // is applied unconditionally on top, so "all" can only widen to the scopes
    // they actually ADMINISTER — read access to a parent unit is not enough for
    // its governance queue.
    const workspaceId = url.searchParams.get("workspaceId") ?? undefined;
    const scope = scopeFromCookies(cookies);
    return HttpResponse.json(
      listGovernanceApprovals(workspaceId).filter(
        (a) =>
          canReadGovernanceApproval(scope, a.workspaceId, a.projectId) ||
          // You always see what you raised, wherever it climbed to.
          (a.requestedById != null && a.requestedById === scope.identityId),
      ),
    );
  }),

  // ───── Notifications ─────
  // Mirrors app/api/notifications/route.ts. Addressed to an identity or a
  // role; never an "everything" list.
  http.get("/api/notifications", async ({ cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });
    return HttpResponse.json(
      listNotifications(
        notificationViewer(scopeFromCookies(cookies).identityId, effectivePlatformRole(session)),
      ),
    );
  }),
  http.post("/api/notifications", async ({ cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });
    return HttpResponse.json({
      marked: markNotificationsRead(
        notificationViewer(scopeFromCookies(cookies).identityId, effectivePlatformRole(session)),
      ),
    });
  }),

  // Raise a request. Mirrors app/api/governance-approvals/route.ts::POST.
  http.post("/api/governance-approvals", async ({ request, cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });

    const role = effectivePlatformRole(session);
    if (!canRaiseRequest(role)) {
      return HttpResponse.json(
        {
          code: "forbidden",
          message:
            "Organization Admins are the final approval authority and cannot raise requests.",
        },
        { status: 403 },
      );
    }

    const parsed = RequestCreateInput.safeParse(await request.json());
    if (!parsed.success) {
      return HttpResponse.json(
        { code: "invalid", message: parsed.error.issues[0]?.message ?? "Invalid request" },
        { status: 400 },
      );
    }
    const input = parsed.data;
    // Mirrors the route's tier check — see that file.
    if (!canRaiseType(role, input.type)) {
      return HttpResponse.json(
        {
          code: "forbidden",
          message: `A ${role ? ROLE_META[role].label : "viewer"} cannot raise a ${REQUEST_TYPE_LABEL[input.type]} request.`,
        },
        { status: 403 },
      );
    }
    const scope = scopeFromCookies(cookies);
    if (!scope.isOrgWide && !scope.businessUnitIds.includes(input.workspaceId)) {
      return HttpResponse.json({ code: "not_found", message: "not found" }, { status: 404 });
    }

    const unit = fxListWorkspaces().find((w) => String(w.id) === input.workspaceId);
    if (!unit) return HttpResponse.json({ code: "not_found", message: "not found" }, { status: 404 });
    const project = input.projectId
      ? PROJECTS.find((p) => String(p.id) === input.projectId)
      : undefined;

    return HttpResponse.json(
      createGovernanceApproval({
        type: input.type,
        workspaceId: String(unit.id),
        workspaceName: unit.displayName,
        projectId: project ? String(project.id) : null,
        projectName: project?.name ?? null,
        title: input.title,
        summary: `${REQUEST_TYPE_LABEL[input.type]} requested by ${session.user.name}.`,
        description: input.description,
        priority: input.priority,
        attachments: input.attachments,
        requestedBy: session.user.name,
        requestedById: scope.identityId,
        requestedByRole: role,
        targetRef: input.projectId ?? String(unit.id),
        // The agent asked for — what routes stage two. See the Next route.
        payload: input.phase ? { phase: input.phase } : null,
      }),
      { status: 201 },
    );
  }),

  // Withdraw your own request. Mirrors .../[id]/cancel/route.ts.
  http.post("/api/governance-approvals/:id/cancel", async ({ params, request, cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });

    const body = (await request.json().catch(() => ({}))) as { reason?: string };
    const result = cancelGovernanceApproval(
      String(params.id),
      session.user.name,
      scopeFromCookies(cookies).identityId,
      body.reason,
    );
    if (result === undefined) return HttpResponse.json({ code: "not_found" }, { status: 404 });
    if (result === "forbidden") {
      return HttpResponse.json(
        {
          code: "forbidden",
          message:
            "Only the person who raised a request can withdraw it, and only while it is open.",
        },
        { status: 403 },
      );
    }
    return HttpResponse.json(result);
  }),

  // Climb one tier. Mirrors .../[id]/escalate/route.ts.
  http.post("/api/governance-approvals/:id/escalate", async ({ params, request, cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });

    const body = (await request.json().catch(() => ({}))) as { reason?: string };
    const result = escalateGovernanceApproval(String(params.id), session.user.name, body.reason);
    if (result === undefined) return HttpResponse.json({ code: "not_found" }, { status: 404 });
    if (result === "top") {
      return HttpResponse.json(
        {
          code: "conflict",
          message:
            "This request is already with the Organization Admin — there is no tier above to escalate to.",
        },
        { status: 409 },
      );
    }
    return HttpResponse.json(result);
  }),
  http.post("/api/governance-approvals/:id/decide", async ({ params, request, cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });

    const id = String(params.id);
    const body = (await request.json()) as GovernanceApprovalDecisionInput;
    const approval = getGovernanceApproval(id);
    if (!approval) return HttpResponse.json({ code: "not_found" }, { status: 404 });

    // Mirrors the route's eligibility gate — see that file for why the
    // endpoint enforces this and not only the sheet.
    const eligibility = canDecideRequest({
      currentApproverRole: (approval.currentApproverRole as PlatformRole | null) ?? null,
      requestedById: approval.requestedById,
      status: approval.status,
      viewerRole: effectivePlatformRole(session),
      viewerIdentityId: scopeFromCookies(cookies).identityId,
    });
    if (!eligibility.allowed) {
      return HttpResponse.json(
        { code: "forbidden", message: eligibility.reason ?? "Not yours to decide." },
        { status: 403 },
      );
    }
    // The right role is not enough — it must be the right one OF that role.
    // See the route handler for why this matters most to cross_bu_assignment.
    if (
      !canReadGovernanceApproval(
        scopeFromCookies(cookies),
        approval.workspaceId,
        approval.projectId,
      )
    ) {
      return HttpResponse.json(
        { code: "forbidden", message: "This request belongs to another business unit." },
        { status: 403 },
      );
    }

    const decided = decideGovernanceApproval(id, body.decision, session.user.name, body.reason);
    if (!decided) return HttpResponse.json({ code: "not_found" }, { status: 404 });

    if (approval.type === "project_creation") {
      if (body.decision === "approve") activateProject(approval.targetRef, session.user.name);
      else rejectProjectCreation(approval.targetRef, session.user.name, body.reason);
    } else if (approval.type === "model_credential") {
      if (body.decision === "approve") activateModelProvider(approval.targetRef, session.user.name);
      else rejectModelProvider(approval.targetRef, session.user.name, body.reason);
    } else if (approval.type === "budget_increase" && body.decision === "approve") {
      const requestedAmountUsd = approval.payload?.requestedAmountUsd;
      if (typeof requestedAmountUsd === "number") {
        fxPatchWorkspace(approval.targetRef, { monthlyBudgetUsd: requestedAmountUsd });
      }
    } else if (approval.type === "project_archive" && body.decision === "approve") {
      setProjectArchived(approval.targetRef, true);
    } else if (approval.type === "cross_bu_assignment" && body.decision === "approve") {
      fxApplyCrossBuAssignment(approval, session.user.name);
    } else if (
      (approval.type === "agent_default_org" ||
        approval.type === "agent_default_workspace" ||
        approval.type === "agent_default_project") &&
      body.decision === "approve"
    ) {
      publishVersion(approval.targetRef, session.user.name);
    }

    return HttpResponse.json(decided);
  }),

  // ───── Agent Studio prompt profiles (Behavior tab) ─────
  // Mirrors app/api/agent-profiles/** exactly — see
  // [[msw-dual-runtime-mutation-rule]].
  http.get("/api/agent-profiles/summary", async ({ request }) => {
    await lag();
    const url = new URL(request.url);
    const sp = url.searchParams;
    const scope = (sp.get("scope") ?? "workspace") as ProfileScope;
    const scopeId = sp.get("scope_id");
    const agents = getAgentProfileSummary(scope, scopeId, {
      workspaceId: sp.get("workspace_id"),
      projectId: sp.get("project_id"),
      userId: sp.get("user_id"),
    });
    return HttpResponse.json({ agents });
  }),
  http.get("/api/agent-profiles/versions", async ({ request }) => {
    await lag();
    const url = new URL(request.url);
    const sp = url.searchParams;
    const agentId = sp.get("agent_id");
    if (!agentId) return HttpResponse.json({ code: "invalid_input", message: "agent_id is required" }, { status: 422 });
    const scope = (sp.get("scope") ?? "workspace") as ProfileScope;
    const scopeId = sp.get("scope_id");
    return HttpResponse.json({ versions: listVersions(agentId, scope, scopeId) });
  }),
  http.post("/api/agent-profiles/draft", async ({ request, cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });

    const body = (await request.json()) as AgentProfileDraftInput;
    if (!body?.agent_id || !body?.scope) {
      return HttpResponse.json({ code: "invalid_input", message: "agent_id and scope are required" }, { status: 422 });
    }
    const FIELD_CAPS = { prompt_prepend: 4000, prompt_append: 4000, output_contract_extra: 2000 } as const;
    const violations = (
      [
        ["prompt_prepend", body.prompt_prepend ?? ""],
        ["prompt_append", body.prompt_append ?? ""],
        ["output_contract_extra", body.output_contract_extra ?? ""],
      ] as const
    )
      .filter(([field, value]) => value.length > FIELD_CAPS[field])
      .map(([field]) => ({
        field,
        code: "too_long",
        message: `Over the ${FIELD_CAPS[field].toLocaleString()}-character limit.`,
      }));
    if (violations.length > 0) {
      return HttpResponse.json({ detail: { violations } }, { status: 422 });
    }

    const created = createDraft({
      agentId: body.agent_id,
      scope: body.scope,
      scopeId: body.scope_id ?? null,
      promptPrepend: body.prompt_prepend ?? "",
      promptAppend: body.prompt_append ?? "",
      outputContractExtra: body.output_contract_extra ?? "",
      createdBy: session.user.name,
    });
    return HttpResponse.json(created);
  }),
  http.post("/api/agent-profiles/:id/publish", async ({ params, cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });
    // Mirrors app/api/agent-profiles/[id]/publish/route.ts. In the browser THIS
    // is the handler that answers, so an ownership rule enforced only in the
    // Next route would not be enforced at all where people actually click.
    const scope = versionScope(String(params.id));
    if (!scope) return HttpResponse.json({ code: "not_found" }, { status: 404 });
    if (!canPublishAtTier(effectivePlatformRole(session), scope)) {
      return HttpResponse.json(
        { code: "forbidden", message: "You don't own this tier's defaults." },
        { status: 403 },
      );
    }
    const published = publishVersion(String(params.id), session.user.name);
    if (!published) return HttpResponse.json({ code: "not_found" }, { status: 404 });
    return HttpResponse.json(published);
  }),
  http.post("/api/agent-profiles/:id/unpublish", async ({ params, cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });
    const unpublished = unpublishVersion(String(params.id));
    if (!unpublished) return HttpResponse.json({ code: "not_found" }, { status: 404 });
    return HttpResponse.json(unpublished);
  }),
  http.post("/api/agent-profiles/preview", async ({ request, cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });

    const body = (await request.json()) as AgentProfileDraftInput;
    const preview = buildPreview(
      body.agent_id,
      body.scope,
      body.scope_id ?? null,
      { workspaceId: body.workspace_id, projectId: body.project_id, userId: body.user_id },
      {
        promptPrepend: body.prompt_prepend ?? "",
        promptAppend: body.prompt_append ?? "",
        outputContractExtra: body.output_contract_extra ?? "",
      },
    );
    return HttpResponse.json({ ...preview, warnings: [] });
  }),
  http.post("/api/agent-profiles/:id/propose", async ({ params, request, cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });

    const id = String(params.id);
    const body = (await request.json()) as {
      scope: "org" | "workspace" | "project";
      agentId: string;
      agentLabel: string;
      workspaceId?: string;
      workspaceName?: string;
      projectId?: string;
      projectName?: string;
    };
    const scopeLabel =
      body.scope === "org" ? "organization" : body.scope === "workspace" ? "business unit" : "project";
    const created = createGovernanceApproval({
      type: agentDefaultApprovalType(body.scope),
      workspaceId: body.workspaceId ?? "",
      workspaceName: body.workspaceName ?? "",
      projectId: body.projectId ?? null,
      projectName: body.projectName ?? null,
      title: `${body.agentLabel} default change (${scopeLabel})`,
      summary: `${session.user.name} proposed a ${body.agentLabel} behavior change for the ${scopeLabel} default.`,
      requestedBy: session.user.name,
      targetRef: id,
      payload: { agentId: body.agentId, scope: body.scope },
    });
    return HttpResponse.json(created, { status: 201 });
  }),

  // ───── Agent Studio skills (Skills tab) ─────
  // Mirrors app/api/agent-skills/** exactly — see [[msw-dual-runtime-mutation-rule]].
  http.get("/api/agent-skills", async ({ request }) => {
    await lag();
    const sp = new URL(request.url).searchParams;
    const agentId = sp.get("agent_id");
    if (!agentId) {
      return HttpResponse.json({ code: "invalid_input", message: "agent_id is required" }, { status: 422 });
    }
    const scope = (sp.get("scope") ?? "workspace") as SkillScope;
    const scopeId = sp.get("scope_id");
    return HttpResponse.json({ skills: fxListAgentSkills(agentId, scope, scopeId) });
  }),
  // Registered BEFORE the generic ":origin/:skillKey" detail route below —
  // both are 2-segment GET paths, and MSW matches in registration order, so
  // the more specific "/versions" literal has to win first or every version
  // list request would be swallowed as a (nonexistent) skill detail lookup.
  http.get("/api/agent-skills/:skillKey/versions", async ({ params, request }) => {
    await lag();
    const sp = new URL(request.url).searchParams;
    const agentId = sp.get("agent_id");
    const scope = (sp.get("scope") ?? "workspace") as SkillScope;
    const scopeId = sp.get("scope_id");
    if (!agentId) {
      return HttpResponse.json({ code: "invalid_input", message: "agent_id is required" }, { status: 422 });
    }
    return HttpResponse.json({
      versions: fxListAgentSkillVersions(String(params.skillKey), agentId, scope, scopeId),
    });
  }),
  http.get("/api/agent-skills/:origin/:skillKey", async ({ params, request }) => {
    await lag();
    const sp = new URL(request.url).searchParams;
    const agentId = sp.get("agent_id");
    const scope = (sp.get("scope") ?? "workspace") as SkillScope;
    const scopeId = sp.get("scope_id");
    if (!agentId) {
      return HttpResponse.json({ code: "invalid_input", message: "agent_id is required" }, { status: 422 });
    }
    const skill = fxGetAgentSkill(String(params.skillKey), agentId, scope, scopeId);
    if (!skill) return HttpResponse.json({ code: "not_found" }, { status: 404 });
    return HttpResponse.json(skill);
  }),
  http.post("/api/agent-skills", async ({ request, cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });
    const body = (await request.json()) as {
      agent_id: string;
      scope: SkillScope;
      scope_id?: string | null;
      skill_key: string;
      display_name: string;
      description?: string;
      when_to_use?: string;
      body: string;
    };
    if (!body?.agent_id || !body?.scope || !body?.skill_key || !body?.display_name || !body?.body) {
      return HttpResponse.json(
        { code: "invalid_input", message: "agent_id, scope, skill_key, display_name and body are required" },
        { status: 422 },
      );
    }
    const created = fxCreateAgentSkill({
      agentId: body.agent_id,
      scope: body.scope,
      scopeId: body.scope_id ?? null,
      skillKey: body.skill_key,
      displayName: body.display_name,
      description: body.description ?? "",
      whenToUse: body.when_to_use ?? "",
      body: body.body,
      createdBy: session.user.name,
    });
    return HttpResponse.json(created, { status: 201 });
  }),
  http.put("/api/agent-skills/:skillKey", async ({ params, request, cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });
    const body = (await request.json()) as {
      agent_id: string;
      scope: SkillScope;
      scope_id?: string | null;
      display_name: string;
      description?: string;
      when_to_use?: string;
      body: string;
    };
    const updated = fxUpdateAgentSkill(String(params.skillKey), body.agent_id, body.scope, body.scope_id ?? null, {
      displayName: body.display_name,
      description: body.description ?? "",
      whenToUse: body.when_to_use ?? "",
      body: body.body,
      updatedBy: session.user.name,
    });
    if (!updated) return HttpResponse.json({ code: "not_found" }, { status: 404 });
    return HttpResponse.json(updated);
  }),
  http.delete("/api/agent-skills/:skillKey", async ({ params, request }) => {
    await lag();
    const sp = new URL(request.url).searchParams;
    const agentId = sp.get("agent_id");
    const scope = (sp.get("scope") ?? "workspace") as SkillScope;
    const scopeId = sp.get("scope_id");
    if (!agentId) {
      return HttpResponse.json({ code: "invalid_input", message: "agent_id is required" }, { status: 422 });
    }
    const deleted = fxDeleteAgentSkill(String(params.skillKey), agentId, scope, scopeId);
    if (!deleted) return HttpResponse.json({ code: "not_found" }, { status: 404 });
    return HttpResponse.json({ deleted: true });
  }),
  http.post("/api/agent-skills/toggle", async ({ request }) => {
    await lag();
    const body = (await request.json()) as {
      agent_id: string;
      scope: SkillScope;
      scope_id?: string | null;
      origin: "vendor" | "custom";
      skill_key: string;
      enabled: boolean;
    };
    fxToggleAgentSkill(body.skill_key, body.agent_id, body.scope, body.scope_id ?? null, body.enabled);
    return HttpResponse.json({ origin: body.origin, skill_key: body.skill_key, enabled: body.enabled });
  }),

  http.post("/api/workspaces/:id/budget-increase-request", async ({ params, request, cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });

    const workspace = fxGetWorkspace(String(params.id));
    if (!workspace) return HttpResponse.json({ code: "not_found", message: "not found" }, { status: 404 });

    const body = (await request.json()) as { requestedAmountUsd?: number; reason?: string };
    if (!body?.requestedAmountUsd || body.requestedAmountUsd <= 0) {
      return HttpResponse.json(
        { code: "invalid_input", message: "requestedAmountUsd must be a positive number" },
        { status: 422 },
      );
    }

    const created = createGovernanceApproval({
      type: "budget_increase",
      workspaceId: workspace.id,
      workspaceName: workspace.displayName,
      title: `Budget increase: ${workspace.displayName}`,
      summary: `${session.user.name} requested a monthly budget increase to $${body.requestedAmountUsd.toLocaleString()} for ${workspace.displayName}${body.reason ? ` — "${body.reason}"` : ""}.`,
      requestedBy: session.user.name,
      targetRef: workspace.id,
      payload: { requestedAmountUsd: body.requestedAmountUsd },
    });
    return HttpResponse.json(created, { status: 201 });
  }),

  // ───── Model catalogue cascade (org grants → what a unit may use → onboarded
  // providers). Mirrors app/api/model/** exactly — see
  // [[msw-dual-runtime-mutation-rule]].
  http.get("/api/model/catalog", async () => {
    await lag();
    return HttpResponse.json(getModelCatalog());
  }),
  // Mirrors app/api/model/grant-matrix/route.ts. Org Admin only — the matrix
  // is every unit's standing against every model.
  http.get("/api/model/grant-matrix", async ({ cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });
    if (effectivePlatformRole(session) !== "org_admin") {
      return HttpResponse.json({ code: "forbidden", message: "not found" }, { status: 403 });
    }
    return HttpResponse.json(getModelGrantMatrix());
  }),
  http.get("/api/model/allowed/org", async () => {
    await lag();
    return HttpResponse.json(getOrgModelGrants());
  }),
  http.put("/api/model/allowed/org", async ({ request, cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });
    if (effectivePlatformRole(session) !== "org_admin") {
      return HttpResponse.json(
        { code: "forbidden", message: "Only an Organization Admin can change model grants." },
        { status: 403 },
      );
    }
    const body = (await request.json()) as { entries: OrgModelGrant[] };
    return HttpResponse.json(setOrgModelGrants(body.entries));
  }),
  // What a unit may use, plus whether anything still needs a key. Mirrors
  // app/api/model/availability/route.ts.
  http.get("/api/model/availability", async ({ request, cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });
    const workspaceId = new URL(request.url).searchParams.get("workspaceId");
    if (!workspaceId) {
      return HttpResponse.json({ code: "invalid_input", message: "workspaceId is required" }, { status: 422 });
    }
    const scope = scopeFromCookies(cookies);
    if (!canReadBusinessUnit(scope, workspaceId)) {
      return HttpResponse.json({ code: "not_found" }, { status: 404 });
    }
    return HttpResponse.json(getBuModelAvailability(workspaceId));
  }),
  http.get("/api/model/allowed/bu", async ({ request }) => {
    await lag();
    const url = new URL(request.url);
    const workspaceId = url.searchParams.get("workspaceId");
    if (!workspaceId) {
      return HttpResponse.json({ code: "invalid_input", message: "workspaceId is required" }, { status: 422 });
    }
    return HttpResponse.json(getBuAllowedModels(workspaceId));
  }),
  http.put("/api/model/allowed/bu", async ({ request, cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });
    const url = new URL(request.url);
    const workspaceId = url.searchParams.get("workspaceId");
    if (!workspaceId) {
      return HttpResponse.json({ code: "invalid_input", message: "workspaceId is required" }, { status: 422 });
    }
    if (effectivePlatformRole(session) !== "org_admin") {
      return HttpResponse.json(
        {
          code: "forbidden",
          message: "Only an Organization Admin can grant models to a business unit.",
        },
        { status: 403 },
      );
    }
    const body = (await request.json()) as { entries: ModelAllowEntry[] };
    return HttpResponse.json(setBuModelGrants(workspaceId, body.entries));
  }),

  // Last tier — what a project selected from what its BU was granted. Mirrors
  // app/api/model/allowed/project/route.ts exactly, including resolving the
  // project's parent Business Unit here rather than inside the fixture module.
  http.get("/api/model/allowed/project", async ({ request }) => {
    await lag();
    const url = new URL(request.url);
    const projectId = url.searchParams.get("projectId");
    if (!projectId) {
      return HttpResponse.json({ code: "invalid_input", message: "projectId is required" }, { status: 422 });
    }
    const project = fxGetProjectById(projectId);
    if (!project) return HttpResponse.json({ code: "not_found" }, { status: 404 });
    return HttpResponse.json(getProjectModelSelection(projectId, project.workspaceId ?? null));
  }),
  http.put("/api/model/allowed/project", async ({ request, cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });
    const url = new URL(request.url);
    const projectId = url.searchParams.get("projectId");
    if (!projectId) {
      return HttpResponse.json({ code: "invalid_input", message: "projectId is required" }, { status: 422 });
    }
    const project = fxGetProjectById(projectId);
    if (!project) return HttpResponse.json({ code: "not_found" }, { status: 404 });
    const body = (await request.json()) as {
      selected?: ModelAllowEntry[];
      defaultKey?: string | null;
    };
    return HttpResponse.json(
      setProjectModelSelection(projectId, project.workspaceId ?? null, {
        selected: body.selected ?? [],
        defaultKey: body.defaultKey,
      }),
    );
  }),

  http.get("/api/model/providers", async ({ request, cookies }) => {
    await lag();
    const url = new URL(request.url);
    // Mirrors the route's `scope=all` — see that file for why one scope
    // cannot answer "which providers is this organization using".
    if (url.searchParams.get("scope") === "all") {
      const scope = scopeFromCookies(cookies);
      return HttpResponse.json(
        listAllModelProviders(scope.isOrgWide ? null : scope.businessUnitIds),
      );
    }
    const workspaceId = url.searchParams.get("workspaceId");
    return HttpResponse.json(listModelProviders(workspaceId || null));
  }),
  http.post("/api/model/providers", async ({ request, cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });
    const body = (await request.json()) as CreateModelProviderInput;
    if (!body?.provider || !body?.display_name) {
      return HttpResponse.json(
        { code: "invalid_input", message: "provider and display_name are required" },
        { status: 422 },
      );
    }
    const role = effectivePlatformRole(session);
    const created = createModelProvider(body, { role, displayName: session.user.name });
    return HttpResponse.json(created, { status: 201 });
  }),
  http.patch("/api/model/providers/:id", async ({ params, request }) => {
    await lag();
    const body = (await request.json()) as {
    display_name?: string;
    enabled_models?: string[];
    api_key?: string;
    api_base?: string | null;
    rpm_limit?: number | null;
    tpm_limit?: number | null;
    cost_limit_usd?: number | null;
  };
    const updated = updateModelProvider(String(params.id), body);
    if (!updated) return HttpResponse.json({ code: "not_found" }, { status: 404 });
    return HttpResponse.json(updated);
  }),
  http.delete("/api/model/providers/:id", async ({ params }) => {
    await lag();
    const ok = deleteModelProvider(String(params.id));
    if (!ok) return HttpResponse.json({ code: "not_found" }, { status: 404 });
    return new HttpResponse(null, { status: 204 });
  }),
  http.post("/api/model/providers/:id/verify", async ({ params }) => {
    await lag();
    const result = verifyModelProvider(String(params.id));
    if (!result) return HttpResponse.json({ code: "not_found" }, { status: 404 });
    return HttpResponse.json(result);
  }),
  http.post("/api/model/providers/probe", async ({ request }) => {
    await lag();
    const body = (await request.json()) as {
      provider: string;
      api_key: string;
      api_base?: string;
      model?: string;
    };
    return HttpResponse.json(probeModelProvider(body));
  }),
  http.put("/api/model/default", async ({ request }) => {
    await lag();
    const body = (await request.json()) as { offering_id: string };
    setModelDefaultOffering(body.offering_id);
    return HttpResponse.json({ ok: true });
  }),

  // ───── Runs ─────
  http.get("/api/runs", async ({ request }) => {
    await lag();
    const url = new URL(request.url);
    const projectId = url.searchParams.get("projectId");
    const status = url.searchParams.get("status");
    let list = [...RUNS];
    if (projectId) list = list.filter((r) => r.projectId === projectId);
    if (status) list = list.filter((r) => r.status === status);
    return HttpResponse.json(page(list, url.searchParams));
  }),

  http.get("/api/runs/:id", async ({ params }) => {
    await lag();
    const run = RUNS.find((r) => r.id === params.id);
    if (!run) {
      return HttpResponse.json({ code: "not_found", message: "Run not found" }, { status: 404 });
    }
    return HttpResponse.json(run);
  }),

  http.get("/api/runs/:id/steps", async ({ params }) => {
    await lag();
    const steps = STEPS.filter((s) => s.runId === params.id);
    return HttpResponse.json(steps);
  }),

  http.post("/api/runs/:id/approvals", async ({ params, request }) => {
    await lag();
    const run = RUNS.find((r) => r.id === params.id);
    if (!run) {
      return HttpResponse.json({ code: "not_found", message: "Run not found" }, { status: 404 });
    }
    const body = (await request.json()) as {
      decision: "approve" | "reject" | "retry";
      reason?: string;
      idempotencyKey: string;
    };
    run.status =
      body.decision === "approve"
        ? "approved"
        : body.decision === "reject"
          ? "rejected"
          : "running";
    return HttpResponse.json({
      id: `appr_${Date.now()}`,
      runId: run.id,
      artifactId: null,
      decision: body.decision,
      decidedBy: "u_admin",
      decidedAt: new Date().toISOString(),
      reason: body.reason,
    });
  }),

  // ───── Artifacts ─────
  http.get("/api/projects/:id/artifacts", async ({ params, request }) => {
    await lag();
    const url = new URL(request.url);
    const phase = url.searchParams.get("phase");
    let list = ARTIFACTS.filter((a) => a.projectId === params.id);
    if (phase) list = list.filter((a) => a.phase === phase);
    return HttpResponse.json(list);
  }),

  http.get("/api/artifacts/:id", async ({ params }) => {
    await lag();
    const a = ARTIFACTS.find((x) => x.id === params.id);
    if (!a) {
      return HttpResponse.json({ code: "not_found", message: "not found" }, { status: 404 });
    }
    return HttpResponse.json(a);
  }),

  http.patch("/api/artifacts/:id", async ({ params, request }) => {
    await lag();
    const a = ARTIFACTS.find((x) => x.id === params.id);
    if (!a) {
      return HttpResponse.json({ code: "not_found", message: "not found" }, { status: 404 });
    }
    const patch = (await request.json()) as {
      title?: string;
      status?: (typeof a)["status"];
      body?: (typeof a)["body"];
    };
    if (patch.title !== undefined) a.title = patch.title;
    if (patch.status !== undefined) a.status = patch.status;
    if (patch.body !== undefined) a.body = patch.body;
    a.version += 1;
    a.updatedAt = new Date().toISOString();
    return HttpResponse.json(a);
  }),

  // ───── Capabilities ─────
  // Mirrors app/api/capabilities/projects/:id/agents/** exactly — see
  // [[msw-dual-runtime-mutation-rule]].
  http.get("/api/capabilities/projects/:id/agents", async ({ params }) => {
    await lag();
    const data = getProjectCapabilitiesData(String(params.id));
    if (!data) return HttpResponse.json({ code: "not_found", message: "Project not found" }, { status: 404 });
    return HttpResponse.json(data);
  }),
  http.put("/api/capabilities/projects/:id/agents/:agentId/curated", async ({ params, request }) => {
    await lag();
    const body = (await request.json()) as { disabled: string[] };
    const result = setCuratedDisabled(String(params.id), String(params.agentId), body.disabled ?? []);
    return HttpResponse.json(result);
  }),

  // ───── MCP registry ─────
  // Mirrors app/api/mcp/registry/route.ts's GET exactly — see
  // [[msw-dual-runtime-mutation-rule]].
  http.get("/api/mcp/registry", async ({ request, cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });
    const url = new URL(request.url);
    const activeOnly = url.searchParams.get("active_only") === "true";
    const scope = scopeFromCookies(cookies);
    return HttpResponse.json(
      listMcpServersForScope(scope.isOrgWide ? null : scope.businessUnitIds, activeOnly),
    );
  }),
  // Registering lands at the level the registrar governs. Mirrors the POST in
  // app/api/mcp/registry/route.ts — a browser-side write and a route write
  // never share memory, so both runtimes need the rule.
  http.post("/api/mcp/registry", async ({ request, cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });
    const role = effectivePlatformRole(session);
    if (role !== "org_admin" && role !== "bu_admin") {
      return HttpResponse.json(
        {
          code: "forbidden",
          message: "Only an Organization or Business Unit Admin registers an MCP server.",
        },
        { status: 403 },
      );
    }
    const body = (await request.json()) as Partial<McpServer> & { server_name?: string };
    if (!body.server_name) {
      return HttpResponse.json({ code: "bad_request", message: "Name the server." }, { status: 400 });
    }
    return HttpResponse.json(createMcpServerRecord({ ...body, server_name: body.server_name }));
  }),

  // ───── Connectors ─────
  // REQ-M4-05: response shape is aligned to the real Connector Zod schema
  // (id, tenantId, kind, name, installed, health, capabilities, lastCheckedAt, account?).
  // MSW is retained for Storybook/component tests — retired from the runtime path
  // (NEXT_PUBLIC_API_MOCKS=off routes through the BFF to FastAPI instead).
  http.get("/api/connectors", async ({ request, cookies }) => {
    await lag();
    // Mirrors app/api/connectors/route.ts — org-wide plus the unit's own,
    // intersected with the units the viewer may read. The "no workspaceId means
    // the whole tenant" default survives only for an unbounded viewer.
    const workspaceId = new URL(request.url).searchParams.get("workspaceId");
    const scope = scopeFromCookies(cookies);
    return HttpResponse.json(
      visibleConnectorsForScope(
        CONNECTORS,
        workspaceId,
        scope.isOrgWide ? null : scope.businessUnitIds,
      ),
    );
  }),
  // What a project may use, and its own credentials against those things.
  // Mirrors app/api/projects/[id]/integrations/route.ts.
  http.get("/api/projects/:id/integrations", async ({ params, cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });
    const id = String(params.id);
    const project = PROJECTS.find((p) => String(p.id) === id);
    if (!project) return HttpResponse.json({ code: "not_found" }, { status: 404 });
    const scope = scopeFromCookies(cookies);
    const reachable =
      scope.isOrgWide ||
      (project.workspaceId ? scope.businessUnitIds.includes(String(project.workspaceId)) : false);
    if (!reachable) return HttpResponse.json({ code: "not_found" }, { status: 404 });
    return HttpResponse.json(listProjectIntegrations(id, mswViewerId(session)));
  }),
  http.put("/api/projects/:id/integrations", async ({ request, params, cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });
    const id = String(params.id);
    const project = PROJECTS.find((p) => String(p.id) === id);
    if (!project) return HttpResponse.json({ code: "not_found" }, { status: 404 });
    const scope = scopeFromCookies(cookies);
    const reachable =
      scope.isOrgWide ||
      (project.workspaceId ? scope.businessUnitIds.includes(String(project.workspaceId)) : false);
    if (!reachable) return HttpResponse.json({ code: "not_found" }, { status: 404 });

    const parsed = ProjectIntegrationCredentialInput.safeParse(await request.json());
    if (!parsed.success) {
      return HttpResponse.json(
        { code: "bad_request", message: parsed.error.issues[0]?.message ?? "Invalid credential." },
        { status: 400 },
      );
    }
    const approved = listProjectIntegrations(id, mswViewerId(session));
    if (!approved.some((i) => i.kind === parsed.data.kind && i.id === parsed.data.targetId)) {
      return HttpResponse.json(
        { code: "forbidden", message: "That integration is not approved for this project." },
        { status: 403 },
      );
    }
    const who = session.user?.name ?? session.user?.email ?? "Someone";
    return HttpResponse.json(upsertProjectCredential(id, parsed.data, who, mswViewerId(session)));
  }),

  // Built-in role permissions. Mirrors app/api/admin/role-permissions/route.ts.
  http.get("/api/admin/role-permissions", async ({ cookies }) => {
    await lag();
    if (!sessionFromCookies(cookies)) {
      return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });
    }
    return HttpResponse.json(listRolePermissions());
  }),
  http.put("/api/admin/role-permissions", async ({ request, cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });
    if (effectivePlatformRole(session) !== "org_admin") {
      return HttpResponse.json(
        { code: "forbidden", message: "Only an Organization Admin can change a role." },
        { status: 403 },
      );
    }
    const body = (await request.json()) as {
      role?: string;
      permissions?: string[];
      reset?: boolean;
    };
    if (!body.role) {
      return HttpResponse.json({ code: "bad_request", message: "Name the role." }, { status: 400 });
    }
    try {
      return HttpResponse.json(
        body.reset
          ? resetRolePermissions(body.role as PlatformRole)
          : setRolePermissions(body.role as PlatformRole, body.permissions ?? []),
      );
    } catch (e) {
      return HttpResponse.json(
        { code: "forbidden", message: e instanceof Error ? e.message : "Cannot change that role." },
        { status: 403 },
      );
    }
  }),

  // The access matrix. Mirrors app/api/integrations/access/route.ts.
  http.get("/api/integrations/access", async ({ cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });
    const scope = scopeFromCookies(cookies);
    return HttpResponse.json(
      listIntegrationAccess(scope.isOrgWide ? null : scope.businessUnitIds),
    );
  }),
  http.post("/api/integrations/access", async ({ request, cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });
    const p = new URL(request.url).searchParams;
    if (effectivePlatformRole(session) !== "org_admin") {
      return HttpResponse.json(
        { code: "forbidden", message: "Only an Organization Admin grants integration access." },
        { status: 403 },
      );
    }
    const kind = p.get("kind") === "mcp" ? "mcp" : "connector";
    const targetId = p.get("id");
    if (!targetId) {
      return HttpResponse.json(
        { code: "bad_request", message: "Name the integration." },
        { status: 400 },
      );
    }
    const workspaceId = p.get("workspaceId");
    if (!workspaceId) {
      return HttpResponse.json(
        { code: "bad_request", message: "Name the business unit." },
        { status: 400 },
      );
    }
    const units =
      kind === "mcp"
        ? grantMcpToUnit(targetId, workspaceId)
        : grantConnectorToUnit(targetId, workspaceId);
    return HttpResponse.json({ ok: true, remainingUnits: units });
  }),
  http.delete("/api/integrations/access", async ({ request, cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });

    const p = new URL(request.url).searchParams;
    const kind = p.get("kind") === "mcp" ? "mcp" : "connector";
    const targetId = p.get("id");
    const level = p.get("level");
    if (!targetId) {
      return HttpResponse.json(
        { code: "bad_request", message: "Name the integration." },
        { status: 400 },
      );
    }
    const role = effectivePlatformRole(session);
    const scope = scopeFromCookies(cookies);

    if (level === "unit") {
      const workspaceId = p.get("workspaceId");
      if (!workspaceId) {
        return HttpResponse.json({ code: "bad_request", message: "Name the unit." }, { status: 400 });
      }
      if (role !== "org_admin") {
        return HttpResponse.json(
          {
            code: "forbidden",
            message: "Only an Organization Admin can take an integration away from a business unit.",
          },
          { status: 403 },
        );
      }
      const left =
        kind === "mcp"
          ? revokeMcpGrant(targetId, workspaceId)
          : revokeConnectorGrant(targetId, workspaceId);
      return HttpResponse.json({ ok: true, remainingUnits: left });
    }

    if (level === "project") {
      const projectId = p.get("projectId");
      if (!projectId) {
        return HttpResponse.json(
          { code: "bad_request", message: "Name the project." },
          { status: 400 },
        );
      }
      if (role !== "org_admin" && role !== "bu_admin") {
        return HttpResponse.json(
          { code: "forbidden", message: "Only an admin tier can revoke a project's integration." },
          { status: 403 },
        );
      }
      const project = PROJECTS.find((x) => String(x.id) === projectId);
      if (!project) return HttpResponse.json({ code: "not_found" }, { status: 404 });
      if (!scope.isOrgWide && !scope.businessUnitIds.includes(String(project.workspaceId))) {
        return HttpResponse.json({ code: "not_found" }, { status: 404 });
      }
      return HttpResponse.json({
        ok: true,
        changed: revokeProjectIntegration(projectId, kind, targetId),
      });
    }

    return HttpResponse.json(
      { code: "bad_request", message: "level must be 'unit' or 'project'." },
      { status: 400 },
    );
  }),

  // Which connector kinds the Org Admin permits. Mirrors
  // app/api/connectors/grants/route.ts — the static `grants` segment wins over
  // the `:kind` matcher below, so this must stay declared before it.
  http.get("/api/connectors/grants", async ({ request, cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });
    const workspaceId = new URL(request.url).searchParams.get("workspaceId");
    const scope = scopeFromCookies(cookies);
    if (workspaceId) {
      if (!canReadBusinessUnit(scope, workspaceId)) {
        return HttpResponse.json({ code: "not_found" }, { status: 404 });
      }
      return HttpResponse.json(connectorGrantsForWorkspace(workspaceId));
    }
    // Bounded viewer: the union across their units, with the unit lists
    // stripped — see connectorGrantsForWorkspaces.
    if (!scope.isOrgWide) {
      return HttpResponse.json(connectorGrantsForWorkspaces(scope.businessUnitIds));
    }
    return HttpResponse.json(listConnectorGrants());
  }),
  http.put("/api/connectors/grants", async ({ request, cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });
    if (effectivePlatformRole(session) !== "org_admin") {
      return HttpResponse.json(
        { code: "forbidden", message: "Only an Organization Admin can change connector grants." },
        { status: 403 },
      );
    }
    const workspaceId = new URL(request.url).searchParams.get("workspaceId");
    const body = (await request.json()) as { grants?: ConnectorGrant[]; kinds?: string[] };
    return HttpResponse.json(
      workspaceId
        ? setBuConnectorGrants(workspaceId, body.kinds ?? [])
        : setConnectorGrants(body.grants ?? []),
    );
  }),

  http.get("/api/connectors/:kind", async ({ params, cookies }) => {
    await lag();
    const c = CONNECTORS.find((x) => x.kind === params.kind);
    // A by-kind read must respect the same boundary as the list, or a
    // Business Unit Admin could name a sibling unit's connector kind directly
    // and read its account, health and capabilities.
    const scope = scopeFromCookies(cookies);
    const readable =
      c != null &&
      (c.scope === "organization" || canReadBusinessUnit(scope, c.workspaceId ?? null)) &&
      kindIsPermitted(scope, String(params.kind));
    if (!readable) {
      return HttpResponse.json({ code: "not_found", message: "not found" }, { status: 404 });
    }
    return HttpResponse.json(c);
  }),

  // REMOVED: the /api/connectors/:kind/install mock. That BFF route is gone with the
  // OAuth flow — a connector is connected by pasting a credential, which the
  // /credentials handler below mocks. A mock for a route that no longer exists is
  // worse than none: it answers 200 to a call the real app can only 404.

  // Pasted credentials (ADO PAT, Jira token, GH Actions PAT). Mirrors
  // app/api/connectors/[kind]/credentials/route.ts.
  http.post("/api/connectors/:kind/credentials", async ({ params, request, cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });
    const kind = String(params.kind);
    const scope = scopeFromCookies(cookies);
    if (!kindIsPermitted(scope, kind)) {
      return HttpResponse.json({ code: "forbidden", message: NOT_PERMITTED }, { status: 403 });
    }
    const body = (await request.json().catch(() => ({}))) as {
      org_url?: string;
      base_url?: string;
      owner?: string;
      workspaceId?: string | null;
    };
    // Mirrors the route: a named unit must be the caller's; otherwise their
    // only one, never the first of several.
    const requested = body.workspaceId ? String(body.workspaceId) : null;
    if (requested && !canReadBusinessUnit(scope, requested)) {
      return HttpResponse.json(
        { code: "forbidden", message: "Not your business unit." },
        { status: 403 },
      );
    }
    const target =
      requested ?? (scope.businessUnitIds.length === 1 ? scope.businessUnitIds[0]! : null);
    const onboardAt = onboardingScopeFor(effectivePlatformRole(session));
    if (onboardAt.requiresWorkspace && !target) {
      return HttpResponse.json(
        { code: "invalid_input", message: "workspaceId is required — you belong to several." },
        { status: 422 },
      );
    }
    const connector = recordConnectorCredentials(
      CONNECTORS,
      kind,
      {
        scope: onboardAt.scope,
        workspaceId: target,
        tenantId: String(CONNECTORS[0]?.tenantId ?? ""),
      },
      body.org_url ?? body.base_url ?? body.owner ?? null,
    );
    if (!connector) return HttpResponse.json({ code: "not_found" }, { status: 404 });
    return HttpResponse.json({ kind, status: "valid", account: connector.account ?? null });
  }),

  http.post("/api/connectors/:kind/disconnect", async ({ params }) => {
    await lag();
    const c = CONNECTORS.find((x) => x.kind === params.kind);
    if (!c) {
      return HttpResponse.json({ code: "not_found", message: "not found" }, { status: 404 });
    }
    c.installed = false;
    c.health = "disconnected";
    return HttpResponse.json(c);
  }),

  // ───── Audit ─────
  // Scoped to the viewer's projects; organization-level rows (projectId: null)
  // are dropped for anyone not org-wide, since an unattributable governance
  // event is exactly the row that must not cross a boundary. Mirrors
  // app/api/audit/route.ts.
  http.get("/api/audit", async ({ request, cookies }) => {
    await lag();
    const url = new URL(request.url);
    const scope = scopeFromCookies(cookies);
    const rows = filterByProject(scope, AUDIT_EVENTS, (e) => e.projectId);
    return HttpResponse.json(page(rows, url.searchParams));
  }),

  // ───── Access scope (the viewer's Business Unit / project boundary) ─────
  // Mirrors app/api/auth/access-scope/route.ts. Every scope indicator, the
  // dashboard shape and every "is this empty or forbidden" empty state read
  // this one answer, so it must resolve identically in both runtimes.
  http.get("/api/auth/access-scope", async ({ cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) {
      return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });
    }
    return HttpResponse.json(resolveSessionScope(session));
  }),

  // ───── Access / RBAC (admin) ─────
  // Scoped to units the viewer ADMINISTERS: this list is the Business Unit
  // picker on Roles & Access, so it decides whose role assignments can be
  // opened at all. Read access to a parent unit is deliberately not enough.
  http.get("/api/admin/workspaces", async ({ cookies }) => {
    await lag();
    const scope = scopeFromCookies(cookies);
    return HttpResponse.json(
      ACCESS_WORKSPACES.filter((w) => canManageBusinessUnit(scope, w.id)),
    );
  }),
  http.get("/api/admin/roles", async () => {
    await lag();
    return HttpResponse.json(ACCESS_ROLES);
  }),
  http.get("/api/admin/org-members", async () => {
    await lag();
    return HttpResponse.json(ORG_MEMBERS);
  }),
  // The default was `ACCESS_WORKSPACES[0]` — always Payments, regardless of who
  // asked. That both leaked a roster and made the page show the wrong unit for
  // any admin who doesn't run Payments; the default is now the caller's own
  // first manageable unit, and a denied id returns empty rather than falling
  // back to someone else's.
  http.get("/api/admin/members", async ({ request, cookies }) => {
    await lag();
    const url = new URL(request.url);
    const scope = scopeFromCookies(cookies);
    const manageable = ACCESS_WORKSPACES.filter((w) => canManageBusinessUnit(scope, w.id));
    const wid = url.searchParams.get("workspace_id") ?? manageable[0]?.id;
    if (!wid || !canManageBusinessUnit(scope, wid)) return HttpResponse.json([]);
    return HttpResponse.json(ACCESS_MEMBERS[wid] ?? []);
  }),
  http.post("/api/admin/assignments", async ({ request }) => {
    await lag();
    const b = (await request.json()) as { user_id: string; workspace_id: string; role_name: string };

    // One Business Unit per admin. Enforced here and not only in the dialog:
    // a picker that merely disables a button is a suggestion.
    if (b.role_name === "bu_admin") {
      const elsewhere = Object.entries(ACCESS_MEMBERS).find(
        ([wsId, members]) =>
          wsId !== b.workspace_id &&
          members.some((m) => m.userId === b.user_id && m.roles.includes("bu_admin")),
      );
      if (elsewhere) {
        return HttpResponse.json(
          {
            code: "conflict",
            message:
              "They already run another business unit. A person administers one business unit.",
          },
          { status: 409 },
        );
      }
    }

    const list = (ACCESS_MEMBERS[b.workspace_id] ??= []);
    let m = list.find((x) => x.userId === b.user_id);
    if (!m) {
      const isEmail = b.user_id.includes("@");
      m = {
        userId: b.user_id,
        name: null,
        email: isEmail ? b.user_id : null,
        initials: mockInitials(b.user_id),
        roles: [],
      };
      list.push(m);
    }
    if (!m.roles.includes(b.role_name)) m.roles.push(b.role_name);
    return HttpResponse.json({ ok: true });
  }),
  http.delete("/api/admin/assignments", async ({ request }) => {
    await lag();
    const b = (await request.json()) as { user_id: string; workspace_id: string; role_name: string };
    const list = ACCESS_MEMBERS[b.workspace_id] ?? [];
    const m = list.find((x) => x.userId === b.user_id);
    if (m) {
      m.roles = m.roles.filter((r) => r !== b.role_name);
      if (m.roles.length === 0) {
        ACCESS_MEMBERS[b.workspace_id] = list.filter((x) => x.userId !== b.user_id);
      }
    }
    return HttpResponse.json({ ok: true });
  }),

  // Mirrors app/api/onboarding/route.ts exactly (same lib/mock functions) —
  // needed so a person onboarded while MSW is intercepting client fetches
  // (the normal dev-with-mocks path) is visible to the ALSO-MSW-handled
  // /api/admin/members and /api/workspaces/:id/members reads above/elsewhere.
  // A Next.js server route and an MSW browser handler are separate JS
  // runtimes with independent copies of any "shared" module state, so both
  // sides of a read/write pair must run in the SAME one.
  http.post("/api/onboarding", async ({ request }) => {
    await lag();
    const body = (await request.json()) as Parameters<typeof fxOnboardIntoOrganization>[0];
    const { status, body: payload } = fxOnboardIntoOrganization(body);
    return HttpResponse.json(payload as Record<string, unknown>, { status });
  }),

  // ───── Cross-unit loans ─────
  // Mirrors app/api/admin/cross-bu-grants/route.ts.
  http.get("/api/admin/cross-bu-grants", async ({ cookies }) => {
    await lag();
    const scope = scopeFromCookies(cookies);
    return HttpResponse.json(
      fxListCrossBuGrants()
        .filter(
          (g) =>
            canManageBusinessUnit(scope, g.parentWorkspaceId) ||
            canManageBusinessUnit(scope, g.targetWorkspaceId),
        )
        .map((g) => ({
          ...g,
          displayName: fxGetIdentity(g.identityId)?.displayName ?? g.identityId,
          projectName: fxGetProjectById(g.projectId)?.name ?? g.projectId,
          parentWorkspaceName:
            fxGetWorkspace(g.parentWorkspaceId)?.displayName ?? g.parentWorkspaceId,
          targetWorkspaceName:
            fxGetWorkspace(g.targetWorkspaceId)?.displayName ?? g.targetWorkspaceId,
          lentByYou: canManageBusinessUnit(scope, g.parentWorkspaceId),
        })),
    );
  }),
  http.delete("/api/admin/cross-bu-grants", async ({ request, cookies }) => {
    await lag();
    const body = (await request.json().catch(() => ({}))) as {
      identityId?: string;
      projectId?: string;
    };
    if (!body.identityId || !body.projectId) {
      return HttpResponse.json(
        { code: "invalid_input", message: "identityId and projectId are required" },
        { status: 422 },
      );
    }
    const grant = fxListCrossBuGrants().find(
      (g) => g.identityId === body.identityId && g.projectId === body.projectId,
    );
    if (!grant) return HttpResponse.json({ code: "not_found" }, { status: 404 });
    if (!canManageBusinessUnit(scopeFromCookies(cookies), grant.parentWorkspaceId)) {
      return HttpResponse.json(
        {
          code: "forbidden",
          message: "Only the business unit that lent this person can end the loan.",
        },
        { status: 403 },
      );
    }
    fxRevokeCrossBuGrant(body.identityId, body.projectId);
    fxRemoveProjectMembershipsInWorkspace(body.identityId, grant.targetWorkspaceId);
    return HttpResponse.json({ ok: true });
  }),

  // ───── People directory (Users & Roles) ─────
  // Mirrors app/api/admin/users/route.ts. Org-wide on purpose — see that file
  // for why this one list is not scope-filtered.
  http.get("/api/admin/users", async ({ cookies }) => {
    await lag();
    return HttpResponse.json(fxScopeUserDirectory(scopeFromCookies(cookies)));
  }),

  // ───── Custom roles (Roles & Access → Custom roles) ─────
  // Mirrors app/api/admin/custom-roles/** exactly — see
  // [[msw-dual-runtime-mutation-rule]].
  http.get("/api/admin/custom-roles", async () => {
    await lag();
    return HttpResponse.json(fxListCustomRoles());
  }),
  http.post("/api/admin/custom-roles", async ({ request, cookies }) => {
    await lag();
    const body = (await request.json()) as {
      name: string;
      description?: string;
      permissions: string[];
      agentAccess?: Partial<Record<Phase, InvolvementLevel>>;
      scope: CustomRoleScope;
      businessUnitId?: string | null;
    };
    // Ownership from the session's scope, exactly as the route handler does —
    // a role pinned to a unit the caller doesn't run is an escalation.
    const owner = fxResolveRoleOwner(scopeFromCookies(cookies), body.businessUnitId);
    if ("error" in owner) {
      return HttpResponse.json({ code: "forbidden", message: owner.error }, { status: 403 });
    }
    const role = fxCreateCustomRole({ ...body, businessUnitId: owner.businessUnitId });
    return HttpResponse.json(role, { status: 201 });
  }),
  http.patch("/api/admin/custom-roles/:id", async ({ params, request, cookies }) => {
    await lag();
    const existing = fxGetCustomRole(String(params.id));
    if (!existing) return HttpResponse.json({ code: "not_found" }, { status: 404 });
    if (!fxCanWriteCustomRole(scopeFromCookies(cookies), existing)) {
      return HttpResponse.json(
        { code: "forbidden", message: "This role belongs to another business unit." },
        { status: 403 },
      );
    }
    const body = (await request.json()) as Partial<{
      name: string;
      description: string | null;
      permissions: string[];
      agentAccess: Partial<Record<Phase, InvolvementLevel>>;
      scope: CustomRoleScope;
    }>;
    const role = fxUpdateCustomRole(String(params.id), body);
    if (!role) return HttpResponse.json({ code: "not_found" }, { status: 404 });
    return HttpResponse.json(role);
  }),
  http.delete("/api/admin/custom-roles/:id", async ({ params, cookies }) => {
    await lag();
    const existing = fxGetCustomRole(String(params.id));
    if (existing && !fxCanWriteCustomRole(scopeFromCookies(cookies), existing)) {
      return HttpResponse.json(
        { code: "forbidden", message: "This role belongs to another business unit." },
        { status: 403 },
      );
    }
    fxDeleteCustomRole(String(params.id));
    return new HttpResponse(null, { status: 204 });
  }),

  // ───── User detail (Users page click-through) ─────
  // Mirrors app/api/admin/users/[id]/route.ts exactly — see
  // [[msw-dual-runtime-mutation-rule]].
  http.get("/api/admin/users/:id", async ({ params }) => {
    await lag();
    const detail = fxGetUserDetail(String(params.id));
    if (!detail) return HttpResponse.json({ code: "not_found" }, { status: 404 });
    return HttpResponse.json(detail);
  }),
  // Change an org-level appointment. Mirrors the route handler's PATCH.
  http.patch("/api/admin/users/:id", async ({ params, request }) => {
    await lag();
    const body = (await request.json().catch(() => ({}))) as {
      role?: string;
      workspaceId?: string | null;
    };
    const { status, body: payload } = fxChangeOrgAppointment({
      userId: String(params.id),
      ...body,
    });
    return HttpResponse.json(payload as Record<string, unknown>, { status });
  }),

  http.get("/api/admin/audit", async ({ request }) => {
    await lag();
    const url = new URL(request.url);
    const page = Number(url.searchParams.get("page") ?? "1");
    const pageSize = Number(url.searchParams.get("page_size") ?? "50");
    return HttpResponse.json({ items: [], pagination: { page, pageSize, total: 0 } });
  }),

  // Spend split by business unit / project / model — mirrors
  // app/api/cost/spend-series/route.ts, including its scope bounding.
  http.get("/api/cost/spend-series", async ({ request, cookies }) => {
    await lag();
    const scope = scopeFromCookies(cookies);
    const url = new URL(request.url);

    const raw = url.searchParams.get("groupBy") ?? "business_unit";
    const groupBy = (["business_unit", "project", "model", "provider"] as const).includes(
      raw as "business_unit",
    )
      ? (raw as "business_unit" | "project" | "model" | "provider")
      : "business_unit";

    const monthsRaw = Number(url.searchParams.get("months") ?? 6);
    const months = Number.isFinite(monthsRaw)
      ? Math.min(24, Math.max(1, Math.trunc(monthsRaw)))
      : 6;

    const requested = url.searchParams.get("workspaceId");
    const workspaceId = requested && requested !== "all" ? requested : null;
    if (workspaceId && !canReadBusinessUnit(scope, workspaceId)) {
      return HttpResponse.json({ code: "not_found", message: "not found" }, { status: 404 });
    }

    const projectId = url.searchParams.get("projectId");
    if (projectId && !canReadProject(scope, projectId)) {
      return HttpResponse.json({ code: "not_found", message: "not found" }, { status: 404 });
    }

    return HttpResponse.json(
      buildSpendSeries(
        months,
        scope.isOrgWide ? null : scope.businessUnitIds,
        groupBy,
        workspaceId,
        projectId,
      ),
    );
  }),

  // ───── Organization rollup (Org Admin dashboard) ─────
  // Mirrors app/api/org/overview/route.ts — see [[msw-dual-runtime-mutation-rule]].
  http.get("/api/org/overview", async ({ cookies }) => {
    await lag();
    const scope = scopeFromCookies(cookies);
    return HttpResponse.json(
      buildOrgOverview(
        scope.isOrgWide
          ? null
          : { workspaceIds: scope.businessUnitIds, projectIds: scope.projectIds },
      ),
    );
  }),

  // ───── Workspaces (F0/F1/F2) ─────
  // Scoped: this list feeds the sidebar switcher and every Business Unit
  // dropdown, so a sibling unit that never reaches the browser cannot be
  // picked, searched, filtered or linked to.
  http.get("/api/workspaces", async ({ cookies }) => {
    await lag();
    const scope = scopeFromCookies(cookies);
    return HttpResponse.json(
      fxListWorkspaces().filter((w) => canReadBusinessUnit(scope, String(w.id))),
    );
  }),
  // Org Admin only — mirrors app/api/workspaces/route.ts. Without this the
  // server route refuses while MSW happily creates one in the browser, which
  // is the worst version: it looks like it worked.
  http.post("/api/workspaces", async ({ request, cookies }) => {
    await lag();
    if (!scopeFromCookies(cookies).isOrgWide) {
      return HttpResponse.json(
        {
          code: "forbidden",
          message: `Only an Organization Admin can create a ${BUSINESS_UNIT_LABEL.toLowerCase()}`,
        },
        { status: 403 },
      );
    }
    const body = (await request.json()) as {
      displayName: string;
      businessUnit?: string;
      costCenter?: string;
        monthlyBudgetUsd?: number | null;
      budgetStartDate?: string | null;
      budgetEndDate?: string | null;
      isActive?: boolean;
    };
    if (!body?.displayName || body.displayName.trim().length < 2) {
      return HttpResponse.json(
        { code: "invalid_input", message: `${BUSINESS_UNIT_LABEL} name must be at least 2 characters` },
        { status: 422 },
      );
    }
    return HttpResponse.json(fxCreateWorkspace({ ...body, displayName: body.displayName.trim() }), {
      status: 201,
    });
  }),
  http.get("/api/workspaces/:id", async ({ params, cookies }) => {
    await lag();
    const ws = fxGetWorkspace(String(params.id));
    if (!ws || !canReadBusinessUnit(scopeFromCookies(cookies), String(params.id))) {
      return HttpResponse.json({ code: "not_found", message: "not found" }, { status: 404 });
    }
    return HttpResponse.json(ws);
  }),
  // MANAGE, not read — a Project Admin may read the parent unit of their own
  // project for context but must never be able to rename or re-classify it.
  http.patch("/api/workspaces/:id", async ({ params, request, cookies }) => {
    await lag();
    const scope = scopeFromCookies(cookies);
    if (!canManageBusinessUnit(scope, String(params.id))) {
      return HttpResponse.json({ code: "not_found", message: "not found" }, { status: 404 });
    }
    const patch = (await request.json()) as Record<string, unknown>;
    // Org-Admin-only field, and the first-cap-only budget rule — both mirror
    // app/api/workspaces/[id]/route.ts.
    if ("isActive" in patch && !scope.isOrgWide) {
      return HttpResponse.json(
        { code: "forbidden", message: "Only an Organization Admin can change active status" },
        { status: 403 },
      );
    }
    const touchesBudget =
      "monthlyBudgetUsd" in patch || "budgetStartDate" in patch || "budgetEndDate" in patch;
    if (touchesBudget && !scope.isOrgWide) {
      const current = fxGetWorkspace(String(params.id));
      if ((current?.monthlyBudgetUsd ?? null) !== null) {
        return HttpResponse.json(
          {
            code: "forbidden",
            message: "Request a budget increase — an existing cap needs Org Admin approval to change",
          },
          { status: 403 },
        );
      }
    }
    const ws = fxPatchWorkspace(String(params.id), patch);
    if (!ws) return HttpResponse.json({ code: "not_found", message: "not found" }, { status: 404 });
    return HttpResponse.json(ws);
  }),
  // Org Admin only — mirrors app/api/workspaces/[id]/admin/route.ts.
  http.post("/api/workspaces/:id/admin", async ({ params, request, cookies }) => {
    await lag();
    if (!scopeFromCookies(cookies).isOrgWide) {
      return HttpResponse.json(
        {
          code: "forbidden",
          message: `Only an Organization Admin can change a ${BUSINESS_UNIT_LABEL.toLowerCase()}'s admin`,
        },
        { status: 403 },
      );
    }
    const body = (await request.json().catch(() => ({}))) as {
      email?: string;
      displayName?: string;
    };
    if (!body?.email) {
      return HttpResponse.json(
        { code: "invalid_input", message: "email is required" },
        { status: 422 },
      );
    }
    const result = fxSetBusinessUnitAdmin(String(params.id), {
      email: body.email,
      displayName: body.displayName,
    });
    if (!result) {
      return HttpResponse.json({ code: "not_found", message: "not found" }, { status: 404 });
    }
    addAccessMember(
      String(params.id),
      {
        userId: result.admin.ssoSubject,
        name: result.admin.displayName,
        email: result.admin.email,
      },
      "bu_admin",
    );
    return HttpResponse.json({
      workspaceId: String(params.id),
      admin: {
        identityId: result.admin.id,
        userId: result.admin.ssoSubject,
        email: result.admin.email,
        displayName: result.admin.displayName,
        initials: result.admin.initials,
      },
      replacedDisplayName: result.replaced?.displayName ?? null,
    });
  }),
  http.post("/api/workspaces/:id/archive", async ({ params }) => {
    await lag();
    const ws = fxArchiveWorkspace(String(params.id));
    if (!ws) return HttpResponse.json({ code: "not_found", message: "not found" }, { status: 404 });
    return HttpResponse.json(ws);
  }),
  http.get("/api/workspaces/:id/members", async ({ params }) => {
    await lag();
    // Transform fixture WorkspaceMember (frontend-first, Identity-rich) →
    // WorkspaceMemberOut (backend-aligned, simpler) so the schema stays in sync.
    const members = fxListMembers(String(params.id)).map((m) => ({
      userId: m.identity.ssoSubject,
      email: m.identity.email,
      displayName: m.identity.displayName,
      initials: m.identity.initials,
      roleName: m.role,
      joinedAt: new Date().toISOString(),
    }));
    return HttpResponse.json(members);
  }),
  // The three writes below carry the same `canManageBusinessUnit` guard as
  // their route-handler twins: the people directory is org-wide, so "other
  // units are view-only" has to be a property of the write, not of the page.
  http.post("/api/workspaces/:id/members", async ({ params, request, cookies }) => {
    await lag();
    if (!canManageBusinessUnit(scopeFromCookies(cookies), String(params.id))) {
      return HttpResponse.json(
        { code: "forbidden", message: "You don't administer this business unit." },
        { status: 403 },
      );
    }
    const body = (await request.json().catch(() => ({}))) as {
      userId?: string;
      roleName?: string;
      email?: string | null;
      initials?: string;
    };
    if (!body.userId || !body.roleName) {
      return HttpResponse.json({ code: "validation_error", message: "userId and roleName are required" }, { status: 422 });
    }
    const identity = fxFindOrCreateIdentityBySsoSubject(body.userId, body.email, body.initials);
    fxSetMembershipRole(String(params.id), identity.id, body.roleName);
    return HttpResponse.json({
      userId: identity.ssoSubject,
      email: identity.email,
      displayName: identity.displayName,
      initials: identity.initials,
      roleName: body.roleName,
      joinedAt: new Date().toISOString(),
    }, { status: 201 });
  }),
  http.patch("/api/workspaces/:id/members/:userId", async ({ params, request, cookies }) => {
    await lag();
    if (!canManageBusinessUnit(scopeFromCookies(cookies), String(params.id))) {
      return HttpResponse.json(
        { code: "forbidden", message: "You don't administer this business unit." },
        { status: 403 },
      );
    }
    const session = sessionFromCookies(cookies);
    const body = (await request.json().catch(() => ({}))) as {
      roleName?: string;
      roleLabel?: string;
    };
    const { status, body: payload } = fxAssignBusinessUnitRole({
      workspaceId: String(params.id),
      userId: String(params.userId),
      roleName: body.roleName ?? "",
      roleLabel: body.roleLabel,
      actorName: session?.user?.name,
    });
    return HttpResponse.json(payload as Record<string, unknown>, { status });
  }),
  http.delete("/api/workspaces/:id/members/:userId", async ({ params, cookies }) => {
    await lag();
    if (!canManageBusinessUnit(scopeFromCookies(cookies), String(params.id))) {
      return HttpResponse.json(
        { code: "forbidden", message: "You don't administer this business unit." },
        { status: 403 },
      );
    }
    const identity = fxFindOrCreateIdentityBySsoSubject(String(params.userId));
    fxRemoveMembership(String(params.id), identity.id);
    return new HttpResponse(null, { status: 204 });
  }),

  // ───── Project members (project-scoped role binding) ─────
  // Mirrors app/api/projects/[id]/members/** exactly — see
  // [[msw-dual-runtime-mutation-rule]].
  http.get("/api/projects/:id/members", async ({ params }) => {
    await lag();
    return HttpResponse.json(fxListProjectMembers(String(params.id)));
  }),
  http.post("/api/projects/:id/members", async ({ params, request }) => {
    await lag();
    const body = (await request.json()) as { email: string; displayName?: string; roleName: string };
    if (!body?.email || !body?.roleName) {
      return HttpResponse.json(
        { code: "invalid_input", message: "email and roleName are required" },
        { status: 422 },
      );
    }
    // The governance tier is never a project member, and a project is staffed
    // from its own Business Unit — same guard as the route handler, because the
    // add path takes an email no picker can filter.
    const blocked = fxProjectMembershipBlock(String(params.id), body.email);
    if (blocked) {
      return HttpResponse.json({ code: "forbidden", message: blocked }, { status: 422 });
    }
    const member = fxAddProjectMember(String(params.id), body);
    return HttpResponse.json(member, { status: 201 });
  }),

  // Ask another Business Unit to lend a contributor to this project. Mirrors
  // app/api/projects/[id]/access-requests/route.ts.
  http.post("/api/projects/:id/access-requests", async ({ params, request, cookies }) => {
    await lag();
    const session = sessionFromCookies(cookies);
    if (!session) return HttpResponse.json({ code: "unauthenticated" }, { status: 401 });

    const project = fxGetProjectById(String(params.id));
    if (!project) return HttpResponse.json({ code: "not_found" }, { status: 404 });

    const scope = scopeFromCookies(cookies);
    const entitled =
      scope.isOrgWide ||
      canManageProject(scope, String(project.id)) ||
      canManageBusinessUnit(scope, project.workspaceId ? String(project.workspaceId) : null);
    if (!entitled) {
      return HttpResponse.json(
        {
          code: "forbidden",
          message: "Only this project's admin, or its business unit's, can ask for people on it.",
        },
        { status: 403 },
      );
    }

    const body = (await request.json().catch(() => ({}))) as {
      email?: string;
      roleName?: string;
      reason?: string;
    };
    const { status, body: payload } = fxRequestCrossBuAssignment({
      projectId: String(project.id),
      email: body.email,
      roleName: body.roleName,
      reason: body.reason,
      actorName: session.user.name,
      actorIdentityId: scope.identityId,
      actorRole: effectivePlatformRole(session),
    });
    return HttpResponse.json(payload as Record<string, unknown>, { status });
  }),
  http.patch("/api/projects/:id/members/:membershipId", async ({ params, request }) => {
    await lag();
    const body = (await request.json()) as { roleName: string };
    const member = fxUpdateProjectMemberRole(String(params.id), String(params.membershipId), body.roleName);
    if (!member) return HttpResponse.json({ code: "not_found" }, { status: 404 });
    return HttpResponse.json(member);
  }),
  http.delete("/api/projects/:id/members/:membershipId", async ({ params }) => {
    await lag();
    fxRemoveProjectMember(String(params.id), String(params.membershipId));
    return new HttpResponse(null, { status: 204 });
  }),

  // ───── Stream ─────
  http.get("/api/runs/:id/stream", sseHandler),
  http.get("/api/stream", workspaceStreamHandler),

  // ───── Chat ─────
  http.post("/api/chat", chatHandler),

  // ───── HITL gate decisions ─────
  // Real backend translates this into a workflow signal; the mock just flips
  // the artifact's status to mirror what the agent worker would do downstream.
  http.post("/api/runs/:id/signals/:name", async ({ params, request }) => {
    await lag();
    const run = RUNS.find((r) => r.id === params.id);
    if (!run) {
      return HttpResponse.json(
        { code: "not_found", message: "Run not found" },
        { status: 404 },
      );
    }
    const body = (await request.json().catch(() => ({}))) as {
      idempotencyKey?: string;
      payload?: { artifactId?: string; decision?: "approve" | "reject" | "retry" };
    };
    if (!body.idempotencyKey) {
      return HttpResponse.json(
        { code: "missing_idempotency_key", message: "Idempotency-Key required" },
        { status: 400 },
      );
    }
    const decision = body.payload?.decision;
    const artifactId = body.payload?.artifactId;
    if (artifactId && decision) {
      const a = ARTIFACTS.find((x) => x.id === artifactId);
      if (a) {
        a.status =
          decision === "approve"
            ? "approved"
            : decision === "reject"
              ? "rejected"
              : "running";
        a.updatedAt = new Date().toISOString();
      }
    }
    return HttpResponse.json(
      {
        accepted: true,
        signalName: params.name,
        runId: params.id,
        idempotencyKey: body.idempotencyKey,
      },
      { status: 202 },
    );
  }),
];
