"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Building2, Globe, Loader2, ShieldCheck } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { GrantVisibilityControl } from "@/components/app/grant-visibility-control";
import { listConnectorGrants, setConnectorGrants } from "@/lib/api/connectors";
import { qk } from "@/lib/api/query-keys";
import { useWorkspaces } from "@/hooks/use-workspaces";
import { BUSINESS_UNIT_LABEL_PLURAL } from "@/lib/scope";
import type { GrantVisibility } from "@/lib/schemas/grant";
import type { ConnectorGrant } from "@/lib/schemas/connector";
import type { ConnectorKind } from "@/lib/schemas/enums";

interface DraftGrant {
  visibility: GrantVisibility;
  businessUnitIds: string[];
}

/**
 * The Organization Admin's connector policy — the exact counterpart of
 * `ModelAccessMatrix`, and deliberately the same shape: the two cascades govern
 * different resources but the same way, and an admin who has learned one
 * should not have to learn the other.
 *
 * A kind that isn't permitted here is absent from every {BUSINESS_UNIT_LABEL_PLURAL}'s
 * catalogue — not greyed out, absent — and any org-wide connection of that
 * kind stops being inherited by them. That is stronger than hiding a button:
 * the list endpoint filters on it too, so a BU Admin cannot reach one by URL.
 */
export function ConnectorGrantsCard({
  /** The catalogue of kinds this platform offers, in the page's own order. */
  kinds,
  kindLabel,
}: {
  kinds: ConnectorKind[];
  kindLabel: (kind: ConnectorKind) => string;
}) {
  const queryClient = useQueryClient();
  const { data: workspacesData } = useWorkspaces();
  const workspaces = React.useMemo(
    () => (workspacesData ?? []).filter((w) => w.status === "active"),
    [workspacesData],
  );

  const grantsQ = useQuery({
    queryKey: qk.connectors.grants(null),
    queryFn: () => listConnectorGrants(),
  });
  const grants = React.useMemo(() => grantsQ.data ?? [], [grantsQ.data]);

  const [draft, setDraft] = React.useState<Map<string, DraftGrant> | null>(null);

  const serverSignature = React.useMemo(
    () =>
      grants
        .map((g) => `${g.kind}:${g.visibility}:${[...g.businessUnitIds].sort().join("+")}`)
        .sort()
        .join("|"),
    [grants],
  );
  React.useEffect(() => setDraft(null), [serverSignature]);

  const serverMap = React.useMemo(() => {
    const m = new Map<string, DraftGrant>();
    for (const g of grants) {
      m.set(g.kind, { visibility: g.visibility, businessUnitIds: [...g.businessUnitIds] });
    }
    return m;
  }, [grants]);

  const current = draft ?? serverMap;

  const mutate = (fn: (next: Map<string, DraftGrant>) => void) => {
    const next = new Map(current);
    fn(next);
    setDraft(next);
  };

  const saveM = useMutation({
    mutationFn: (next: ConnectorGrant[]) => setConnectorGrants(next),
    onSuccess: () => {
      toast.success("Connector access updated");
      // Which connectors a unit can see is derived from these, so the
      // connector lists everywhere have to refetch, not just this card.
      queryClient.invalidateQueries({ queryKey: ["connectors"] });
    },
    onError: (err) =>
      toast.error("Couldn't update connector access", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  const save = () => {
    const next: ConnectorGrant[] = [];
    for (const [kind, v] of current) {
      next.push({
        kind: kind as ConnectorKind,
        visibility: v.visibility,
        businessUnitIds: v.businessUnitIds,
      });
    }
    saveM.mutate(next);
  };

  const dirty = draft !== null;
  const globalCount = [...current.values()].filter((g) => g.visibility === "global").length;

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
            <h3 className="font-display text-[14px] font-bold tracking-[-0.01em]">
              Connector access
            </h3>
            <p className="text-muted-foreground mt-0.5 text-[12px]">
              Which integrations this organization permits, and who gets them. A{" "}
              {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase().slice(0, -1)} can only connect and use what
              you permit here.
            </p>
            {!grantsQ.isLoading && current.size > 0 && (
              <p className="text-muted-foreground mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[10.5px]">
                <span className="inline-flex items-center gap-1">
                  <Globe className="size-3" aria-hidden />
                  {globalCount} global
                </span>
                <span className="inline-flex items-center gap-1">
                  <Building2 className="size-3" aria-hidden />
                  {current.size - globalCount} restricted
                </span>
              </p>
            )}
          </div>
          <div className="flex shrink-0 gap-2">
            {dirty && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setDraft(null)}
                disabled={saveM.isPending}
              >
                Cancel
              </Button>
            )}
            <Button
              size="sm"
              onClick={save}
              disabled={!dirty || saveM.isPending}
              aria-busy={saveM.isPending}
            >
              {saveM.isPending ? <Loader2 className="size-4 animate-spin" aria-hidden /> : null}
              {saveM.isPending ? "Saving…" : "Save"}
            </Button>
          </div>
        </div>
      </CardHeader>

      <CardContent className="pt-0">
        {grantsQ.isLoading ? (
          <p className="text-muted-foreground text-[12.5px]">Loading…</p>
        ) : (
          <ul className="divide-line-soft border-line-soft divide-y rounded-xl border">
            {kinds.map((kind) => {
              const grant = current.get(kind);
              const checkboxId = `connector-grant-${kind}`;
              return (
                <li key={kind} className="space-y-2 p-3">
                  <div className="flex items-center gap-2.5">
                    <Checkbox
                      id={checkboxId}
                      checked={!!grant}
                      disabled={saveM.isPending}
                      onCheckedChange={() =>
                        mutate((next) => {
                          if (next.has(kind)) next.delete(kind);
                          else next.set(kind, { visibility: "global", businessUnitIds: [] });
                        })
                      }
                    />
                    <Label
                      htmlFor={checkboxId}
                      className={cn(
                        "min-w-0 flex-1 cursor-pointer truncate text-[13px] font-normal",
                        !grant && "text-muted-foreground",
                      )}
                    >
                      {kindLabel(kind)}
                    </Label>
                  </div>
                  {grant && (
                    <GrantVisibilityControl
                      className="pl-[26px]"
                      idPrefix={`connector-grant-${kind}`}
                      value={grant}
                      workspaces={workspaces}
                      disabled={saveM.isPending}
                      onChange={(next) => mutate((map) => map.set(kind, next))}
                    />
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
