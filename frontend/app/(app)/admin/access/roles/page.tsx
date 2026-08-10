"use client";

import * as React from "react";

import { cn } from "@/lib/utils";
import { RestrictedAccess } from "@/components/auth/restricted-access";
import { AccessHeader, AccessTabs } from "@/components/app/access-tabs";
import { RolePermissionsPanel } from "@/components/app/role-permissions-panel";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useSession } from "@/hooks/use-session";
import { hasPermission } from "@/lib/auth/permissions";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import {
  AGENT_OWNERSHIP,
  ROLE_META,
  ROLE_ORDER,
  type Involvement,
  type PlatformRole,
} from "@/lib/roles";
import { PHASE_LABEL } from "@/lib/agents";
import { PHASE_ALL } from "@/lib/agents";
import type { Phase } from "@/lib/schemas";

/**
 * Built-in roles — PRD §33.1, §14.7, §14.11.
 *
 * The reference half of the Roles & Access screen: the platform's twelve
 * roles, the two tiers that never cross, and the role × agent ownership
 * matrix that decides where every approval routes.
 *
 * Assigning a role to a person is Users — you arrive there thinking about the
 * person, not the scope. This screen is what a role MEANS: the permissions it
 * holds (editable by an Organization Admin), the agents it owns, and the two
 * tiers that never cross.
 */

const INVOLVEMENT_GLYPH: Record<Involvement, string> = {
  owner: "◆",
  primary: "●",
  build: "◐",
  requests: "○",
  use: "·",
  none: "",
};

const INVOLVEMENT_LABEL: Record<Involvement, string> = {
  owner: "Owner — fallback approver",
  primary: "Primary user and approver",
  build: "Builds; the owner approves",
  requests: "Requests it; neither runs nor approves",
  use: "May run its Safe capabilities",
  none: "No involvement",
};

const INVOLVEMENT_TONE: Record<Involvement, string> = {
  owner: "text-primary",
  primary: "text-primary",
  build: "text-info",
  requests: "text-muted-foreground",
  use: "text-muted-foreground/60",
  none: "text-transparent",
};

export default function BuiltInRolesPage() {
  const session = useSession({ required: true });
  // Editing what a role holds is org-wide policy, not unit administration.
  const isOrgAdmin = effectivePlatformRole(session) === "org_admin";

  if (!hasPermission(session, "member:manage")) {
    return (
      <RestrictedAccess description="Roles & Access is an administrator surface. Ask your Organization Admin if you need access." />
    );
  }

  // The matrix shows the eight shared agents; track-specific agents follow the
  // same ownership rules and are listed under each role's detail.
  /**
   * ALL THIRTEEN agents, not the eight `PHASE_ORDER` lists.
   *
   * That constant is the greenfield pipeline — the stages a Track 1 project
   * runs. Ownership is a property of the ROLE, not of a track, so a matrix
   * built from it silently omitted Discovery, Strategy, Migration mapping,
   * Validation and Data engineering, and the Data Engineer's own agent was
   * missing from the row describing them.
   */
  const agents: readonly Phase[] = PHASE_ALL;

  return (
    <div className="w-full space-y-5 p-4 md:px-10 md:py-8">
      <AccessHeader description="What each built-in role can do, what it owns, and where every approval routes. An Organization Admin can change what a role holds; roles are given to people on Users." />

      <AccessTabs />

      {/* The twelve role cards used to sit here — name, PRD section, one-line
          description, scope. Reference material: true once, then a screenful
          of prose above the thing you came to change. The Permissions section
          below names every role anyway, and the ownership matrix says which
          agents each one owns. */}

      {/* ── What each role can DO ────────────────────────────────────────
          Above the ownership matrix: "what am I allowed to do" is the question
          people arrive with, and ownership is the follow-up about agents. */}
      <section className="space-y-3">
        <h2 className="font-display text-[13px] font-semibold tracking-tight">
          Permissions
        </h2>
        <p className="text-muted-foreground text-[12.5px]">
          The permission behind every button. Open a role to see exactly what it
          holds — an Organization Admin can change it, and a changed role says so
          and can be put back.
        </p>
        <RolePermissionsPanel canEdit={isOrgAdmin} />
      </section>

      {/* ── Ownership matrix ─────────────────────────────────────────────── */}
      <section className="space-y-3">
        <h2 className="font-display text-[13px] font-semibold tracking-tight">
          Role × agent ownership
        </h2>
        <p className="text-muted-foreground text-[12.5px]">
          Each agent has one owning role that chats with it as its primary user
          and approves its Consequential actions and Sign-offs. Approvals route
          sideways to that owner — never up to a governance tier.
        </p>

        <div className="border-line-soft bg-panel-elevated overflow-hidden rounded-xl border">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow className="border-line-soft hover:bg-transparent">
                  <TableHead className="text-muted-foreground sticky left-0 z-10 bg-[var(--panel-elevated)] font-mono text-[10.5px] tracking-widest uppercase">
                    Role
                  </TableHead>
                  {agents.map((p) => (
                    <TableHead
                      key={p}
                      className="text-muted-foreground text-center font-mono text-[10px] tracking-wide uppercase"
                    >
                      {PHASE_LABEL[p]}
                    </TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {ROLE_ORDER.filter((r) => r !== "custom").map((r) => {
                  const meta = ROLE_META[r];
                  return (
                    <TableRow key={r} className="border-line-soft">
                      <TableCell className="sticky left-0 z-10 bg-[var(--panel-elevated)] py-2 text-[12.5px] font-medium whitespace-nowrap">
                        {meta.shortLabel}
                      </TableCell>
                      {agents.map((p) => {
                        const inv = AGENT_OWNERSHIP[r as PlatformRole][p];
                        return (
                          <TableCell key={p} className="py-2 text-center">
                            {inv === "none" ? (
                              <span className="text-muted-foreground/25" aria-label="No involvement">
                                —
                              </span>
                            ) : (
                              <Tooltip>
                                <TooltipTrigger asChild>
                                  <span
                                    className={cn(
                                      "cursor-help text-[13px]",
                                      INVOLVEMENT_TONE[inv],
                                    )}
                                    aria-label={`${meta.shortLabel}, ${PHASE_LABEL[p]}: ${INVOLVEMENT_LABEL[inv]}`}
                                  >
                                    {INVOLVEMENT_GLYPH[inv]}
                                  </span>
                                </TooltipTrigger>
                                <TooltipContent side="top">
                                  {INVOLVEMENT_LABEL[inv]}
                                </TooltipContent>
                              </Tooltip>
                            )}
                          </TableCell>
                        );
                      })}
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        </div>

        {/* Legend */}
        <div className="border-line-soft flex flex-wrap items-center gap-x-5 gap-y-2 rounded-lg border px-4 py-3">
          {(["owner", "primary", "build", "requests", "use"] as Involvement[]).map((inv) => (
            <span key={inv} className="flex items-center gap-1.5">
              <span className={cn("text-[13px]", INVOLVEMENT_TONE[inv])}>
                {INVOLVEMENT_GLYPH[inv]}
              </span>
              <span className="text-muted-foreground text-[11.5px]">
                {INVOLVEMENT_LABEL[inv]}
              </span>
            </span>
          ))}
        </div>

        <p className="text-muted-foreground text-[12.5px]">
          Organization Admin and Business Unit Admin are absent from this matrix
          by design: they have no agent access at all. A Developer never appears
          as an approver — it builds in Development and the Architect approves,
          which is what makes self-approval structurally impossible.
        </p>
      </section>
    </div>
  );
}
