"use client";

import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ArrowLeft,
  FolderPlus,
  Globe,
  KeyRound,
  Pencil,
  Search,
  Trash2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  Command,
  CommandEmpty,
  CommandInput,
  CommandItem,
  CommandList,
  substringFilter,
} from "@/components/ui/command";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { LoadingState } from "@/components/ui/loading-state";
import { ErrorState } from "@/components/ui/error-state";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { RestrictedAccess } from "@/components/auth/restricted-access";
import { EditProviderDialog } from "@/components/app/edit-provider-dialog";
import { SpendRankedBars } from "@/components/app/spend-bar-chart";
import { useAccessScope } from "@/hooks/use-access-scope";
import { useScopedBusinessUnits } from "@/hooks/use-scoped-business-units";
import { getSpendSeries } from "@/lib/api/cost";
import {
  assignProviderToProject,
  deleteModelProvider,
  getModelCatalog,
  listModelProviders,
} from "@/lib/api/models";
import { listProjects } from "@/lib/api/projects";
import { qk } from "@/lib/api/query-keys";
import { providerLabel } from "@/lib/models/provider-labels";
import { BUSINESS_UNIT_LABEL, BUSINESS_UNIT_LABEL_PLURAL } from "@/lib/scope";
import type { ModelProvider } from "@/lib/schemas/model";
import type { Project } from "@/lib/schemas";

const usd = (n: number) =>
  n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });

/**
 * One provider, gated by role.
 *
 * `useAccessScope`'s `role` is async (a query, not derived from the session
 * synchronously), so it is genuinely null for a beat on every load — that is
 * the still-resolving state, never "unauthenticated", and must render neither
 * branch below rather than flash one and then swap it for the other.
 *
 * Business Unit Admin is the ONLY role with a real screen here. Org Admin
 * must NOT get one: this page used to be the Org Admin's own keys-detail
 * screen, and a review of an earlier task found they could still reach it by
 * direct URL and add an org-wide credential — a live violation of this plan's
 * single hardest constraint, "Org Admin never adds a key, period". Task 9.1
 * closed that by denying Org Admin here outright, and that denial is
 * intentionally NOT lifted by this task: `RestrictedAccess` covers
 * `role === "org_admin"` by simply falling into the same branch as every
 * other non-bu_admin role below, rather than getting a dedicated `if` that a
 * future edit could misread as "give them something real". There is no
 * Org-Admin-specific component in this file to route to any more — it was
 * deleted for exactly this reason (unreachable code with a working
 * credential-add button in it is worse than no code).
 */
export default function ProviderDetailPage() {
  const params = useParams<{ provider: string }>();
  const providerKind = decodeURIComponent(params.provider);
  const { role } = useAccessScope();

  if (role === null) {
    return (
      <div className="w-full space-y-6 p-4 md:px-10 md:py-8">
        <LoadingState variant="card" />
      </div>
    );
  }

  if (role === "bu_admin") {
    return <BuAdminProviderDetail providerKind={providerKind} />;
  }

  // Every other resolved role — org_admin included — is denied. See the doc
  // comment above for why org_admin specifically must land here too.
  return (
    <RestrictedAccess description="Provider detail is being rebuilt for the new access model and isn't available yet." />
  );
}

// ───────── Business Unit Admin's view ─────────

/** One row of the BU Admin's table: a model offered by one of the
 *  credentials the viewer can see for this provider. */
interface BuKeyRow {
  model_id: string;
  offeringId: string;
  isDefault: boolean;
  credential: ModelProvider;
}

/**
 * One provider, from a Business Unit Admin's side.
 *
 * Built from scratch for Task 11 (see the ledger's Task 9.1 / Task 11 entries
 * — this page was locked to every role after a review found Org Admin could
 * reach it by direct URL and add an org-wide credential, a live violation of
 * "Org Admin never adds a key, period"). That same constraint is why there is
 * no Org-Admin-equivalent component in this file at all any more — an earlier
 * revision of this task briefly reintroduced one (a relocated copy of the
 * pre-9.1 org-wide grant view, "Add model" button and all) and routed
 * `role === "org_admin"` to it, which un-did Task 9.1's fix. It has been
 * deleted; `ProviderDetailPage` sends every role except `bu_admin` to
 * `RestrictedAccess`, org_admin included, with no dedicated branch for it to
 * fall through past. What a Business Unit Admin does here — manage the keys
 * their own unit(s) actually hold and decide which of their projects gets to
 * use one — is also just a smaller, credential-first job than the org-wide
 * grant governance (global/specific visibility, per-unit access, the default
 * model) that page used to do, so there was never a reason to share a
 * component between the two even before the deletion.
 *
 * SCOPING. `listModelProviders(workspaceId)` (already used by the list page's
 * own `!isOrg` branch) returns org-wide connections plus that ONE workspace's
 * own — never another unit's — so calling it once per unit the viewer is
 * bound to (`useScopedBusinessUnits`) and de-duplicating by connection id is
 * enough to guarantee no credential belonging to a unit the viewer isn't
 * bound to ever reaches this component. No client-side filter stands between
 * that guarantee and the render — the query shape IS the boundary.
 *
 * MANAGE ACTIONS ARE OWN-UNIT ONLY. A connection with `workspaceId === null`
 * is the Org Admin's own org-wide key: it is real and useful to see (it may
 * be exactly what covers a model for this unit), but editing, deleting or
 * assigning it to a project is not this admin's call — and the assign
 * endpoint itself rejects an org-wide provider outright (a provider with no
 * workspace can never match a project's), so offering the button there would
 * be a control that always 404s. Those rows render read-only with a
 * "Centrally keyed" marker instead. Edit / Delete / Assign only appear on a
 * row whose credential's `workspaceId` names one of the viewer's own units.
 */
function BuAdminProviderDetail({ providerKind }: { providerKind: string }) {
  const queryClient = useQueryClient();
  const { units, isLoading: unitsLoading } = useScopedBusinessUnits();
  const unitNameById = React.useMemo(
    () => new Map(units.map((u) => [u.id, u.name] as const)),
    [units],
  );

  // One connections query per unit the viewer administers. Each is already
  // scoped server-side to "org-wide + this one unit's own" — see the
  // component doc above.
  const unitProvidersQ = useQueries({
    queries: units.map((u) => ({
      queryKey: qk.model.providers(u.id),
      queryFn: () => listModelProviders(u.id),
      staleTime: 0,
    })),
  });
  const providersLoading = unitsLoading || unitProvidersQ.some((q) => q.isLoading);
  const providersError = unitProvidersQ.find((q) => q.isError);

  const spendQ = useQuery({
    queryKey: qk.cost.spendSeries("model", "all", 6),
    // Already scoped server-side to the caller's readable units (spend.py's
    // `allowed_workspace_ids`), so no per-unit filtering is needed here.
    queryFn: () => getSpendSeries({ groupBy: "model", months: 6 }),
    staleTime: 60_000,
  });
  const catalogQ = useQuery({ queryKey: qk.model.catalog(), queryFn: getModelCatalog });

  // The candidates for "Assign to project" — already scoped server-side to
  // what this viewer may see (projects.py's `visible_project_ids`), narrowed
  // further per-row to the credential's own unit (see `AssignToProjectButton`).
  const projectsQ = useQuery({
    queryKey: qk.projects.list({ pageSize: 100 }),
    queryFn: () => listProjects({ pageSize: 100 }),
    staleTime: 30_000,
  });
  const projects = React.useMemo(() => projectsQ.data?.items ?? [], [projectsQ.data]);

  /** Every connection serving this provider that the viewer may see — org-wide
   *  ones plus each of their own units'. De-duplicated: an org-wide key shows
   *  up once per unit query above.
   *
   *  EXCLUDES project-scoped rows. `listModelProviders(workspaceId)` filters
   *  only on `workspace_id IS NULL OR workspace_id = :w`, with no `project_id`
   *  filter — so a project's own BYOK key (which carries its owning project's
   *  workspace_id too, see `create_project_provider_route`) would otherwise
   *  land here indistinguishable from a genuinely BU-scoped key, complete with
   *  Assign/Edit/Delete controls a BU Admin has no business exercising on
   *  another admin's project's private credential. */
  const credentials: ModelProvider[] = React.useMemo(() => {
    const byId = new Map<string, ModelProvider>();
    for (const q of unitProvidersQ) {
      for (const p of q.data ?? []) {
        if (p.provider === providerKind && !p.projectId) byId.set(p.id, p);
      }
    }
    return [...byId.values()];
  }, [unitProvidersQ, providerKind]);

  /** One row per (model, credential) — the same grain the org table uses, so
   *  a model on two of this unit's keys still gets two rows rather than
   *  silently picking one. Only ENABLED offerings: a disabled one is not
   *  something this unit can actually run, and `assignProviderToProject`
   *  itself only ever pushes the enabled set. */
  const rows: BuKeyRow[] = React.useMemo(
    () =>
      credentials.flatMap((c) =>
        c.offerings
          .filter((o) => o.enabled)
          .map((o) => ({
            model_id: o.model_id,
            offeringId: o.id,
            isDefault: o.is_default,
            credential: c,
          })),
      ),
    [credentials],
  );

  const spendByModel = React.useMemo(() => {
    const m = new Map<string, number>();
    for (const s of spendQ.data?.series ?? []) {
      m.set(s.id, s.points[s.points.length - 1] ?? 0);
    }
    return m;
  }, [spendQ.data]);

  const providerSpend = React.useMemo(() => {
    const seen = new Set<string>();
    return rows.reduce((a, r) => {
      if (seen.has(r.model_id)) return a;
      seen.add(r.model_id);
      return a + (spendByModel.get(r.model_id) ?? 0);
    }, 0);
  }, [rows, spendByModel]);

  const modelSpendRows = React.useMemo(() => {
    const ids = new Set(rows.map((r) => r.model_id));
    return [...ids].map((id) => ({ id, name: id, value: spendByModel.get(id) ?? 0 }));
  }, [rows, spendByModel]);

  const sharedModels = React.useMemo(() => {
    const count = new Map<string, number>();
    for (const r of rows) count.set(r.model_id, (count.get(r.model_id) ?? 0) + 1);
    return new Set([...count.entries()].filter(([, n]) => n > 1).map(([id]) => id));
  }, [rows]);

  const [query, setQuery] = React.useState("");
  const visibleRows = React.useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter(
      (r) =>
        r.model_id.toLowerCase().includes(q) ||
        r.credential.display_name.toLowerCase().includes(q) ||
        (r.credential.workspaceId
          ? (unitNameById.get(r.credential.workspaceId) ?? "").toLowerCase().includes(q)
          : false),
    );
  }, [rows, query, unitNameById]);

  const [editing, setEditing] = React.useState<ModelProvider | null>(null);
  const [removing, setRemoving] = React.useState<ModelProvider | null>(null);

  const invalidateProviders = () => queryClient.invalidateQueries({ queryKey: ["model"] });

  const removeM = useMutation({
    mutationFn: (id: string) => deleteModelProvider(id),
    onSuccess: () => {
      toast.success("Subscription removed");
      setRemoving(null);
      invalidateProviders();
    },
    onError: (err) =>
      toast.error("Couldn't remove subscription", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  const assignM = useMutation({
    mutationFn: ({ credentialId, projectId }: { credentialId: string; projectId: string }) =>
      assignProviderToProject(credentialId, projectId),
    onSuccess: (_result, vars) => {
      const project = projects.find((p) => String(p.id) === vars.projectId);
      toast.success(project ? `Assigned to ${project.name}` : "Key assigned to project");
    },
    onError: (err) =>
      toast.error("Couldn't assign this key to that project", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  const label = providerLabel(providerKind);

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
              What this provider costs your {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}, which key
              serves each model, and which projects it&apos;s assigned to.
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

      {providersError ? (
        <ErrorState
          title="Couldn't load this provider"
          onRetry={() => unitProvidersQ.forEach((q) => void q.refetch())}
        />
      ) : providersLoading ? (
        <LoadingState variant="card" />
      ) : units.length === 0 ? (
        <div className="border-line-soft bg-surface-1 rounded-xl border border-dashed px-6 py-10 text-center">
          <p className="text-muted-foreground mx-auto max-w-md text-sm">
            You aren&apos;t bound to any {BUSINESS_UNIT_LABEL.toLowerCase()} yet, so there&apos;s
            nothing here to manage.
          </p>
        </div>
      ) : (
        <>
          <Card className="border-line-soft bg-panel-elevated">
            <CardHeader className="pb-3">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h2 className="font-display text-[14px] font-bold tracking-[-0.01em]">
                  Cost by model
                </h2>
                <span className="text-muted-foreground font-mono text-[10px] tracking-[0.12em] uppercase">
                  This month
                </span>
              </div>
              <p className="text-muted-foreground text-[12px]">
                {modelSpendRows.length} {label} {modelSpendRows.length === 1 ? "model" : "models"}{" "}
                your {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()} can use, ranked by what they cost
                this month.
              </p>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3">
                <Stat label="This month" value={spendQ.data ? usd(providerSpend) : "—"} />
                <Stat label="Models" value={String(modelSpendRows.length)} />
                <Stat label="Keys" value={String(credentials.length)} />
              </div>
              <SpendRankedBars
                rows={modelSpendRows}
                emptyLabel={`No ${label} spend this month.`}
              />
            </CardContent>
          </Card>

          <Card className="border-line-soft bg-panel-elevated">
            <CardHeader className="pb-2">
              <h2 className="font-display text-[14px] font-bold tracking-[-0.01em]">
                Models &amp; keys
              </h2>
              <p className="text-muted-foreground text-[12px]">
                Manage the key serving each model, and assign it to one of your projects.
              </p>
            </CardHeader>
            <CardContent className="space-y-3">
              {rows.length === 0 ? (
                <p className="text-muted-foreground text-[12.5px]">
                  {label} isn&apos;t keyed for your {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()} yet.
                  Add a key from the{" "}
                  <Link href="/admin/models" className="text-brand-bright underline underline-offset-2">
                    Models
                  </Link>{" "}
                  page.
                </p>
              ) : (
                <>
                  <div className="flex items-center justify-between gap-3">
                    <div className="border-line-soft bg-surface-1 flex max-w-sm flex-1 items-center gap-2 rounded-lg border px-2.5">
                      <Search className="text-muted-foreground size-3.5 shrink-0" aria-hidden />
                      <Input
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        placeholder={`Search ${rows.length} models or credentials…`}
                        aria-label="Search models or credentials"
                        className="h-9 border-0 bg-transparent px-0 text-[13px] shadow-none focus-visible:ring-0"
                      />
                      {query && (
                        <button
                          type="button"
                          onClick={() => setQuery("")}
                          className="text-muted-foreground hover:text-foreground shrink-0 font-mono text-[10.5px] transition-colors"
                        >
                          Clear
                        </button>
                      )}
                    </div>
                    {query.trim() && (
                      <span className="text-muted-foreground shrink-0 font-mono text-[11px]">
                        {visibleRows.length} of {rows.length}
                      </span>
                    )}
                  </div>

                  {visibleRows.length === 0 ? (
                    <p className="text-muted-foreground py-4 text-center text-[12.5px]">
                      No model or credential matches &ldquo;{query.trim()}&rdquo;.
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
                            <th className="text-muted-foreground px-2 py-2 text-right font-mono text-[10px] font-semibold tracking-wider uppercase">
                              Manage
                            </th>
                          </tr>
                        </thead>
                        <tbody className="divide-line-soft divide-y">
                          {visibleRows.map((row) => {
                            const c = row.credential;
                            // Own-unit key only: an org-wide credential's
                            // workspaceId is null, and this admin doesn't
                            // edit, delete or assign the Org Admin's key —
                            // see the component doc above.
                            const own = !!c.workspaceId;
                            return (
                              <tr key={`${row.model_id}::${c.id}`}>
                                <td className="py-2.5 pr-3">
                                  <div className="flex flex-wrap items-center gap-2">
                                    <span className="font-mono text-[12px]">{row.model_id}</span>
                                    {row.isDefault && (
                                      <span className="text-success bg-success/10 border-success/30 shrink-0 rounded-full border px-1.5 py-px font-mono text-[9px] font-semibold tracking-wide uppercase">
                                        Default
                                      </span>
                                    )}
                                  </div>
                                </td>

                                <td className="px-2 py-2.5 text-right font-mono text-[12px] tabular-nums">
                                  {spendQ.data ? usd(spendByModel.get(row.model_id) ?? 0) : "—"}
                                  {sharedModels.has(row.model_id) && (
                                    <span className="text-muted-foreground ml-1 block font-sans text-[10px] font-normal">
                                      across all keys
                                    </span>
                                  )}
                                </td>

                                <td className="px-2 py-2.5">
                                  <div className="space-y-0.5">
                                    <span className="block text-[11.5px]">{c.display_name}</span>
                                    <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                                      {c.hasKey === false && (
                                        <span className="text-warning inline-flex items-center gap-1 font-mono text-[9.5px] tracking-wide uppercase">
                                          <KeyRound className="size-2.5" aria-hidden />
                                          Holds no key
                                        </span>
                                      )}
                                      {c.hasKey && c.status !== "valid" && (
                                        <span className="text-muted-foreground font-mono text-[9.5px] tracking-wide uppercase">
                                          {c.status}
                                        </span>
                                      )}
                                      {own ? (
                                        <span className="text-muted-foreground font-mono text-[9.5px] tracking-wide uppercase">
                                          {unitNameById.get(c.workspaceId as string) ??
                                            "Unit-scoped"}{" "}
                                          only
                                        </span>
                                      ) : (
                                        <span className="text-muted-foreground inline-flex items-center gap-1 font-mono text-[9.5px] tracking-wide uppercase">
                                          <Globe className="size-2.5" aria-hidden />
                                          Centrally keyed
                                        </span>
                                      )}
                                    </span>
                                  </div>
                                </td>

                                <td className="py-2.5 pl-2">
                                  <div className="flex items-center justify-end gap-1">
                                    {own ? (
                                      <>
                                        <AssignToProjectButton
                                          workspaceId={c.workspaceId as string}
                                          projects={projects}
                                          projectsLoading={projectsQ.isLoading}
                                          pending={assignM.isPending}
                                          onAssign={(projectId) =>
                                            assignM.mutate({ credentialId: c.id, projectId })
                                          }
                                        />
                                        <Button
                                          variant="ghost"
                                          size="sm"
                                          className="text-muted-foreground hover:text-foreground size-7 p-0"
                                          onClick={() => setEditing(c)}
                                          aria-label={`Edit ${c.display_name} — key, endpoint and limits`}
                                          title={`Edit ${c.display_name}`}
                                        >
                                          <Pencil className="size-3.5" aria-hidden />
                                        </Button>
                                        <Button
                                          variant="ghost"
                                          size="sm"
                                          className="text-muted-foreground hover:text-destructive size-7 p-0"
                                          onClick={() => setRemoving(c)}
                                          aria-label={`Remove ${c.display_name}`}
                                          title={`Remove ${c.display_name}`}
                                        >
                                          <Trash2 className="size-3.5" aria-hidden />
                                        </Button>
                                      </>
                                    ) : (
                                      <span className="text-muted-foreground font-mono text-[10px] tracking-wide uppercase">
                                        Managed by your Org Admin
                                      </span>
                                    )}
                                  </div>
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}
                </>
              )}
            </CardContent>
          </Card>
        </>
      )}

      <EditProviderDialog
        provider={editing}
        catalog={catalogQ.data ?? []}
        onClose={() => setEditing(null)}
        onSaved={() => queryClient.invalidateQueries({ queryKey: ["model"] })}
      />

      <Dialog open={removing !== null} onOpenChange={(o) => !o && setRemoving(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Remove {removing?.display_name}?</DialogTitle>
            <DialogDescription>
              The key is deleted from the tenant&apos;s vault. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          {removing &&
            (() => {
              const orphaned = rows
                .filter((r) => r.credential.id === removing.id)
                .filter(
                  (r) =>
                    !rows.some(
                      (o) => o.model_id === r.model_id && o.credential.id !== removing.id,
                    ),
                )
                .map((r) => r.model_id);
              return orphaned.length === 0 ? (
                <p className="text-muted-foreground text-[13px]">
                  Every model this subscription serves is also served by another key here.
                </p>
              ) : (
                <div className="border-warning/40 bg-warning/5 rounded-lg border p-3">
                  <p className="text-[13px] font-medium">
                    {orphaned.length} {orphaned.length === 1 ? "model" : "models"} will have no
                    key behind {orphaned.length === 1 ? "it" : "them"}:
                  </p>
                  <p className="text-muted-foreground mt-1 font-mono text-[11.5px]">
                    {orphaned.join(", ")}
                  </p>
                </div>
              );
            })()}
          <DialogFooter>
            <Button variant="ghost" onClick={() => setRemoving(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={removeM.isPending}
              onClick={() => removing && removeM.mutate(removing.id)}
            >
              {removeM.isPending ? "Removing…" : "Remove subscription"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/**
 * "Assign to project" — a small Command+Popover picker matching
 * `UnitAccessPicker`'s own structure, narrowed to the ONE business unit this
 * credential belongs to. The backend's `assign_provider_to_project` rejects
 * any project outside that unit outright (a provider's `workspace_id` must
 * equal the project's), so listing anything wider would just be a picker full
 * of choices that 403 on selection.
 */
function AssignToProjectButton({
  workspaceId,
  projects,
  projectsLoading,
  pending,
  onAssign,
}: {
  workspaceId: string;
  projects: Project[];
  projectsLoading: boolean;
  pending: boolean;
  onAssign: (projectId: string) => void;
}) {
  const [open, setOpen] = React.useState(false);
  const scoped = React.useMemo(
    () => projects.filter((p) => p.workspaceId === workspaceId),
    [projects, workspaceId],
  );

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="text-muted-foreground hover:text-foreground size-7 p-0"
          aria-label="Assign this key to a project"
          title="Assign this key to one of your projects"
          disabled={pending}
        >
          <FolderPlus className="size-3.5" aria-hidden />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[min(20rem,90vw)] p-0" align="end">
        {projectsLoading ? (
          <p className="text-muted-foreground p-3 text-[12px]">Loading projects…</p>
        ) : scoped.length === 0 ? (
          <p className="text-muted-foreground p-3 text-[12px]">
            No projects in this {BUSINESS_UNIT_LABEL.toLowerCase()} yet.
          </p>
        ) : (
          <Command filter={substringFilter}>
            <CommandInput placeholder="Search projects…" />
            <CommandList className="max-h-[min(50vh,16rem)]">
              <CommandEmpty>No matching project.</CommandEmpty>
              {scoped.map((p) => {
                const id = String(p.id);
                return (
                  <CommandItem
                    key={id}
                    value={`${p.name} ${id}`}
                    onSelect={() => {
                      onAssign(id);
                      setOpen(false);
                    }}
                  >
                    <span className="truncate text-[12.5px]">{p.name}</span>
                  </CommandItem>
                );
              })}
            </CommandList>
          </Command>
        )}
      </PopoverContent>
    </Popover>
  );
}

// ───────── Shared bits ─────────

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="border-line-soft bg-surface-1 rounded-xl border px-3 py-2.5">
      <p className="text-muted-foreground font-mono text-[9.5px] tracking-[0.12em] uppercase">
        {label}
      </p>
      <p className="font-display mt-1 text-[20px] leading-none font-bold tabular-nums">{value}</p>
      {sub && <p className="text-muted-foreground mt-1 text-[11px]">{sub}</p>}
    </div>
  );
}
