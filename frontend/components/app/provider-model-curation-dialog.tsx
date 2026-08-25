"use client";

import * as React from "react";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { getOrgModelGrants, setOrgModelGrants } from "@/lib/api/models";
import { qk } from "@/lib/api/query-keys";
import { providerLabel } from "@/lib/models/provider-labels";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";
import type { CatalogProvider, OrgModelGrant } from "@/lib/schemas/model";

/**
 * Add/remove ONE business unit's per-model curation for a provider, from the
 * full `OrgModelGrant` array `getOrgModelGrants()` returns.
 *
 * Mirrors `setOrgModelGrants`'s replace-whole-set contract (same as
 * `setBuModelGrants`, documented in `frontend/lib/api/models.ts`): a caller
 * must send back the ENTIRE array this returns, never a delta.
 *
 * A globally-granted model (`visibility: "global"`) is not editable here — the
 * dialog disables its checkbox before this can ever be called for it, and this
 * is a defensive no-op if it somehow is anyway. It must never invent a
 * `specific` override that narrows a global grant; that reads as "this BU is
 * exempt from something everyone else has", which is not a thing this control
 * offers.
 *
 * There is no credential picker in this dialog, so if more than one pre-
 * existing `specific` entry exists for the same (provider, model) pair —
 * distinguishable only by `credentialId`, one per subscription — they are
 * collapsed into a single entry carrying the union of every unit named on any
 * of them. This dialog has no way to say "toggle this BU on for THIS
 * subscription's grant specifically, not that one."
 */
export function toggleModelForUnit(
  grants: OrgModelGrant[],
  provider: string,
  modelId: string,
  workspaceId: string,
): OrgModelGrant[] {
  const matches = (g: OrgModelGrant) => g.provider === provider && g.model_id === modelId;
  const existing = grants.filter(matches);
  const others = grants.filter((g) => !matches(g));

  if (existing.some((g) => g.visibility === "global")) {
    return grants;
  }

  const units = new Set(existing.flatMap((g) => g.businessUnitIds));
  if (units.has(workspaceId)) {
    units.delete(workspaceId);
  } else {
    units.add(workspaceId);
  }

  // No units left on a specific grant reads identically to "granted to
  // nobody" — remove the row rather than keep an empty one lingering.
  if (units.size === 0) {
    return others;
  }

  const base = existing[0];
  const merged: OrgModelGrant = {
    provider,
    model_id: modelId,
    credentialId: base?.credentialId ?? null,
    visibility: "specific",
    businessUnitIds: [...units],
  };
  return [...others, merged];
}

/** True when a set of grants for one (provider, model) reaches this unit —
 *  globally, or by name. Mirrors `toggleModelForUnit`'s own matching rule. */
function isGrantedTo(entries: OrgModelGrant[], workspaceId: string): boolean {
  return entries.some(
    (g) => g.visibility === "global" || g.businessUnitIds.includes(workspaceId),
  );
}

/**
 * Which specific models of a provider a granted Business Unit may use.
 *
 * The provider-level grant (`UnitAccessPicker` on the card) decides whether a
 * BU may connect to a provider AT ALL. This decides which of that provider's
 * models actually reach the BU's catalogue — the restored per-model curation
 * from before this redesign, via the pre-existing, unmodified
 * `org_model_grants` table. No key/credential field appears anywhere here;
 * that action was removed from the Org Admin's flow entirely.
 */
export function ProviderModelCurationDialog({
  open,
  onOpenChange,
  kind,
  grantedUnitIds,
  units,
  catalog,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Provider slug, e.g. "anthropic". */
  kind: string;
  /** Business units currently granted this PROVIDER (Step 3/4's data) — the
   *  universe of BUs this dialog can curate models for. */
  grantedUnitIds: string[];
  /** Every workspace, for resolving a granted id to a display name. */
  units: { id: string; name: string }[];
  catalog: CatalogProvider[];
}) {
  const queryClient = useQueryClient();

  // Fetched once for the whole dialog rather than per-BU: it's one org-wide
  // read regardless of how many units are granted this provider.
  const grantsQ = useQuery({
    queryKey: qk.model.orgGrants(),
    queryFn: getOrgModelGrants,
    enabled: open,
  });

  const saveM = useMutation({
    mutationFn: setOrgModelGrants,
  });

  const models = catalog.find((c) => c.provider === kind)?.models ?? [];
  const grantedUnits = grantedUnitIds
    .map((id) => units.find((u) => u.id === id))
    .filter((u): u is { id: string; name: string } => !!u);

  const [pendingKey, setPendingKey] = React.useState<string | null>(null);

  const handleToggle = async (workspaceId: string, modelId: string) => {
    const key = `${workspaceId}::${modelId}`;
    if (pendingKey) return;
    const current = grantsQ.data ?? [];
    const next = toggleModelForUnit(current, kind, modelId, workspaceId);
    setPendingKey(key);
    try {
      // Cache the server's own echo, not `next` — the route may normalize
      // something (e.g. a default credentialId) that a locally-computed
      // array wouldn't reflect.
      const saved = await saveM.mutateAsync(next);
      queryClient.setQueryData(qk.model.orgGrants(), saved);
      queryClient.invalidateQueries({ queryKey: qk.model.buAllowed(workspaceId) });
    } catch (err) {
      toast.error("Couldn't update model access", {
        description: err instanceof Error ? err.message : undefined,
      });
    } finally {
      setPendingKey(null);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-lg overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="font-display">Models — {providerLabel(kind)}</DialogTitle>
          <DialogDescription>
            Choose which of this provider&apos;s models each granted{" "}
            {BUSINESS_UNIT_LABEL.toLowerCase()} may use. A unit not granted the provider at all
            isn&apos;t listed here — grant it first.
          </DialogDescription>
        </DialogHeader>

        {grantedUnits.length === 0 ? (
          <p className="text-muted-foreground text-[12.5px]">
            Grant this provider to a {BUSINESS_UNIT_LABEL.toLowerCase()} first.
          </p>
        ) : grantsQ.isLoading ? (
          <div className="text-muted-foreground flex items-center gap-2 py-6 text-[12.5px]">
            <Loader2 className="size-4 animate-spin" aria-hidden />
            Loading current grants…
          </div>
        ) : grantsQ.isError ? (
          <p className="text-warning text-[12.5px]">Couldn&apos;t load current model grants.</p>
        ) : models.length === 0 ? (
          <p className="text-muted-foreground text-[12.5px]">
            The catalogue lists no models for this provider yet.
          </p>
        ) : (
          <div className="space-y-4">
            {grantedUnits.map((unit) => (
              <section key={unit.id} className="space-y-1.5">
                <h4 className="text-[12.5px] font-semibold">{unit.name}</h4>
                <ul className="divide-line-soft border-line-soft divide-y rounded-xl border">
                  {models.map((m) => {
                    const entries = (grantsQ.data ?? []).filter(
                      (g) => g.provider === kind && g.model_id === m.model_id,
                    );
                    const isGlobal = entries.some((g) => g.visibility === "global");
                    const checked = isGrantedTo(entries, unit.id);
                    const checkboxId = `curate-${kind}-${unit.id}-${m.model_id}`;
                    const key = `${unit.id}::${m.model_id}`;
                    return (
                      <li key={m.model_id} className="flex items-center gap-3 p-2.5">
                        <Checkbox
                          id={checkboxId}
                          checked={checked}
                          disabled={isGlobal || pendingKey === key}
                          onCheckedChange={() => handleToggle(unit.id, m.model_id)}
                        />
                        <Label
                          htmlFor={checkboxId}
                          className={
                            "flex min-w-0 flex-1 items-center justify-between gap-2 font-normal " +
                            (isGlobal ? "cursor-default" : "cursor-pointer")
                          }
                        >
                          <span className="truncate font-mono text-[12px]">{m.model_id}</span>
                          {isGlobal && (
                            <span className="text-muted-foreground shrink-0 font-mono text-[9.5px] tracking-wide uppercase">
                              Granted globally
                            </span>
                          )}
                        </Label>
                      </li>
                    );
                  })}
                </ul>
              </section>
            ))}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
