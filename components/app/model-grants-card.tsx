"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Building2,
  CheckCircle2,
  ChevronRight,
  Globe,
  KeyRound,
  Loader2,
  ShieldCheck,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { GrantVisibilityControl } from "@/components/app/grant-visibility-control";
import {
  getModelCatalog,
  getModelGrantMatrix,
  getOrgModelGrants,
  setOrgModelGrants,
} from "@/lib/api/models";
import { qk } from "@/lib/api/query-keys";
import { useWorkspaces } from "@/hooks/use-workspaces";
import { BUSINESS_UNIT_LABEL, BUSINESS_UNIT_LABEL_PLURAL } from "@/lib/scope";
import type { GrantVisibility } from "@/lib/schemas/grant";
import type {
  CatalogProvider,
  ModelGrantMatrixRow,
  OrgModelGrant,
} from "@/lib/schemas/model";

const keyOf = (provider: string, modelId: string) => `${provider}::${modelId}`;

interface DraftGrant {
  visibility: GrantVisibility;
  businessUnitIds: string[];
}

/**
 * The Organization Admin's model catalogue policy: which models the
 * organization has approved, and how far each approval reaches.
 *
 * This is the top of the cascade and the only place models enter it. A model
 * that isn't checked here does not exist for anyone — no Business Unit Admin
 * can add it, no Project Admin can credential it, no run can select it.
 * Checking one and leaving it Global is the common case and needs no further
 * thought; Specific is for the models that belong to particular parts of the
 * business, and names them explicitly.
 *
 * Credentials are a separate decision, deliberately: granting a model says who
 * may use it, not who pays for the key. The provider cards below this handle
 * the key, and the "needs credentials" annotations there are what connect the
 * two.
 */
export function ModelGrantsCard() {
  const queryClient = useQueryClient();
  const { data: workspacesData } = useWorkspaces();
  const workspaces = React.useMemo(
    () => (workspacesData ?? []).filter((w) => w.status === "active"),
    [workspacesData],
  );

  const catalogQ = useQuery({ queryKey: qk.model.catalog(), queryFn: getModelCatalog });
  const grantsQ = useQuery({ queryKey: qk.model.orgGrants(), queryFn: getOrgModelGrants });

  /**
   * Credential facts — who is keyed, centrally or locally.
   *
   * Kept SEPARATE from reach on purpose. Reach ("which units get this") is read
   * from the live draft below, so ticking a unit updates the summary
   * immediately; credentials are the server's answer and cannot change from
   * this card at all. Reading reach from the matrix instead would make the
   * summary lag a whole save behind the checkbox that drives it.
   */
  const matrixQ = useQuery({ queryKey: qk.model.grantMatrix(), queryFn: getModelGrantMatrix });
  const credentials = React.useMemo(() => {
    const m = new Map<string, ModelGrantMatrixRow>();
    for (const r of matrixQ.data?.rows ?? []) m.set(keyOf(r.provider, r.model_id), r);
    return m;
  }, [matrixQ.data]);
  const orgKeyed = React.useMemo(
    () => new Set(matrixQ.data?.centrallyKeyedProviders ?? []),
    [matrixQ.data],
  );

  /**
   * Which providers are open.
   *
   * Everything starts collapsed EXCEPT providers that have something granted:
   * an admin opening this page is far likelier to be checking what is already
   * in force than browsing the catalogue, and a wall of expanded providers
   * buries exactly that. Null means "not yet initialised" so the seeding
   * effect can tell an untouched card from one deliberately all-collapsed.
   */
  const [openProviders, setOpenProviders] = React.useState<Set<string> | null>(null);

  const catalog: CatalogProvider[] = React.useMemo(() => catalogQ.data ?? [], [catalogQ.data]);
  const grants = React.useMemo(() => grantsQ.data ?? [], [grantsQ.data]);

  // The catalog is what CAN be granted; the grants may also name a custom
  // model that was onboarded by hand and is therefore absent from it. Both
  // have to be listed, or unchecking would be the only way to interact with a
  // custom model — and it wouldn't even be visible to uncheck.
  const rows = React.useMemo(() => {
    const out: { provider: string; label: string; models: { model_id: string }[] }[] = catalog.map(
      (c) => ({ provider: c.provider, label: c.label, models: c.models.map((m) => ({ model_id: m.model_id })) }),
    );
    for (const g of grants) {
      let group = out.find((o) => o.provider === g.provider);
      if (!group) {
        group = { provider: g.provider, label: g.provider, models: [] };
        out.push(group);
      }
      if (!group.models.some((m) => m.model_id === g.model_id)) {
        group.models.push({ model_id: g.model_id });
      }
    }
    return out;
  }, [catalog, grants]);

  const [draft, setDraft] = React.useState<Map<string, DraftGrant> | null>(null);

  // Re-seed whenever the server's answer changes, so a save (or another
  // admin's edit landing in a refetch) doesn't leave a stale draft on screen.
  const serverSignature = React.useMemo(
    () =>
      grants
        .map((g) => `${keyOf(g.provider, g.model_id)}:${g.visibility}:${[...g.businessUnitIds].sort().join("+")}`)
        .sort()
        .join("|"),
    [grants],
  );
  React.useEffect(() => setDraft(null), [serverSignature]);

  const serverMap = React.useMemo(() => {
    const m = new Map<string, DraftGrant>();
    for (const g of grants) {
      m.set(keyOf(g.provider, g.model_id), {
        visibility: g.visibility,
        businessUnitIds: [...g.businessUnitIds],
      });
    }
    return m;
  }, [grants]);

  const current = draft ?? serverMap;

  React.useEffect(() => {
    if (openProviders !== null || grants.length === 0) return;
    setOpenProviders(new Set(grants.map((g) => g.provider)));
  }, [grants, openProviders]);

  const isOpen = (provider: string) => openProviders?.has(provider) ?? false;
  const toggleProvider = (provider: string) =>
    setOpenProviders((prev) => {
      const next = new Set(prev ?? []);
      if (next.has(provider)) next.delete(provider);
      else next.add(provider);
      return next;
    });

  /**
   * The units a grant currently reaches, from the DRAFT — so the summary moves
   * with the checkbox rather than with the last save.
   */
  const unitsFor = React.useCallback(
    (grant: DraftGrant | undefined): { id: string; name: string }[] => {
      if (!grant) return [];
      if (grant.visibility === "global") {
        return workspaces.map((w) => ({ id: String(w.id), name: w.displayName }));
      }
      return workspaces
        .filter((w) => grant.businessUnitIds.includes(String(w.id)))
        .map((w) => ({ id: String(w.id), name: w.displayName }));
    },
    [workspaces],
  );

  const mutate = (fn: (next: Map<string, DraftGrant>) => void) => {
    const next = new Map(current);
    fn(next);
    setDraft(next);
  };

  const toggle = (provider: string, modelId: string) => {
    const k = keyOf(provider, modelId);
    mutate((next) => {
      if (next.has(k)) next.delete(k);
      else next.set(k, { visibility: "global", businessUnitIds: [] });
    });
  };

  const saveM = useMutation({
    mutationFn: (entries: OrgModelGrant[]) => setOrgModelGrants(entries),
    onSuccess: () => {
      toast.success("Model access updated");
      // Grants drive what every unit and project sees, so a change here
      // invalidates far more than this card's own query.
      queryClient.invalidateQueries({ queryKey: qk.model.orgGrants() });
      queryClient.invalidateQueries({ queryKey: ["model"] });
    },
    onError: (err) =>
      toast.error("Couldn't update model access", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  const save = () => {
    const entries: OrgModelGrant[] = [];
    for (const [k, v] of current) {
      const [provider, model_id] = k.split("::");
      if (!provider || !model_id) continue;
      entries.push({ provider, model_id, visibility: v.visibility, businessUnitIds: v.businessUnitIds });
    }
    saveM.mutate(entries);
  };

  const dirty = draft !== null;
  const loading = catalogQ.isLoading || grantsQ.isLoading;

  const globalCount = [...current.values()].filter((g) => g.visibility === "global").length;
  const specificCount = current.size - globalCount;

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
              Which models this organization has approved, and who gets them. Nothing outside this
              list can be onboarded or used anywhere in the org.
            </p>
            {!loading && current.size > 0 && (
              <p className="text-muted-foreground mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[10.5px]">
                <span className="inline-flex items-center gap-1">
                  <Globe className="size-3" aria-hidden />
                  {globalCount} global
                </span>
                <span className="inline-flex items-center gap-1">
                  <Building2 className="size-3" aria-hidden />
                  {specificCount} restricted
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
            <Button size="sm" onClick={save} disabled={!dirty || saveM.isPending} aria-busy={saveM.isPending}>
              {saveM.isPending ? <Loader2 className="size-4 animate-spin" aria-hidden /> : null}
              {saveM.isPending ? "Saving…" : "Save"}
            </Button>
          </div>
        </div>
      </CardHeader>

      <CardContent className="pt-0">
        {loading ? (
          <p className="text-muted-foreground text-[12.5px]">Loading…</p>
        ) : rows.length === 0 ? (
          <p className="text-muted-foreground text-[12.5px]">
            No models in the catalogue yet. Add a provider below to bring one in.
          </p>
        ) : (
          <div className="space-y-3">
            {rows.map((group) => {
              const grantedCount = group.models.filter((m) =>
                current.has(keyOf(group.provider, m.model_id)),
              ).length;
              const providerKeyed = orgKeyed.has(group.provider);
              const expanded = isOpen(group.provider);

              return (
                <div
                  key={group.provider}
                  className="border-line-soft overflow-hidden rounded-xl border"
                >
                  {/* ── Provider header ─────────────────────────────────── */}
                  <button
                    type="button"
                    onClick={() => toggleProvider(group.provider)}
                    aria-expanded={expanded}
                    className="hover:bg-surface-1 focus-visible:ring-ring flex w-full items-center gap-2.5 px-3 py-2.5 text-left transition-colors focus-visible:ring-2 focus-visible:outline-none"
                  >
                    <ChevronRight
                      className={cn(
                        "text-muted-foreground size-3.5 shrink-0 transition-transform",
                        expanded && "rotate-90",
                      )}
                      aria-hidden
                    />
                    <span className="font-display text-[13px] font-bold tracking-[-0.01em]">
                      {group.label}
                    </span>

                    {/* The badge that answers "does anyone downstream have to
                        do anything?" before a single row is read. */}
                    {providerKeyed ? (
                      <span className="border-success/40 bg-success/10 text-success inline-flex shrink-0 items-center gap-1 rounded-full border px-1.5 py-px font-mono text-[9.5px] tracking-wide uppercase">
                        <KeyRound className="size-2.5" aria-hidden />
                        Org-keyed
                      </span>
                    ) : (
                      <span className="border-line-soft text-muted-foreground inline-flex shrink-0 items-center gap-1 rounded-full border px-1.5 py-px font-mono text-[9.5px] tracking-wide uppercase">
                        No org key
                      </span>
                    )}

                    <span className="text-muted-foreground ml-auto shrink-0 font-mono text-[10.5px]">
                      {grantedCount} of {group.models.length} granted
                    </span>
                  </button>

                  {expanded && (
                    <ul className="divide-line-soft border-line-soft divide-y border-t">
                      {group.models.map((m) => {
                        const k = keyOf(group.provider, m.model_id);
                        const grant = current.get(k);
                        const checkboxId = `grant-${group.provider}-${m.model_id}`;
                        const cred = credentials.get(k);
                        const central = cred?.centrallyCredentialed ?? providerKeyed;
                        const reach = unitsFor(grant);
                        // Only interesting where there is no central key —
                        // otherwise nobody needed to key anything.
                        const selfKeyed = central
                          ? []
                          : reach.filter(
                              (u) =>
                                cred?.units.find((cu) => cu.id === u.id)?.locallyCredentialed,
                            );

                        return (
                          <li key={m.model_id} className="space-y-2 p-3">
                            <div className="flex flex-wrap items-center gap-2">
                              <Checkbox
                                id={checkboxId}
                                checked={!!grant}
                                disabled={saveM.isPending}
                                onCheckedChange={() => toggle(group.provider, m.model_id)}
                              />
                              <Label
                                htmlFor={checkboxId}
                                className={cn(
                                  "min-w-0 flex-1 cursor-pointer truncate font-mono text-[12px] font-normal",
                                  !grant && "text-muted-foreground",
                                )}
                              >
                                {m.model_id}
                              </Label>

                              {/* Granting says who MAY use it; this says
                                  whether they can without doing anything. */}
                              {grant &&
                                (central ? (
                                  <span className="text-success inline-flex shrink-0 items-center gap-1 font-mono text-[10px] tracking-wide uppercase">
                                    <CheckCircle2 className="size-3" aria-hidden />
                                    Ready
                                  </span>
                                ) : (
                                  <span className="text-warning inline-flex shrink-0 items-center gap-1 font-mono text-[10px] tracking-wide uppercase">
                                    <KeyRound className="size-3" aria-hidden />
                                    Needs a key
                                  </span>
                                ))}
                            </div>

                            {grant && (
                              <div className="space-y-1.5 pl-[26px]">
                                {/* Who has it, in one line, before the editor. */}
                                <p className="text-muted-foreground flex flex-wrap items-center gap-x-1.5 text-[11.5px]">
                                  {grant.visibility === "global" ? (
                                    <>
                                      <Globe className="size-3 shrink-0" aria-hidden />
                                      All {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()} (
                                      {workspaces.length})
                                    </>
                                  ) : reach.length === 0 ? (
                                    <>
                                      <Building2 className="size-3 shrink-0" aria-hidden />
                                      <span className="text-warning">
                                        No {BUSINESS_UNIT_LABEL.toLowerCase()} named — nobody
                                        can use this yet
                                      </span>
                                    </>
                                  ) : (
                                    <>
                                      <Building2 className="size-3 shrink-0" aria-hidden />
                                      {reach.map((u) => u.name).join(", ")}
                                    </>
                                  )}
                                </p>

                                {!central && reach.length > 0 && (
                                  <p className="text-muted-foreground text-[11px]">
                                    {selfKeyed.length === 0
                                      ? "None of them have keyed it — granted, but inert until one does."
                                      : `Keyed by ${selfKeyed.map((u) => u.name).join(", ")}${
                                          selfKeyed.length < reach.length
                                            ? ` · still unkeyed for ${reach
                                                .filter((u) => !selfKeyed.some((s) => s.id === u.id))
                                                .map((u) => u.name)
                                                .join(", ")}`
                                            : ""
                                        }`}
                                  </p>
                                )}

                                <GrantVisibilityControl
                                  idPrefix={`grant-${group.provider}-${m.model_id}`}
                                  value={grant}
                                  workspaces={workspaces}
                                  disabled={saveM.isPending}
                                  onChange={(next) => mutate((map) => map.set(k, next))}
                                />
                              </div>
                            )}
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </div>
              );
            })}
          </div>
        )}

        <p className="text-muted-foreground mt-4 text-[11.5px]">
          Granting a model says who may use it, not who pays for it. Add a provider below to
          credential one centrally — anything you leave uncredentialed, the receiving{" "}
          {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()} can key themselves.
        </p>
      </CardContent>
    </Card>
  );
}
