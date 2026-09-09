"use client";

import * as React from "react";
import Link from "next/link";
import { formatDistanceToNow } from "date-fns";
import { MessageSquare } from "lucide-react";

import { cn } from "@/lib/utils";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { LoadingState } from "@/components/ui/loading-state";
import { Separator } from "@/components/ui/separator";
import { AGENT_LABEL, chatAgentPhase, chatSessionHref } from "@/lib/agents";
import type { ConversationSession } from "@/lib/api/conversations";
import type { ProjectId } from "@/lib/schemas";

export interface RecentChatsListProps {
  projectId: ProjectId;
  /** null while loading. */
  sessions: readonly ConversationSession[] | null;
  className?: string;
}

/** The agent's display name, or its raw id if it is one this build does not know. */
function agentLabel(agentId: string | null | undefined): string {
  if (!agentId) return "Agent";
  const phase = chatAgentPhase(agentId);
  if (phase) return AGENT_LABEL[phase] ?? agentId;
  return agentId === "orchestrator" ? AGENT_LABEL.orchestrator : agentId;
}

/**
 * The person's most recent conversations on a project, whichever agent they were with.
 *
 * ONE CLICK BACK IN. Every row is a link to the agent's page carrying `?session=`, so
 * the drawer opens on that conversation rather than a blank one — a chat you cannot
 * get back to is a chat you have to start again.
 *
 * SCROLLS RATHER THAN GROWS. Ten conversations is a tall list, and this panel sits
 * above the artifacts one; letting it run to full height pushed everything else off
 * the screen. The list keeps its own scrollbar so the page's shape does not depend on
 * how much chatting has happened.
 */
export function RecentChatsList({ projectId, sessions, className }: RecentChatsListProps) {
  if (sessions === null) return <LoadingState variant="list" rows={4} className={className} />;

  if (sessions.length === 0) {
    return (
      <EmptyState
        icon={MessageSquare}
        title="No chats yet"
        description="Open any agent and ask it something — the conversation will show up here."
        variant="card"
        className={className}
      />
    );
  }

  return (
    <Card className={className}>
      <CardContent className="p-2">
        <ul className="max-h-[22rem] divide-y overflow-y-auto">
          {sessions.map((s) => (
            <li key={s.id}>
              <Link
                href={chatSessionHref(projectId, s.agent_id ?? "", s.id)}
                className={cn(
                  "hover:bg-accent flex items-center gap-3 rounded-md px-2 py-2.5 transition-colors",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                )}
              >
                <MessageSquare
                  className="text-muted-foreground size-4 shrink-0"
                  aria-hidden
                />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{s.title || "New chat"}</p>
                  <div className="text-muted-foreground flex items-center gap-2 text-xs">
                    <span>{agentLabel(s.agent_id)}</span>
                    {(s.updated_at || s.created_at) && (
                      <>
                        <Separator orientation="vertical" className="h-3" />
                        <span>
                          {formatDistanceToNow(
                            new Date((s.updated_at || s.created_at)!),
                            { addSuffix: true },
                          )}
                        </span>
                      </>
                    )}
                  </div>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
