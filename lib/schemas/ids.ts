import { z } from "zod";

/**
 * Branded ID types. Compile-time guarantee that a ProjectId can't be
 * passed where a RunId is expected. At runtime they're plain strings.
 *
 *   const id: ProjectId = ProjectId.parse("proj_abc123");
 *   fetchRun(id); // ⟵ TypeScript error
 */
export const TenantId = z.string().min(1).brand<"TenantId">();
export const UserId = z.string().min(1).brand<"UserId">();
export const OrganizationId = z.string().min(1).brand<"OrganizationId">();
export const WorkspaceId = z.string().min(1).brand<"WorkspaceId">();
export const IdentityId = z.string().min(1).brand<"IdentityId">();
export const MembershipId = z.string().min(1).brand<"MembershipId">();
export const ProjectId = z.string().min(1).brand<"ProjectId">();
export const RunId = z.string().min(1).brand<"RunId">();
export const StepId = z.string().min(1).brand<"StepId">();
export const ArtifactId = z.string().min(1).brand<"ArtifactId">();
export const ConnectorId = z.string().min(1).brand<"ConnectorId">();
export const ApprovalId = z.string().min(1).brand<"ApprovalId">();
export const AuditId = z.string().min(1).brand<"AuditId">();
export const NotificationId = z.string().min(1).brand<"NotificationId">();
export const TraceId = z.string().min(1).brand<"TraceId">();
export const SpanId = z.string().min(1).brand<"SpanId">();

export type TenantId = z.infer<typeof TenantId>;
export type UserId = z.infer<typeof UserId>;
export type OrganizationId = z.infer<typeof OrganizationId>;
export type WorkspaceId = z.infer<typeof WorkspaceId>;
export type IdentityId = z.infer<typeof IdentityId>;
export type MembershipId = z.infer<typeof MembershipId>;
export type ProjectId = z.infer<typeof ProjectId>;
export type RunId = z.infer<typeof RunId>;
export type StepId = z.infer<typeof StepId>;
export type ArtifactId = z.infer<typeof ArtifactId>;
export type ConnectorId = z.infer<typeof ConnectorId>;
export type ApprovalId = z.infer<typeof ApprovalId>;
export type AuditId = z.infer<typeof AuditId>;
export type NotificationId = z.infer<typeof NotificationId>;
export type TraceId = z.infer<typeof TraceId>;
export type SpanId = z.infer<typeof SpanId>;
