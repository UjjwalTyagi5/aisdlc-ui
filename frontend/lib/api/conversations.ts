import { z } from "zod";

import type { ProjectId } from "@/lib/schemas";

import { api, API_BASE, ApiRequestError } from "./client";

/** A chat session in the per-user, per-agent history rail (blueprint §11A). */
export const ConversationSession = z.object({
  id: z.string(),
  title: z.string(),
  agent_id: z.string().nullable().optional(),
  created_at: z.string().nullable().optional(),
  updated_at: z.string().nullable().optional(),
});
export type ConversationSession = z.infer<typeof ConversationSession>;

/** One persisted turn in a session transcript. */
export const ConversationMessage = z.object({
  id: z.string(),
  seq: z.number(),
  role: z.string(),
  content: z.string(),
  content_type: z.string(),
  created_at: z.string().nullable().optional(),
  artifact_refs: z.array(z.unknown()).nullable().optional(),
});
export type ConversationMessage = z.infer<typeof ConversationMessage>;

/** The caller's sessions for an agent on a project, newest-first. */
export const listConversations = (projectId: ProjectId, agentId: string) =>
  api(`/conversations`, {
    query: { agent_id: agentId, project_id: projectId },
    schema: z.array(ConversationSession),
  });

/** Start a fresh session; returned id is reused as the chat/WS session id. */
export const createConversation = (input: {
  agentId: string;
  projectId: ProjectId;
  title?: string;
}) =>
  api(`/conversations`, {
    method: "POST",
    body: { agent_id: input.agentId, project_id: input.projectId, title: input.title },
    schema: ConversationSession,
  });

export const getConversationMessages = (id: string) =>
  api(`/conversations/${encodeURIComponent(id)}/messages`, {
    schema: z.array(ConversationMessage),
  });

export const renameConversation = (id: string, title: string) =>
  api(`/conversations/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: { title },
    schema: ConversationSession,
  });

export const deleteConversation = (id: string) =>
  api(`/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });

/** A stored chat attachment (download URL served from /generated). */
export const AttachmentRef = z.object({
  name: z.string(),
  url: z.string(),
  content_type: z.string().nullable().optional(),
  size: z.number().nullable().optional(),
  path: z.string().nullable().optional(),
});
export type AttachmentRef = z.infer<typeof AttachmentRef>;

const AttachmentsEnvelope = z.object({ attachments: z.array(AttachmentRef) });

/** Upload one or more files to a session (multipart — not JSON, so a raw fetch). */
export async function uploadAttachments(
  sessionId: string,
  files: File[],
): Promise<AttachmentRef[]> {
  const form = new FormData();
  for (const f of files) form.append("files", f, f.name);
  const res = await fetch(
    `${API_BASE}/conversations/${encodeURIComponent(sessionId)}/attachments`,
    { method: "POST", credentials: "include", body: form },
  );
  if (!res.ok) {
    throw new ApiRequestError(res.status, await res.json().catch(() => null), res.statusText);
  }
  return AttachmentsEnvelope.parse(await res.json()).attachments;
}

/** List a session's stored attachments (download view on reopen). */
export const listAttachments = (sessionId: string) =>
  api(`/conversations/${encodeURIComponent(sessionId)}/attachments`, {
    schema: AttachmentsEnvelope,
  }).then((r) => r.attachments);
