"use client";

import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowLeft, Check, Loader2, Minus, Plug } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { LoadingState } from "@/components/ui/loading-state";
import { ErrorState } from "@/components/ui/error-state";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { RestrictedAccess } from "@/components/auth/restricted-access";
import { useAccessScope } from "@/hooks/use-access-scope";
import { useWorkspaces } from "@/hooks/use-workspaces";
import {
  listConnectorGrants,
  listConnectors,
  setConnectorGrants,
} from "@/lib/api/connectors";
import { qk } from "@/lib/api/query-keys";
import { grantReaches } from "@/lib/schemas/grant";
import { BUSINESS_UNIT_LABEL_PLURAL } from "@/lib/scope";
import type { Connector, ConnectorGrant } from "@/lib/schemas/connector";
import type { ConnectorKind } from "@/lib/schemas/enums";

/**
 * One connector kind, in full — the Integrations twin of the provider detail
 * screen, and deliberately the same shape: the two cascades govern different
 * resources by identical rules, so they should not be learned twice.
 *
 * NO SPEND HERE, unlike a model provider. Connectors are billed by their own
 * vendor (PRD §34.5), so a spend figure on this page would be an invented
 * number — the one thing a cost surface must never be. What this owns instead
 * is the connection itself and who may use it.
 */
export default function ConnectorDetailPage() {
  const params = useParams<{ kind: string }>();
  const kind = decodeURIComponent(params.kind) as ConnectorKind;
  const queryClient = useQueryClient();
  const { role } = useAccessScope();

  const { data: workspacesData } = useWorkspaces();
  const units = React.useMemo(
    () => (workspacesData ?? []).filter((w) => w.status === "active"),
    [workspacesData],
  );

  const connectorsQ = useQuery({
    queryKey: qk.connectors.list(),
    queryFn: () => listConnectors(),
  });
  const grantsQ = useQuery({
    queryKey: qk.connectors.grants(),
    queryFn: () => listConnectorGrants(),
  });

  const grants = React.useMemo(() => grantsQ.data ?? [], [grantsQ.data]);
  const grant = React.useMemo(() => grants.find((g) => g.kind === kind), [grants, kind]);

  /**
   * Every connection of this kind — org-wide and unit-scoped together.
   *
   * Listed rather than collapsed to one because the scopes answer different
   * questions: an org-wide connection serves everyone, a unit-scoped one
   * serves its own unit and nobody else, and a page that showed only the first
   * would report a unit's own integration as missing.
   */
  const connections: Connector[] = React.useMemo(
    () => (connectorsQ.data ?? []).filter((c) => c.kind === kind && c.installed),
    [connectorsQ.data, kind],
  );

  const saveM = useMutation({
    mutationFn: (entries: ConnectorGrant[]) => setConnectorGrants(entries),
    onSuccess: () => {
      toast.success("Connector access updated");
      queryClient.invalidateQueries({ queryKey: ["connectors"] });
    },
    onError: (err) =>
      toast.error("Couldn't update connector access", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  function toggleUnit(unitId: string) {
    if (!grant) return;
    saveM.mutate(
      grants.map((g) => {
        if (g.kind !== kind) return g;
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

  function revoke() {
    saveM.mutate(grants.filter((g) => g.kind !== kind));
  }

  if (role !== null && role !== "org_admin") {
    return (
      <RestrictedAccess description="Connector detail is the Organization Admin's — it names every business unit's access to this integration." />
    );
  }

  const loading = connectorsQ.isLoading || grantsQ.isLoading;
  const name = connections[0]?.name ?? kind;
  const isGlobal = grant?.visibility === "global";

  return (
    <div className="w-full space-y-6 p-4 md:px-10 md:py-8">
      <div>
        <Link
          href="/integrations"
          className="text-muted-foreground hover:text-foreground mb-3 inline-flex items-center gap-1 font-mono text-[11px] transition-colors"
        >
          <ArrowLeft className="size-3" aria-hidden />
          All integrations
        </Link>
        <h1 className="font-display text-[32px] leading-[1.05] font-bold tracking-[-0.03em]">
          {name}
        </h1>
        <p className="text-muted-foreground mt-1 text-[13px]">
          How this integration is connected, and which{" "}
          {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()} may use it.
        </p>
      </div>

      {connectorsQ.isError ? (
        <ErrorState title="Couldn't load this connector" onRetry={() => connectorsQ.refetch()} />
      ) : loading ? (
        <LoadingState variant="card" />
      ) : (
        <>
          <Card className="border-line-soft bg-panel-elevated">
            <CardHeader className="pb-2">
              <h2 className="font-display text-[14px] font-bold tracking-[-0.01em]">Connections</h2>
              <p className="text-muted-foreground text-[12px]">
                Where the credentials for this integration live.
              </p>
            </CardHeader>
            <CardContent className="pt-0">
              {connections.length === 0 ? (
                <p className="text-muted-foreground text-[12.5px]">
                  Not connected anywhere yet — granted units can connect it themselves.
                </p>
              ) : (
                <ul className="divide-line-soft border-line-soft divide-y rounded-lg border">
                  {connections.map((c) => (
                    <li key={c.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 p-3">
                      <Plug className="text-muted-foreground size-3.5 shrink-0" aria-hidden />
                      <span className="text-[12.5px] font-medium">{c.name}</span>
                      <span
                        className={cn(
                          "shrink-0 rounded-full border px-1.5 py-px font-mono text-[9.5px] tracking-wide uppercase",
                          c.scope === "organization"
                            ? "border-success/40 bg-success/10 text-success"
                            : "border-line-soft text-muted-foreground",
                        )}
                      >
                        {c.scope === "organization" ? "Platform connection" : "Unit-scoped"}
                      </span>
                      {c.account && (
                        <span className="text-muted-foreground truncate font-mono text-[10.5px]">
                          {c.account}
                        </span>
                      )}
                      <span
                        className={cn(
                          "ml-auto shrink-0 font-mono text-[10.5px]",
                          c.health === "healthy" ? "text-success" : "text-warning",
                        )}
                      >
                        {c.health}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          <Card className="border-line-soft bg-panel-elevated">
            <CardHeader className="pb-2">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h2 className="font-display text-[14px] font-bold tracking-[-0.01em]">Access</h2>
                  <p className="text-muted-foreground text-[12px]">
                    Click a{" "}
                    {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase().replace(/s$/, "")} to grant or
                    revoke.
                  </p>
                </div>
                {grant && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-muted-foreground hover:text-destructive h-7 px-2 text-[11px]"
                    disabled={saveM.isPending}
                    onClick={revoke}
                  >
                    {saveM.isPending ? (
                      <Loader2 className="size-3 animate-spin" aria-hidden />
                    ) : (
                      "Revoke everywhere"
                    )}
                  </Button>
                )}
              </div>
            </CardHeader>
            <CardContent className="pt-0">
              {!grant ? (
                <p className="text-muted-foreground text-[12.5px]">
                  Not approved for the organization — it is absent from every unit&apos;s
                  catalogue, not merely disabled.
                </p>
              ) : (
                <>
                  {isGlobal && (
                    <p className="text-muted-foreground mb-2 text-[11.5px]">
                      Granted globally — reaches every{" "}
                      {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase().replace(/s$/, "")}. Switch it to
                      specific on Integrations to name units.
                    </p>
                  )}
                  <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                    {units.map((u) => {
                      const has = grantReaches(grant, String(u.id));
                      return (
                        <li key={u.id}>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <button
                                type="button"
                                disabled={isGlobal || saveM.isPending}
                                onClick={() => toggleUnit(String(u.id))}
                                aria-label={`${has ? "Revoke" : "Grant"} ${name} for ${u.displayName}`}
                                className={cn(
                                  "border-line-soft focus-visible:ring-ring flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-left transition-colors focus-visible:ring-2 focus-visible:outline-none disabled:cursor-default",
                                  has ? "bg-success/5" : "hover:bg-surface-1",
                                  isGlobal && "opacity-70",
                                )}
                              >
                                <span
                                  className={cn(
                                    "grid size-5 shrink-0 place-items-center rounded border",
                                    has
                                      ? "border-success/40 bg-success/10 text-success"
                                      : "border-line-soft text-muted-foreground/40",
                                  )}
                                >
                                  {has ? (
                                    <Check className="size-3" aria-hidden />
                                  ) : (
                                    <Minus className="size-2.5" aria-hidden />
                                  )}
                                </span>
                                <span className="min-w-0 flex-1 truncate text-[12.5px]">
                                  {u.displayName}
                                </span>
                              </button>
                            </TooltipTrigger>
                            <TooltipContent side="top">
                              {isGlobal
                                ? "Granted globally to every unit."
                                : has
                                  ? `${u.displayName} may use ${name}.`
                                  : `Click to grant ${u.displayName}.`}
                            </TooltipContent>
                          </Tooltip>
                        </li>
                      );
                    })}
                  </ul>
                </>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
