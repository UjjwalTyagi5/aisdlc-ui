"use client";

import * as React from "react";
import { ShieldCheck, Wrench } from "lucide-react";

import { cn } from "@/lib/utils";
import { RestrictedAccess } from "@/components/auth/restricted-access";
import { AccessHeader, AccessTabs } from "@/components/app/access-tabs";
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
import {
  AGENT_OWNERSHIP,
  ROLE_META,
  ROLE_ORDER,
  type Involvement,
  type PlatformRole,
} from "@/lib/roles";
import { PHASE_LABEL } from "@/lib/agents";
import { PHASE_ORDER } from "@/lib/agents";
import type { Phase } from "@/lib/schemas";

/**
 * Built-in roles — PRD §33.1, §14.7, §14.11.
 *
 * The reference half of the Roles & Access screen: the platform's twelve
 * roles, the two tiers that never cross, and the role × agent ownership
 * matrix that decides where every approval routes.
 *
 * Read-only. Assigning a role is the Assignments view; composing a bundle is
 * the Custom roles view.
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

  if (!hasPermission(session, "member:manage")) {
    return (
      <RestrictedAccess description="Roles & Access is an administrator surface. Ask your Organization Admin if you need access." />
    );
  }

  // The matrix shows the eight shared agents; track-specific agents follow the
  // same ownership rules and are listed under each role's detail.
  const agents: readonly Phase[] = PHASE_ORDER;

  return (
    <div className="w-full space-y-5 p-4 md:px-10 md:py-8">
      <AccessHeader description="The platform's twelve built-in roles, what each one owns, and where every approval routes. Read-only — assign a role from Assignments, compose one from Custom roles." />

      <AccessTabs />

      {/* ── The twelve roles ─────────────────────────────────────────────── */}
      <section className="space-y-3">
        <h2 className="font-display text-[13px] font-semibold tracking-tight">
          The twelve roles
        </h2>

        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          {ROLE_ORDER.map((r) => {
            const meta = ROLE_META[r];
            return (
              <div
                key={r}
                className="border-line-soft bg-panel-elevated rounded-lg border px-3.5 py-3"
              >
                <div className="flex flex-wrap items-center gap-2">
                  {meta.tier === "governance" ? (
                    <ShieldCheck className="text-brand-bright size-3.5 shrink-0" aria-hidden />
                  ) : (
                    <Wrench className="text-muted-foreground size-3.5 shrink-0" aria-hidden />
                  )}
                  <span className="text-[13px] font-semibold">{meta.label}</span>
                  <span
                    className={cn(
                      "rounded-full px-1.5 py-px font-mono text-[10px] tracking-wide uppercase",
                      meta.tier === "governance"
                        ? "bg-primary/10 text-primary"
                        : "bg-surface-2 text-muted-foreground",
                    )}
                  >
                    {meta.tier}
                  </span>
                  <span className="text-muted-foreground ml-auto font-mono text-[10px]">
                    {meta.prdSection}
                  </span>
                </div>

                <p className="text-muted-foreground mt-1.5 text-[12.5px] leading-snug">
                  {meta.oneLiner}
                </p>

                <p className="text-muted-foreground/70 mt-1.5 font-mono text-[10.5px] tracking-wide uppercase">
                  Scope: {meta.scope.replace(/_/g, " ")}
                  {meta.governanceOnly && " · never builds or approves delivery work"}
                </p>
              </div>
            );
          })}
        </div>

        <p className="border-line-soft text-muted-foreground rounded-lg border border-dashed px-4 py-3 text-[12.5px]">
          <strong className="text-foreground font-medium">
            Two tiers that never cross.
          </strong>{" "}
          A person may hold several roles within one tier — Business Unit Admin
          of several units, or Project Admin plus Developer — but never one from
          each. Granting across the line is blocked with a reason.
        </p>
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
