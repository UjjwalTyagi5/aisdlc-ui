"use client";

import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowLeft, KeyRound, Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { LoadingState } from "@/components/ui/loading-state";
import { ErrorState } from "@/components/ui/error-state";
import { RestrictedAccess } from "@/components/auth/restricted-access";
import { UnitAccessPicker } from "@/components/app/unit-access-picker";
import { useAccessScope } from "@/hooks/use-access-scope";
import { useScopedBusinessUnits } from "@/hooks/use-scoped-business-units";
import { getSpendSeries } from "@/lib/api/cost";
import {
  getModelGrantMatrix,
  getOrgModelGrants,
  listModelProviders,
  setOrgModelGrants,
} from "@/lib/api/models";
import { qk } from "@/lib/api/query-keys";
import { BUSINESS_UNIT_LABEL_PLURAL } from "@/lib/scope";
import type { ModelGrantMatrixRow, ModelProvider, OrgModelGrant } from "@/lib/schemas/model";

const PROVIDER_LABEL: Record<string, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  google: "Google",
};

const usd = (n: number) =>
  n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });

const keyOf = (provider: string, modelId: string, credentialId?: string | null) =>
  `${provider}::${modelId}::${credentialId ?? ""}`;

/**
 * One provider, in full.
 *
 * The list screen answers "is this provider healthy and what does it cost".
 * This answers everything model-level, which is what made that list unreadable
 * when it all lived there:
 *
 *   what each MODEL costs
 *   which CREDENTIAL serves it — a provider may hold several, each covering
 *     different models, which is how one key can run production models and
 *     another the cheap ones
 *   which BUSINESS UNITS have it, revocable here
 *
 * Org Admin only. The grant matrix names every unit's standing against every
 * model, which is the organisation's whole posture in one screen.
 */
export default function ProviderDetailPage() {
  const params = useParams<{ provider: string }>();
  const providerKind = decodeURIComponent(params.provider);
  const queryClient = useQueryClient();
  const { role } = useAccessScope();
  const { units } = useScopedBusinessUnits();

  const providersQ = useQuery({
    queryKey: qk.model.providers(null),
    queryFn: () => listModelProviders(null),
  });
  const matrixQ = useQuery({ queryKey: qk.model.grantMatrix(), queryFn: getModelGrantMatrix });
  const grantsQ = useQuery({ queryKey: qk.model.orgGrants(), queryFn: getOrgModelGrants });
  const spendQ = useQuery({
    queryKey: qk.cost.spendSeries("model", "all", 6),
    queryFn: () => getSpendSeries({ groupBy: "model", months: 6 }),
    staleTime: 60_000,
  });

  const grants = React.useMemo(() => grantsQ.data ?? [], [grantsQ.data]);

  /** Every credential onboarded for this provider — usually one, sometimes not. */
  const credentials: ModelProvider[] = React.useMemo(
    () => (providersQ.data ?? []).filter((p) => p.provider === providerKind),
    [providersQ.data, providerKind],
  );

  const rows: ModelGrantMatrixRow[] = React.useMemo(
    () => (matrixQ.data?.rows ?? []).filter((r) => r.provider === providerKind),
    [matrixQ.data, providerKind],
  );

  /** Current-month spend per model, from the same series Cost & Budget reads. */
  const spendByModel = React.useMemo(() => {
    const m = new Map<string, number>();
    for (const s of spendQ.data?.series ?? []) {
      m.set(s.id, s.points[s.points.length - 1] ?? 0);
    }
    return m;
  }, [spendQ.data]);

  /**
   * Summed over DISTINCT models, not rows.
   *
   * Spend is recorded per model; a model served by two subscriptions has two
   * rows but one bill. Summing rows counted it twice and inflated the provider
   * total — the exact class of double-count this page exists to avoid.
   */
  const providerSpend = React.useMemo(() => {
    const seen = new Set<string>();
    return rows.reduce((a, r) => {
      if (seen.has(r.model_id)) return a;
      seen.add(r.model_id);
      return a + (spendByModel.get(r.model_id) ?? 0);
    }, 0);
  }, [rows, spendByModel]);

  /** Models served by more than one subscription — their spend is not
   *  attributable to a single key, and the row must say so. */
  const sharedModels = React.useMemo(() => {
    const count = new Map<string, number>();
    for (const r of rows) count.set(r.model_id, (count.get(r.model_id) ?? 0) + 1);
    return new Set([...count.entries()].filter(([, n]) => n > 1).map(([id]) => id));
  }, [rows]);

  const saveM = useMutation({
    mutationFn: (entries: OrgModelGrant[]) => setOrgModelGrants(entries),
    onSuccess: () => {
      toast.success("Model access updated");
      queryClient.invalidateQueries({ queryKey: ["model"] });
    },
    onError: (err) =>
      toast.error("Couldn't update model access", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  function toggleCell(row: ModelGrantMatrixRow, unitId: string) {
    const k = keyOf(row.provider, row.model_id, row.credentialId);
    saveM.mutate(
      grants.map((g) => {
        if (keyOf(g.provider, g.model_id, g.credentialId) !== k) return g;
        const has = g.businessUnitIds.includes(unitId);
        return {
          ...g,
          businessUnitIds: has
            ? g.businessUnitIds.filter((id) => id !== unitId)
            : [...g.businessUnitIds, unitId],
        };
      }),
    );
  }

  /** Revokes THIS subscription's grant, not the model everywhere — the other
   *  key's grant is a separate decision and survives. */
  function revokeModel(row: ModelGrantMatrixRow) {
    const k = keyOf(row.provider, row.model_id, row.credentialId);
    saveM.mutate(grants.filter((g) => keyOf(g.provider, g.model_id, g.credentialId) !== k));
  }

  if (role !== null && role !== "org_admin") {
    return (
      <RestrictedAccess description="Provider detail is the Organization Admin's — it names every business unit's access to every model." />
    );
  }

  const loading = providersQ.isLoading || matrixQ.isLoading || grantsQ.isLoading;
  const label = PROVIDER_LABEL[providerKind] ?? providerKind;

  return (
    <div className="w-full space-y-6 p-4 md:px-10 md:py-8">
      <div>
        <Link
          href="/admin/models"
          className="text-muted-foreground hover:text-foreground mb-3 inline-flex items-center gap-1 font-mono text-[11px] transition-colors"
        >
          <ArrowLeft className="size-3" aria-hidden />
          All providers
        </Link>
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="font-display text-[32px] leading-[1.05] font-bold tracking-[-0.03em]">
              {label}
            </h1>
            <p className="text-muted-foreground mt-1 text-[13px]">
              What this provider costs, which credential serves each model, and which{" "}
              {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()} may use it.
            </p>
          </div>
          {spendQ.data && (
            <div className="text-right">
              <p className="text-muted-foreground font-mono text-[10px] tracking-[0.12em] uppercase">
                This month
              </p>
              <p className="font-display text-[26px] leading-none font-bold tabular-nums">
                {usd(providerSpend)}
              </p>
            </div>
          )}
        </div>
      </div>

      {matrixQ.isError ? (
        <ErrorState title="Couldn't load this provider" onRetry={() => matrixQ.refetch()} />
      ) : loading ? (
        <LoadingState variant="card" />
      ) : (
        <>
          {/* ── Credentials ────────────────────────────────────────────────
              Named at onboarding (the Add provider dialog's "Display name"),
              which is what makes several keys on one provider legible: this
              screen can say WHICH key runs a model rather than just that one
              does. */}
          <Card className="border-line-soft bg-panel-elevated">
            <CardHeader className="pb-2">
              <h2 className="font-display text-[14px] font-bold tracking-[-0.01em]">
                Credentials
              </h2>
              <p className="text-muted-foreground text-[12px]">
                {credentials.length === 1
                  ? "One key serves this provider."
                  : `${credentials.length} keys, each covering the models listed against it.`}
              </p>
            </CardHeader>
            <CardContent className="pt-0">
              {credentials.length === 0 ? (
                <p className="text-muted-foreground text-[12.5px]">
                  No credential onboarded for {label} yet — its models are granted but inert.
                </p>
              ) : (
                <ul className="divide-line-soft border-line-soft divide-y rounded-lg border">
                  {credentials.map((c) => (
                    <li key={c.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 p-3">
                      <span className="text-[12.5px] font-medium">{c.display_name}</span>
                      <span
                        className={cn(
                          "shrink-0 rounded-full border px-1.5 py-px font-mono text-[9.5px] tracking-wide uppercase",
                          c.workspaceId === null
                            ? "border-success/40 bg-success/10 text-success"
                            : "border-line-soft text-muted-foreground",
                        )}
                      >
                        {c.workspaceId === null ? "Platform credential" : "Unit-scoped"}
                      </span>
                      <span className="text-muted-foreground font-mono text-[10.5px]">
                        {c.offerings.filter((o) => o.enabled).length}{" "}
                        {c.offerings.filter((o) => o.enabled).length === 1 ? "model" : "models"}
                      </span>
                      <span className="text-muted-foreground ml-auto font-mono text-[10.5px]">
                        {c.status}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          {/* ── Models: cost, credential, access ──────────────────────────── */}
          <Card className="border-line-soft bg-panel-elevated">
            <CardHeader className="pb-2">
              <h2 className="font-display text-[14px] font-bold tracking-[-0.01em]">
                Models &amp; access
              </h2>
              <p className="text-muted-foreground text-[12px]">
                Open a model&apos;s access to grant or revoke it per{" "}
                {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase().replace(/s$/, "")}, or revoke it
                everywhere.
              </p>
            </CardHeader>
            <CardContent className="pt-0">
              {rows.length === 0 ? (
                <p className="text-muted-foreground text-[12.5px]">
                  No models from {label} are in the catalogue yet.
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[640px] border-collapse text-left">
                    <thead>
                      <tr className="border-line-soft border-b">
                        <th className="text-muted-foreground py-2 pr-3 font-mono text-[10px] font-semibold tracking-wider uppercase">
                          Model
                        </th>
                        <th className="text-muted-foreground px-2 py-2 text-right font-mono text-[10px] font-semibold tracking-wider uppercase">
                          Spend
                        </th>
                        <th className="text-muted-foreground px-2 py-2 font-mono text-[10px] font-semibold tracking-wider uppercase">
                          Credential
                        </th>
                        <th className="text-muted-foreground px-2 py-2 font-mono text-[10px] font-semibold tracking-wider uppercase">
                          Access
                        </th>
                        <th className="w-10" />
                      </tr>
                    </thead>
                    <tbody className="divide-line-soft divide-y">
                      {rows.map((row) => {
                        const isGlobal = row.visibility === "global";
                        return (
                          <tr key={`${row.model_id}::${row.credentialId ?? ""}`}>
                            <td className="py-2.5 pr-3">
                              <div className="flex flex-wrap items-center gap-2">
                                <span className="font-mono text-[12px]">{row.model_id}</span>
                                {!row.granted && (
                                  <span className="text-muted-foreground border-line-soft shrink-0 rounded-full border px-1.5 py-px font-mono text-[9px] tracking-wide uppercase">
                                    Not granted
                                  </span>
                                )}
                                {isGlobal && (
                                  <span className="text-muted-foreground border-line-soft shrink-0 rounded-full border px-1.5 py-px font-mono text-[9px] tracking-wide uppercase">
                                    Global
                                  </span>
                                )}
                              </div>
                            </td>

                            <td className="px-2 py-2.5 text-right font-mono text-[12px] tabular-nums">
                              {spendQ.data ? usd(spendByModel.get(row.model_id) ?? 0) : "—"}
                              {sharedModels.has(row.model_id) && (
                                // The figure is the MODEL's, not this key's —
                                // usage is not recorded per subscription, and
                                // printing it unqualified on two rows would
                                // read as twice the money.
                                <span className="text-muted-foreground ml-1 block font-sans text-[10px] font-normal">
                                  across all keys
                                </span>
                              )}
                            </td>

                            <td className="px-2 py-2.5">
                              {row.credentialName ? (
                                <span className="text-[11.5px]">{row.credentialName}</span>
                              ) : (
                                <span className="text-warning inline-flex items-center gap-1 font-mono text-[10px] tracking-wide uppercase">
                                  <KeyRound className="size-2.5" aria-hidden />
                                  No key
                                </span>
                              )}
                            </td>

                            <td className="px-2 py-2.5">
                              <UnitAccessPicker
                                units={units.map((u) => ({ id: u.id, name: u.name }))}
                                selected={row.units.filter((x) => x.hasAccess).map((x) => x.id)}
                                isGlobal={isGlobal}
                                disabled={!row.granted || saveM.isPending}
                                onToggle={(unitId) => toggleCell(row, unitId)}
                              />
                            </td>

                            <td className="py-2.5 text-right">
                              {row.granted && (
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
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
