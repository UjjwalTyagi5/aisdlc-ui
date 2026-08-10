/**
 * Dummy chat-session store for the per-agent session rail — an in-memory
 * array (mirrors the mutable-WORKSPACES pattern in workspace-fixtures.ts), so
 * created sessions persist for the life of the dev process even though there
 * is no database. Plain data + functions, server-safe (imported by the
 * app/api/conversations route handlers). This is the DUMMY-DATA source; the
 * backend conversations service replaces the route-handler bodies, not these
 * shapes.
 */
import type { ConversationMessage, ConversationSession } from "@/lib/api/conversations";

let nextId = 1;
const SESSIONS: (ConversationSession & { project_id: string })[] = [];

export function listConversations(agentId: string, projectId: string): ConversationSession[] {
  return SESSIONS.filter((s) => s.agent_id === agentId && s.project_id === projectId).sort(
    (a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""),
  );
}

export function createConversation(input: {
  agentId: string;
  projectId: string;
  title?: string;
}): ConversationSession {
  const now = new Date().toISOString();
  const session: ConversationSession & { project_id: string } = {
    id: `conv_${nextId++}`,
    title: input.title ?? "New session",
    agent_id: input.agentId,
    project_id: input.projectId,
    created_at: now,
    updated_at: now,
  };
  SESSIONS.push(session);
  return session;
}

export function renameConversation(id: string, title: string): ConversationSession | undefined {
  const s = SESSIONS.find((x) => x.id === id);
  if (!s) return undefined;
  s.title = title;
  s.updated_at = new Date().toISOString();
  return s;
}

export function deleteConversation(id: string): boolean {
  const i = SESSIONS.findIndex((x) => x.id === id);
  if (i === -1) return false;
  SESSIONS.splice(i, 1);
  return true;
}

/** No mock message-persistence yet — every session opens with an empty transcript. */
export function listMessages(_id: string): ConversationMessage[] {
  return [];
}
