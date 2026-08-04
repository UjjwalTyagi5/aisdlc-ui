"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Check, KeyRound, Loader2, Minus, ShieldCheck } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  getModelGrantMatrix,
  getOrgModelGrants,
  setOrgModelGrants,
} from "@/lib/api/models";
import { qk } from "@/lib/api/query-keys";
import { BUSINESS_UNIT_LABEL_PLURAL } from "@/lib/scope";
import type { ModelGrantMatrixRow, OrgModelGrant } from "@/lib/schemas/model";

const keyOf = (provider: string, modelId: string) => `${provider}::${modelId}`;

const PROVIDER_LABEL: Record<string, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  google: "Google",
};

/**
 * Who has which model — one row per model, one column per Business Unit.
 *
 * THE DISTRIBUTION HALF of Model Management, and only that. The provider cards
 * above own supply: what is onboarded, whose key it runs on, which models it
 * offers. This owns reach. Before the split, three surfaces on this page each
 * listed provider → model — the grants card, the provider cards, and the
 * per-unit availability cards — and an admin had to reconcile them by eye to
 * answer one question.
 *
 * A MATRIX RATHER THAN A TREE because the question is comparative: "who has
 * opus?" and "what does Payments have?" are a column read and a row read of
 * the same grid, and a nested list answers the first badly and the second not
 * at all.
 *
 * Cells are the control. Clicking one grants or revokes that model for that
 * unit — the revoke the Org Admin was previously missing, in the place they
 * were already looking. A `global` grant's cells are shown filled but locked:
 * revoking one unit from a grant that reaches everyone would have to silently
 * demote it for the whole organisation, and a control that quietly means
 * something else is worse than one that declines.
 */
export function ModelAccessMatrix({
  workspaces,
}: {
  workspaces: { id: string; displayName: string }[];
}) {
  const queryClient = useQueryClient();

  const matrixQ = useQuery({ queryKey: qk.model.grantMatrix(), queryFn: getModelGrantMatrix });
  const grantsQ = useQuery({ queryKey: qk.model.orgGrants(), queryFn: getOrgModelGrants });

  const grants = React.useMemo(() => grantsQ.data ?? [], [grantsQ.data]);
  // Only models the organization has actually approved. The full catalogue
  // belongs to the grant dialog, not to a table about who has what — listing
  // every ungranted model would bury four real rows under a dozen empty ones.
  const rows = React.useMemo(
    () => (matrixQ.data?.rows ?? []).filter((r) => r.granted),
    [matrixQ.data],
  );

  const saveM = useMutation({
    mutationFn: (entries: OrgModelGrant[]) => setOrgModelGrants(entries),
    onSuccess: () => {
      toast.success("Model access updated");
      queryClient.invalidateQueries({ queryKey: qk.model.orgGrants() });
      queryClient.invalidateQueries({ queryKey: ["model"] });
    },
    onError: (err) =>
      toast.error("Couldn't update model access", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  /** Grant or revoke one model for one unit. */
  function toggleCell(row: ModelGrantMatrixRow, unitId: string) {
    const k = keyOf(row.provider, row.model_id);
    const next = grants.map((g) => {
      if (keyOf(g.provider, g.model_id) !== k) return g;
      const has = g.businessUnitIds.includes(unitId);
      return {
        ...g,
        businessUnitIds: has
          ? g.businessUnitIds.filter((id) => id !== unitId)
          : [...g.businessUnitIds, unitId],
      };
    });
    saveM.mutate(next);
  }

  /** Revoke the model outright — every unit at once. */
  function revokeModel(row: ModelGrantMatrixRow) {
    const k = keyOf(row.provider, row.model_id);
    saveM.mutate(grants.filter((g) => keyOf(g.provider, g.model_id) !== k));
  }

  const loading = matrixQ.isLoading || grantsQ.isLoading;

  return (
    <Card className="border-line-soft bg-panel-elevated">
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-start gap-3">
          <div
            aria-hidden
            className="border-line-soft bg-surface-2 text-muted-foreground grid size-9 shrink-0 place-items-center rounded-lg border"
          >
            <ShieldCheck className="size-4" aria-hidden />
          </div>
          <div className="min-w-0 flex-1">
            <h3 className="font-display text-[14px] font-bold tracking-[-0.01em]">Model access</h3>
            <p className="text-muted-foreground mt-0.5 text-[12px]">
              Which {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()} may use each approved model. Click a
              cell to grant or revoke.
            </p>
          </div>
        </div>
      </CardHeader>

      <CardContent className="pt-0">
        {loading ? (
          <p className="text-muted-foreground text-[12.5px]">Loading…</p>
        ) : rows.length === 0 ? (
          <p className="text-muted-foreground text-[12.5px]">
            No models approved yet. Onboard a provider above, then grant its models here.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[540px] border-collapse text-left">
              <thead>
                <tr className="border-line-soft border-b">
                  <th className="text-muted-foreground py-2 pr-3 font-mono text-[10px] font-semibold tracking-wider uppercase">
                    Model
                  </th>
                  {workspaces.map((w) => (
                    <th
                      key={w.id}
                      className="text-muted-foreground px-2 py-2 text-center font-mono text-[10px] font-semibold tracking-wider uppercase"
                    >
                      {w.displayName}
                    </th>
                  ))}
                  <th className="w-10" />
                </tr>
              </thead>
              <tbody className="divide-line-soft divide-y">
                {rows.map((row) => {
                  const isGlobal = row.visibility === "global";
                  return (
                    <tr key={keyOf(row.provider, row.model_id)}>
                      <td className="py-2.5 pr-3">
                        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                          <span className="font-mono text-[12px]">{row.model_id}</span>
                          {/* The supply fact, restated where the distribution
                              decision is made: granting an unkeyed model gives
                              access to something nobody can run yet. */}
                          {!row.centrallyCredentialed && (
                            <span className="text-warning inline-flex shrink-0 items-center gap-1 font-mono text-[9.5px] tracking-wide uppercase">
                              <KeyRound className="size-2.5" aria-hidden />
                              No org key
                            </span>
                          )}
                          {isGlobal && (
                            <span className="text-muted-foreground border-line-soft shrink-0 rounded-full border px-1.5 py-px font-mono text-[9px] tracking-wide uppercase">
                              Global
                            </span>
                          )}
                        </div>
                        <span className="text-muted-foreground font-mono text-[10px]">
                          {PROVIDER_LABEL[row.provider] ?? row.provider}
                        </span>
                      </td>

                      {workspaces.map((w) => {
                        const unit = row.units.find((u) => u.id === w.id);
                        const has = unit?.hasAccess ?? false;
                        const selfKeyed = unit?.locallyCredentialed ?? false;
                        const cell = (
                          <span
                            className={cn(
                              "mx-auto grid size-6 place-items-center rounded-md border transition-colors",
                              has
                                ? "border-success/40 bg-success/10 text-success"
                                : "border-line-soft text-muted-foreground/40",
                              isGlobal && "opacity-70",
                            )}
                          >
                            {has ? (
                              <Check className="size-3.5" aria-hidden />
                            ) : (
                              <Minus className="size-3" aria-hidden />
                            )}
                          </span>
                        );
                        return (
                          <td key={w.id} className="px-2 py-2.5 text-center">
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <button
                                  type="button"
                                  disabled={isGlobal || saveM.isPending}
                                  onClick={() => toggleCell(row, w.id)}
                                  aria-label={`${has ? "Revoke" : "Grant"} ${row.model_id} for ${w.displayName}`}
                                  className="focus-visible:ring-ring block w-full rounded-md focus-visible:ring-2 focus-visible:outline-none disabled:cursor-default"
                                >
                                  {cell}
                                </button>
                              </TooltipTrigger>
                              <TooltipContent side="top" className="max-w-[240px]">
                                {isGlobal ? (
                                  <p>
                                    Granted globally — reaches every{" "}
                                    {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase().replace(/s$/, "")}.
                                    Switch it to specific to name units.
                                  </p>
                                ) : has ? (
                                  <p>
                                    {w.displayName} has this.
                                    {!row.centrallyCredentialed &&
                                      (selfKeyed
                                        ? " Running on their own key."
                                        : " No key yet — granted but inert.")}
                                  </p>
                                ) : (
                                  <p>Click to grant {w.displayName} this model.</p>
                                )}
                              </TooltipContent>
                            </Tooltip>
                          </td>
                        );
                      })}

                      <td className="py-2.5 text-right">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="text-muted-foreground hover:text-destructive h-7 px-2 text-[11px]"
                          disabled={saveM.isPending}
                          onClick={() => revokeModel(row)}
                        >
                          {saveM.isPending ? (
                            <Loader2 className="size-3 animate-spin" aria-hidden />
                          ) : (
                            "Revoke"
                          )}
                        </Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        <p className="text-muted-foreground mt-3 text-[11.5px]">
          Access says who may use a model, not who pays for it. A model on the platform credential
          needs nothing from the receiving{" "}
          {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}; one marked{" "}
          <span className="text-warning">no org key</span> stays inert until a unit onboards its
          own.
        </p>
      </CardContent>
    </Card>
  );
}
