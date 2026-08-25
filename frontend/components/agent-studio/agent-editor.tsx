"use client";

import * as React from "react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { AgentProfileSummaryEntry, ProfileScope } from "@/lib/schemas/agent-profiles";

import { agentLabel } from "./agents";
import { BehaviorTab } from "./behavior-tab";
import { SkillsTab } from "./skills-tab";

/**
 * Everything BehaviorTab needs to know about which cascade tier it's editing
 * (Org → Business Unit → Project → Personal, see AGENT_DEFAULT_OWNER_ROLE).
 * `workspaceId`/`workspaceName`/`projectId`/`projectName` are the *current
 * context* (independent of `scope`) — used for governance-approval payloads
 * and messaging even when the tier being edited is a different one (e.g.
 * proposing an org-level change still records which business unit you were
 * in when you proposed it).
 */
export interface ScopeContext {
  scope: ProfileScope;
  scopeId: string | null;
  scopeLabel: string;
  chain: { workspaceId: string | null; projectId: string | null; userId: string | null };
  isOwner: boolean;
  /** Escalation is one rung up only — from the viewer's own tier to the tier
   *  directly above it (see agent-studio.tsx's homeRank/viewedRank math).
   *  False for a skip-level or downward view: those are read-only, no
   *  propose action at all. */
  canPropose: boolean;
  ownerRoleLabel: string | null;
  workspaceId?: string;
  workspaceName?: string;
  projectId?: string;
  projectName?: string;
}

export interface AgentEditorProps {
  summary: AgentProfileSummaryEntry;
  scopeContext: ScopeContext;
}

export function AgentEditor({ summary, scopeContext }: AgentEditorProps) {
  const label = agentLabel(summary.agent_id);

  return (
    <div className="space-y-4">
      <div className="space-y-1">
        <h2 className="text-lg font-semibold tracking-tight">{label} agent</h2>
        <p className="text-muted-foreground text-sm">
          {scopeContext.scopeLabel} default for the {label} agent.
        </p>
      </div>

      <Tabs defaultValue="behavior">
        <TabsList>
          <TabsTrigger value="behavior">Behavior</TabsTrigger>
          <TabsTrigger value="skills">Skills</TabsTrigger>
        </TabsList>

        <TabsContent value="behavior" className="mt-4">
          {/* key=agentId+scope remounts the tab so field state resets cleanly */}
          <BehaviorTab
            key={`${summary.agent_id}-${scopeContext.scope}-${scopeContext.scopeId}`}
            agentId={summary.agent_id}
            agentLabel={label}
            summary={summary}
            scopeContext={scopeContext}
          />
        </TabsContent>

        <TabsContent value="skills" className="mt-4">
          {/* key=agentId remounts the tab so dialog/toggle state resets per agent */}
          <SkillsTab
            key={summary.agent_id}
            agentId={summary.agent_id}
            agentLabel={label}
            scopeContext={scopeContext}
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}
