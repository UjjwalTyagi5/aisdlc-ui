/**
 * Workspaces API client. Calls the same-origin BFF (`/api/workspaces*`).
 */
import { api } from "./client";
import {
  BusinessUnitAdminChange,
  Workspace,
  WorkspaceList,
  WorkspaceMemberList,
  WorkspaceMemberOut,
  type WorkspaceCreateBody,
} from "@/lib/schemas/workspace";
import { GovernanceApproval } from "@/lib/schemas/governance-approval";

export const listWorkspaces = () => api("/workspaces", { schema: WorkspaceList });

export const getWorkspace = (id: string) =>
  api(`/workspaces/${encodeURIComponent(id)}`, { schema: Workspace });

export const createWorkspace = (body: WorkspaceCreateBody) =>
  api("/workspaces", { method: "POST", body, schema: Workspace });

export const updateWorkspace = (
  id: string,
  body: Partial<{
    displayName: string;
    businessUnit: string | null;
    costCenter: string | null;
    monthlyBudgetUsd: number | null;
    /** Validity period of the cap — see lib/schemas/budget-window.ts. */
    budgetStartDate: string | null;
    budgetEndDate: string | null;
    /** Org Admin only — the API rejects this field from a unit's own Admin. */
    isActive: boolean;
  }>,
) => api(`/workspaces/${encodeURIComponent(id)}`, { method: "PATCH", body, schema: Workspace });

export const archiveWorkspace = (id: string) =>
  api(`/workspaces/${encodeURIComponent(id)}/archive`, { method: "POST", schema: Workspace });

/** Re-appoint who runs a Business Unit. Org Admin only (PRD §15.2) — the
 *  previous holder is demoted, not removed. An unrecognized email is
 *  onboarded, same as every other appointment path. */
export const changeBusinessUnitAdmin = (
  id: string,
  body: { email: string; displayName?: string },
) =>
  api(`/workspaces/${encodeURIComponent(id)}/admin`, {
    method: "POST",
    body,
    schema: BusinessUnitAdminChange,
  });

/** A BU Admin can't set their own budget directly — this sends the Org Admin
 *  above them a governance approval instead (lib/governance.ts). */
export const requestBudgetIncrease = (
  id: string,
  body: { requestedAmountUsd: number; reason?: string },
) =>
  api(`/workspaces/${encodeURIComponent(id)}/budget-increase-request`, {
    method: "POST",
    body,
    schema: GovernanceApproval,
  });

// ─── Member management ────────────────────────────────────────────────────────

export const listWorkspaceMembers = (id: string) =>
  api(`/workspaces/${encodeURIComponent(id)}/members`, { schema: WorkspaceMemberList });

export const addWorkspaceMember = (
  id: string,
  body: { userId: string; roleName: string; email?: string | null; initials?: string },
) =>
  api(`/workspaces/${encodeURIComponent(id)}/members`, {
    method: "POST",
    body,
    schema: WorkspaceMemberOut,
  });

export const updateWorkspaceMemberRole = (
  id: string,
  userId: string,
  body: { roleName: string },
) =>
  api(`/workspaces/${encodeURIComponent(id)}/members/${encodeURIComponent(userId)}`, {
    method: "PATCH",
    body,
    schema: WorkspaceMemberOut,
  });

export const removeWorkspaceMember = (id: string, userId: string) =>
  api(`/workspaces/${encodeURIComponent(id)}/members/${encodeURIComponent(userId)}`, {
    method: "DELETE",
  });
