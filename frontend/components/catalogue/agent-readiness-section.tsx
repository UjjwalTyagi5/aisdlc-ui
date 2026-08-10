"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, CircleSlash, Plug } from "lucide-react";

import { cn } from "@/lib/utils";
import { RequestAccessButton } from "@/components/requests/request-access-button";
import { useAccessScope } from "@/hooks/use-access-scope";
import { listConnectorGrants } from "@/lib/api/connectors";
import { qk } from "@/lib/api/query-keys";
import { CATALOGUE_AGENTS } from "@/lib/catalogue";
import { agentConnectorReadiness } from "@/lib/catalogue-readiness";
import { PHASE_LABEL } from "@/lib/agents";
import { CONNECTOR_KIND_LABEL } from "@/lib/connectors";

/**
 * "Could I actually run this agent, and if not, what's missing?"
 *
 * The catalogue described what every agent DOES without ever saying whether
 * the reader could use it. That gap is where the question "do we even have
 * Jira?" came from: the answer lived on Integrations, three clicks away and
 * framed as an estate inventory rather than as this agent's prerequisites.
 *
 * WHAT THIS DOES NOT CLAIM. It reports connector grants only. An agent also
 * needs a model, but no agent in the roster is bound to a PARTICULAR model —
 * a project picks one from what its unit was granted — so a per-agent model
 * row would be inventing a requirement the platform doesn't have. Model
 * availability is answered per scope on the Models screen, and the note below
 * points there rather than restating it wrongly here.
 */
export function AgentReadinessSection() {
  const { role } = useAccessScope();
  const isOrgAdmin = role === "org_admin";

  // The viewer's own grants, unioned across their units by the endpoint. An
  // Org Admin holds everything by definition, so the query is skipped rather
  // than shown as a wall of "not granted" they would have to ignore.
  const grantsQ = useQuery({
    queryKey: qk.connectors.grants(null),
    queryFn: () => listConnectorGrants(),
    enabled: !isOrgAdmin,
  });

  const granted = React.useMemo<ReadonlySet<string>>(
    () =>
      isOrgAdmin
        ? new Set(Object.keys(CONNECTOR_KIND_LABEL))
        : new Set((grantsQ.data ?? []).map((g) => g.kind)),
    [isOrgAdmin, grantsQ.data],
  );

  // Only agents that need something. An agent with no connector requirement
  // has nothing to be blocked ON, and a row saying so for each of them would
  // be nine-tenths of the table carrying no information.
  const rows = React.useMemo(
    () =>
      CATALOGUE_AGENTS.map((a) => ({
        agent: a,
        needs: agentConnectorReadiness(a, granted),
      })).filter((r) => r.needs.length > 0),
    [granted],
  );

  const blocked = rows.filter((r) => r.needs.some((n) => !n.granted));

  const allKinds = Object.keys(CONNECTOR_KIND_LABEL) as (keyof typeof CONNECTOR_KIND_LABEL)[];
  const heldCount = allKinds.filter((k) => granted.has(k)).length;

  return (
    <div className="space-y-6">
      {/* ── Your standing, connector by connector ─────────────────────────────
          The per-agent table below can only report what the roster DECLARES,
          and today only one agent names its connectors. This grid needs no such
          declaration: it is the platform's connector catalogue against the
          viewer's own grants, which is the direct answer to "do we have this?"
          and is true for all eight kinds regardless of what the roster says. */}
      <div className="space-y-3">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="font-display text-[13.5px] font-bold">Connectors you hold</h3>
          <span className="text-muted-foreground font-mono text-[11px]">
            {heldCount} of {allKinds.length} granted to you
          </span>
        </div>
        <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {allKinds.map((k) => {
            const has = granted.has(k);
            return (
              <li
                key={k}
                className={cn(
                  "border-line-soft flex items-center gap-2 rounded-xl border px-3 py-2.5",
                  has ? "bg-panel-elevated" : "bg-surface-1 border-dashed",
                )}
              >
                {has ? (
                  <Check className="text-success size-3.5 shrink-0" aria-hidden />
                ) : (
                  <CircleSlash className="text-muted-foreground size-3.5 shrink-0" aria-hidden />
                )}
                <span
                  className={cn(
                    "min-w-0 flex-1 truncate text-[12.5px]",
                    has ? "font-medium" : "text-muted-foreground",
                  )}
                >
                  {CONNECTOR_KIND_LABEL[k]}
                </span>
                {!has && (
                  <RequestAccessButton
                    label="Ask"
                    variant="ghost"
                    prefill={{
                      type: "connector_access",
                      title: `${CONNECTOR_KIND_LABEL[k]} access`,
                      description: `Requesting ${CONNECTOR_KIND_LABEL[k]}. It isn't granted to us today.`,
                    }}
                    className="h-6 shrink-0 px-1.5"
                  />
                )}
              </li>
            );
          })}
        </ul>
      </div>

      <p className="text-muted-foreground text-[12.5px]">
        {isOrgAdmin
          ? "You grant connectors, so every agent below is runnable for you. What a given business unit holds is on its own row in Integrations."
          : blocked.length === 0
            ? "Every agent that declares a connector requirement has what it needs in your scope."
            : `${blocked.length} of ${rows.length} agents that declare a connector requirement are missing one in your scope.`}{" "}
        Only agents that name their connectors in the roster appear below.
      </p>

      <ul className="border-line-soft divide-line-soft divide-y overflow-hidden rounded-2xl border">
        {rows.map(({ agent, needs }) => (
          <li key={agent.phase} className="flex flex-wrap items-center gap-x-4 gap-y-2 p-4">
            <span className="min-w-[180px] flex-1">
              <span className="block text-[13px] font-semibold">{PHASE_LABEL[agent.phase]}</span>
              <span className="text-muted-foreground block text-[11.5px]">{agent.purpose}</span>
            </span>

            <span className="flex flex-wrap items-center gap-1.5">
              {needs.map((n) => (
                <span
                  key={n.kind}
                  className={cn(
                    "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10.5px] font-semibold",
                    n.granted
                      ? "text-success bg-success/10 border-success/30"
                      : "text-muted-foreground bg-surface-1 border-line-soft border-dashed",
                  )}
                  title={n.granted ? `${n.label} is granted to you` : `${n.label} is not granted to you`}
                >
                  {n.granted ? (
                    <Check className="size-2.5" aria-hidden />
                  ) : (
                    <CircleSlash className="size-2.5" aria-hidden />
                  )}
                  {n.label}
                </span>
              ))}
            </span>

            {/* One request per agent, naming every connector it still lacks —
                not one button per missing chip. Three separate requests for
                one agent is three decisions for the approver where the real
                question ("should this team run Requirements?") is one. */}
            {needs.some((n) => !n.granted) && (
              <RequestAccessButton
                label="Request missing"
                prefill={{
                  type: "connector_access",
                  title: `Connectors for ${PHASE_LABEL[agent.phase]}`,
                  description: `The ${PHASE_LABEL[agent.phase]} agent needs ${needs
                    .filter((n) => !n.granted)
                    .map((n) => n.label)
                    .join(", ")}, which we aren't granted today.`,
                }}
              />
            )}
          </li>
        ))}
      </ul>

      <p className="text-muted-foreground flex items-start gap-2 text-[11.5px]">
        <Plug className="mt-0.5 size-3.5 shrink-0" aria-hidden />
        Connectors only. No agent is tied to a particular model — a project runs on whichever of
        its business unit&apos;s granted models it picks, so model availability is answered on the
        Models screen.
      </p>
    </div>
  );
}
