"use client";

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { API_BASE } from "@/lib/api/client";
import {
  type AttachmentRef,
  type ConversationSession,
  createConversation,
  getConversationMessages,
  listConversations,
  uploadAttachments,
} from "@/lib/api/conversations";
import { qk } from "@/lib/api/query-keys";
import type { ProjectId } from "@/lib/schemas";
import type { AgentChatMessage } from "@/components/app/agent-chat-drawer";

export interface AgentChatContext {
  page: string;
  artifactTitle?: string;
  /** Optional artifact id — sent to the chat endpoint for tool-call grounding. */
  artifactId?: string;
  /**
   * Chat attachments uploaded for this turn (paths under files/<user>/attachments/
   * <session>/). The backend reads .path to hand them to the agent's file tools.
   */
  attachments?: AttachmentRef[];
  /**
   * The owning project id. Used by (a) the Development agent WS to bind the pulled
   * workspace and persist PRs to a project-linked run, and (b) every stage page so
   * the backend can resolve this project's per-stage MCP selection
   * (Project.mcp_servers[agent_id]) and bind those tools for the agent in chat — the
   * same path the pipeline uses.
   */
  project_id?: string;
  /**
   * Requirements-page chat scope. Nested under `requirements` so the backend's
   * `format_pipeline_context` (sections=("requirements",)) passes it through to
   * the agent instead of dropping it. Refs are ADO board source keys (jiraIssueKey).
   *
   * `board_project` is the finalized ADO project (already pulled from), and
   * `selected_stories`/`all_stories` carry the actual story CONTENT so the agent
   * knows what it is working on without re-fetching or asking which project.
   */
  requirements?: {
    board_project?: string;
    selected_story_refs?: string[];
    all_story_refs?: string[];
    selected_stories?: Array<{
      ref?: string;
      title?: string;
      description?: string;
      acceptance_criteria?: string[];
    }>;
    /**
     * `type` is the BOARD's work item type ("User Story", "Epic", "Task"),
     * not the artifact kind. Board ingestion pulls every item on the board, so
     * this list mixes Epics and chore Tasks in with real stories — without the
     * type they all reach the agent as user stories to design a system for.
     */
    all_stories?: Array<{ ref?: string; title?: string; type?: string }>;
  };
}

export interface AgentChatToolCall {
  id: string;
  name: string;
  args: Record<string, unknown>;
  /** Always present in MVP — replaced by real status when the backend reports it. */
  status: "completed";
}

export interface AgentChatExtendedMessage extends AgentChatMessage {
  toolCalls?: AgentChatToolCall[];
  citations?: string[];
  attachments?: AttachmentRef[];
}

interface UseAgentChatOptions {
  context?: AgentChatContext;
  /** Initial seed (e.g. system intro). Defaults to []. */
  initial?: AgentChatExtendedMessage[];
  /**
   * Which agent's WS to talk to directly (self-contained agent page). e.g.
   * "requirement" → /sdlc/agent/requirement/ws. Omitted → the orchestrator
   * (used when running agents together). The requirements page passes
   * "requirement" so its `pipeline_context.requirements` reaches the agent
   * instead of being rebuilt-and-dropped by the orchestrator.
   */
  agent?: string;
  /**
   * Project that owns these chat sessions. With `agent`, it enables the durable
   * per-user/per-agent session rail (list/restore/new). Omitted → the chat stays
   * ephemeral (a throwaway session id per send), preserving the old behavior.
   */
  projectId?: string;
  /**
   * When this value changes, the chat resets — a fresh session id (new agent
   * thread) and cleared messages. The Requirements page passes the finalized
   * board project, so switching projects (re-pulling a different one) starts a
   * clean conversation scoped to the new project instead of carrying the old
   * project's stories/history.
   */
  sessionKey?: string;
  /**
   * Fired whenever the agent emits an `artifact.updated` event (a document was
   * generated or a board item was written). The page uses it to refresh the
   * main-screen artifact/story lists so chat output appears without a manual
   * "Pull stories".
   */
  onArtifact?: () => void;
}

/** A document the agent generated during the chat (BRD, gap report, risk register…). */
export interface GeneratedDocument {
  id: string;
  name?: string;
  url?: string;
}

/**
 * Owns chat state + the streaming POST. Decoupled from any specific phase
 * page — drop into a page, hand `messages` + `send` + `busy` to
 * `<AgentChatDrawer />`.
 */
export function useAgentChat(opts: UseAgentChatOptions = {}) {
  const [messages, setMessages] = React.useState<AgentChatExtendedMessage[]>(
    opts.initial ?? [],
  );
  const [documents, setDocuments] = React.useState<GeneratedDocument[]>([]);
  const [busy, setBusy] = React.useState(false);
  // Keep the latest onArtifact in a ref so `send` doesn't re-create on each render.
  const onArtifactRef = React.useRef(opts.onArtifact);
  onArtifactRef.current = opts.onArtifact;
  const abortRef = React.useRef<AbortController | null>(null);

  const queryClient = useQueryClient();
  const agentId = opts.agent;
  const projectId = opts.projectId as ProjectId | undefined;
  // Durable session rail is enabled only when we know both the agent and project;
  // otherwise the chat stays ephemeral (throwaway session id) like before.
  const sessionsEnabled = !!(agentId && projectId);

  // The active ConversationSession id; undefined = a blank "new chat" not yet
  // persisted. The id doubles as the backend chat/WS thread_id (§11A.2).
  const [sessionId, setSessionId] = React.useState<string | undefined>(undefined);
  const sessionIdRef = React.useRef<string | undefined>(undefined);
  sessionIdRef.current = sessionId;

  // Attachments staged for the next turn (and shown as chips). Cleared after send.
  const [attachments, setAttachments] = React.useState<AttachmentRef[]>([]);

  const sessionsQ = useQuery({
    queryKey: sessionsEnabled
      ? qk.conversations.list(projectId!, agentId!)
      : ["conversations", "disabled"],
    queryFn: () => listConversations(projectId!, agentId!),
    enabled: sessionsEnabled,
    staleTime: 10_000,
  });
  const sessions: ConversationSession[] = sessionsEnabled ? (sessionsQ.data ?? []) : [];

  const cancel = React.useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setBusy(false);
  }, []);

  // Ensure a persisted session exists, creating it lazily. Returns the id (or
  // undefined when the rail is disabled / creation fails). Used by send + attachFiles.
  const ensureSession = React.useCallback(
    async (title?: string): Promise<string | undefined> => {
      if (sessionIdRef.current) return sessionIdRef.current;
      if (!sessionsEnabled) return undefined;
      try {
        const created = await createConversation({
          agentId: opts.agent!,
          projectId: projectId!,
          title,
        });
        sessionIdRef.current = created.id;
        setSessionId(created.id);
        queryClient.invalidateQueries({
          queryKey: qk.conversations.list(projectId!, opts.agent!),
        });
        return created.id;
      } catch {
        return undefined;
      }
    },
    [sessionsEnabled, opts.agent, projectId, queryClient],
  );

  // Upload files to the current (or a freshly-created) session and stage them.
  const attachFiles = React.useCallback(
    async (files: File[]) => {
      if (!files.length) return;
      const sid = await ensureSession(files[0]?.name);
      if (!sid) return; // attachments require a persisted session
      const refs = await uploadAttachments(sid, files);
      setAttachments((cur) => [...cur, ...refs]);
    },
    [ensureSession],
  );

  const removeAttachment = React.useCallback((name: string) => {
    setAttachments((cur) => cur.filter((a) => a.name !== name));
  }, []);

  // Start a blank chat — clears the transcript; the session is created lazily on
  // the first send so empty chats never clutter the rail.
  const newChat = React.useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setSessionId(undefined);
    setMessages(opts.initial ?? []);
    setDocuments([]);
    setAttachments([]);
    setBusy(false);
  }, [opts.initial]);

  // Load a past session's transcript into the drawer and make it active.
  const selectSession = React.useCallback(async (id: string) => {
    abortRef.current?.abort();
    abortRef.current = null;
    setSessionId(id);
    setBusy(false);
    setDocuments([]);
    setAttachments([]); // staged uploads are per-turn; past ones render on their messages
    try {
      const rows = await getConversationMessages(id);
      setMessages(
        rows
          .filter((m) => m.role === "user" || m.role === "agent" || m.role === "orchestrator")
          .map((m) => ({
            id: m.id,
            role: m.role === "user" ? "user" : "agent",
            content: m.content,
            createdAt: m.created_at ?? new Date().toISOString(),
            attachments: (m.artifact_refs as AttachmentRef[] | null) ?? undefined,
          })),
      );
    } catch {
      setMessages([]);
    }
  }, []);

  const send = React.useCallback(
    async (text: string, agentParams?: Record<string, unknown>) => {
      const userMsg: AgentChatExtendedMessage = {
        id: `u${Date.now()}`,
        role: "user",
        content: text,
        createdAt: new Date().toISOString(),
        attachments: attachments.length ? attachments : undefined,
      };
      const agentMsgId = `a${Date.now()}`;
      const agentMsg: AgentChatExtendedMessage = {
        id: agentMsgId,
        role: "agent",
        content: "",
        createdAt: new Date().toISOString(),
        streaming: true,
        toolCalls: [],
        citations: [],
        diffs: [],
      };

      // Snapshot history *before* we mutate state so the request body is stable.
      const historyForRequest = [...messages, userMsg];
      setMessages((m) => [...m, userMsg, agentMsg]);
      setBusy(true);

      // Ensure a persisted session exists (lazy create on the first turn) so the
      // chat gets a stable id that doubles as the backend thread_id. Falls back to
      // an ephemeral id when the rail is disabled or creation fails — chat still works.
      let sid = (await ensureSession(text.slice(0, 60))) ?? sessionIdRef.current;
      if (!sid) sid = `chat_${Date.now()}`;

      // Attach any staged uploads to this turn's context (backend reads .path).
      const turnContext = attachments.length
        ? { ...opts.context, attachments }
        : opts.context;

      // Clear the composer chips NOW — userMsg + turnContext already captured them, and
      // they render on the sent message. (The end-of-stream reset below is now a no-op.)
      setAttachments([]);

      const ac = new AbortController();
      abortRef.current = ac;

      try {
        // The BFF /api/chat route expects { message, sessionId, context } and
        // bridges to the agent WS. (historyForRequest stays local for the
        // optimistic UI; the backend keeps its own thread by sessionId.)
        void historyForRequest;
        const res = await fetch(`${API_BASE}/chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          signal: ac.signal,
          body: JSON.stringify({
            message: text,
            sessionId: sid,
            context: turnContext,
            agent: opts.agent,
            agentParams,
          }),
        });

        if (!res.ok || !res.body) {
          throw new Error(`Chat request failed (${res.status})`);
        }

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });

          // Parse complete `data: ...\n\n` frames; keep partial frames for next loop.
          let idx: number;
          while ((idx = buf.indexOf("\n\n")) !== -1) {
            const frame = buf.slice(0, idx);
            buf = buf.slice(idx + 2);
            const payload = frame
              .split("\n")
              .filter((l) => l.startsWith("data: "))
              .map((l) => l.slice(6))
              .join("\n");
            if (!payload) continue;
            const evt = JSON.parse(payload) as ChatEvent;
            applyEvent(setMessages, agentMsgId, evt);
            if (evt.type === "artifact.updated") {
              // A document was generated or a board item was written. Refresh the
              // main screen, and collect downloadable documents (those with a url).
              onArtifactRef.current?.();
              const id = typeof evt.artifactId === "string" ? evt.artifactId : undefined;
              const url = typeof (evt as { url?: unknown }).url === "string"
                ? (evt as { url: string }).url
                : undefined;
              if (id && url) {
                const name = typeof (evt as { name?: unknown }).name === "string"
                  ? (evt as { name: string }).name
                  : undefined;
                setDocuments((cur) =>
                  cur.some((d) => d.id === id) ? cur : [...cur, { id, name, url }],
                );
              }
            }
          }
        }
      } catch (err) {
        if ((err as { name?: string }).name === "AbortError") {
          // Stop without erroring — user cancelled.
          setMessages((m) =>
            m.map((msg) =>
              msg.id === agentMsgId ? { ...msg, streaming: false } : msg,
            ),
          );
        } else {
          setMessages((m) =>
            m.map((msg) =>
              msg.id === agentMsgId
                ? {
                    ...msg,
                    streaming: false,
                    content:
                      msg.content +
                      (msg.content ? "\n\n" : "") +
                      "_The agent stream errored — please try again._",
                  }
                : msg,
            ),
          );
        }
      } finally {
        setBusy(false);
        abortRef.current = null;
        // (attachments were already cleared at send-time; not here, so a file staged
        //  during streaming for the NEXT turn survives.)
        // Refresh the rail so the new/just-used session surfaces newest-first.
        if (sessionsEnabled) {
          queryClient.invalidateQueries({
            queryKey: qk.conversations.list(projectId!, opts.agent!),
          });
        }
      }
    },
    [messages, opts.context, opts.agent, sessionsEnabled, projectId, queryClient, ensureSession, attachments],
  );

  const reset = React.useCallback(() => setMessages(opts.initial ?? []), [opts.initial]);

  // Reset the session (new agent thread + cleared messages) when sessionKey
  // changes — e.g. the user re-pulls a different board project. Skips the first
  // run so the initial mount keeps its seeded messages.
  const sessionKey = opts.sessionKey;
  const prevKeyRef = React.useRef<string | undefined>(sessionKey);
  React.useEffect(() => {
    if (prevKeyRef.current === sessionKey) return;
    const prev = prevKeyRef.current;
    prevKeyRef.current = sessionKey;
    // Don't reset on the first resolution (undefined → initial project); only on
    // a genuine switch between two known projects.
    if (prev === undefined || sessionKey === undefined) return;
    abortRef.current?.abort();
    abortRef.current = null;
    setSessionId(undefined);
    setBusy(false);
    setMessages(opts.initial ?? []);
    setDocuments([]);
    setAttachments([]);
  }, [sessionKey, opts.initial]);

  return {
    messages,
    send,
    cancel,
    reset,
    busy,
    documents,
    // Session rail
    sessions,
    sessionId,
    selectSession,
    newChat,
    // Attachments
    attachments,
    attachFiles,
    removeAttachment,
    sessionsLoading: sessionsEnabled && sessionsQ.isLoading,
  };
}

// ---- internal: chat event reducer ----
//
// The BFF (/api/chat → mapWsToSseEvent) emits canonical StreamEvents, not a
// bespoke chat vocabulary. We handle the subset relevant to the drawer:
//   step.output.delta  → append `delta` to the streaming agent bubble
//   run.completed      → stop streaming (clears the typing indicator)
//   artifact.updated   → surface as a citation chip
//   code.diff          → upsert a per-turn diff card, keyed by path
type ChatEvent =
  | { type: "step.output.delta"; delta?: string }
  | { type: "run.completed"; status?: string }
  | { type: "artifact.updated"; artifactId?: string; name?: string; url?: string }
  | {
      type: "code.diff";
      path?: string;
      original?: string;
      modified?: string;
      changeKind?: string;
    }
  | { type: string; [k: string]: unknown };

function applyEvent(
  setMessages: React.Dispatch<React.SetStateAction<AgentChatExtendedMessage[]>>,
  agentId: string,
  event: ChatEvent,
) {
  setMessages((messages) =>
    messages.map((m) => {
      if (m.id !== agentId) return m;
      switch (event.type) {
        case "step.output.delta":
          return {
            ...m,
            content: m.content + (typeof event.delta === "string" ? event.delta : ""),
          };
        case "artifact.updated":
          return typeof event.artifactId === "string"
            ? { ...m, citations: [...(m.citations ?? []), event.artifactId] }
            : m;
        case "run.completed":
          return { ...m, streaming: false };
        case "code.diff": {
          const path = typeof event.path === "string" ? event.path : "";
          if (!path) return m;
          const modified = typeof event.modified === "string" ? event.modified : "";
          const original = typeof event.original === "string" ? event.original : "";
          const changeKind = event.changeKind === "created" ? "created" : "edited";
          const existing = m.diffs ?? [];
          const idx = existing.findIndex((d) => d.path === path);
          if (idx === -1) {
            return { ...m, diffs: [...existing, { path, original, modified, changeKind }] };
          }
          // Same path seen again this turn (e.g. edit_file called twice): the
          // later `modified` wins, but `original` stays the FIRST event's value
          // so the card shows the net change across the whole turn.
          const next = existing.map((d, i) => (i === idx ? { ...d, modified } : d));
          return { ...m, diffs: next };
        }
        default:
          return m;
      }
    }),
  );
}
