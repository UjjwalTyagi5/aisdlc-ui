"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Boxes, Globe, Loader2, Plug } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import {
  listConnectorGrants,
  setBuConnectorGrants,
} from "@/lib/api/connectors";
import { getBuAllowedModels, getOrgModelGrants, setBuModelGrants } from "@/lib/api/models";
import { qk } from "@/lib/api/query-keys";
import { CONNECTOR_KIND_LABEL } from "@/lib/connectors";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";
import type { ConnectorGrant } from "@/lib/schemas/connector";
import type { ConnectorKind } from "@/lib/schemas/enums";
import type { ModelAllowEntry } from "@/lib/schemas/model";

const RISE = {
  animationName: "rise",
  animationDuration: "0.55s",
  animationTimingFunction: "cubic-bezier(0.2, 0.7, 0.2, 1)",
  animationFillMode: "both",
} as const;

const keyOf = (e: { provider: string; model_id: string }) => `${e.provider}::${e.model_id}`;

// ─── Shared shell ─────────────────────────────────────────────────────────────

function AccessSection({
  icon: Icon,
  title,
  blurb,
  action,
  children,
  delay,
}: {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  blurb: string;
  action?: React.ReactNode;
  children: React.ReactNode;
  delay: string;
}) {
  return (
    <section
      className="border-line-soft bg-panel-elevated rounded-2xl border px-6 py-5 shadow-[0_1px_0_oklch(1_0_0_/_0.04)_inset,0_8px_24px_-8px_oklch(0_0_0_/_0.28)]"
      style={{ ...RISE, animationDelay: delay }}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Icon className="text-brand-bright size-4" aria-hidden />
            <span className="font-display text-[15px] font-bold tracking-[-0.01em]">{title}</span>
          </div>
          <p className="text-muted-foreground mt-1.5 text-[12.5px]">{blurb}</p>
        </div>
        {action}
      </div>
      <div className="mt-4">{children}</div>
    </section>
  );
}

/** Models and connectors that reach every unit — listed, but never as choices:
 *  a checkbox you can't uncheck is worse than a plain statement of fact. */
function GlobalList({ items, empty }: { items: string[]; empty: string }) {
  if (items.length === 0) {
    return <p className="text-muted-foreground font-mono text-[11.5px]">{empty}</p>;
  }
  return (
    <ul className="flex flex-wrap gap-1.5">
      {items.map((label) => (
        <li
          key={label}
          className="border-line-soft bg-surface-1 text-muted-foreground inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10.5px]"
        >
          <Globe className="size-2.5" aria-hidden />
          {label}
        </li>
      ))}
    </ul>
  );
}

// ─── Models ───────────────────────────────────────────────────────────────────

/**
 * Which models this Business Unit gets.
 *
 * Only restricted (`specific`) models are decisions: a global model reaches
 * every unit by definition, so it is shown as already included rather than as
 * a box that would do nothing — or, worse, one that appeared to revoke the
 * model for the whole organization.
 *
 * Read-only unless the viewer is the Organization Admin. A Business Unit Admin
 * looking at their own unit needs to see what they were given (and ask for
 * more); letting them tick the boxes would make the grant theirs.
 */
export function BuModelAccessCard({
  workspaceId,
  workspaceName,
  canManage,
}: {
  workspaceId: string;
  workspaceName: string;
  canManage: boolean;
}) {
  const queryClient = useQueryClient();

  // The full policy is an org-wide read (it names other units), so only the
  // Org Admin fetches it. Everyone else reads the derived list for this unit.
  const grantsQ = useQuery({
    queryKey: qk.model.orgGrants(),
    queryFn: getOrgModelGrants,
    enabled: canManage,
  });
  const allowedQ = useQuery({
    queryKey: qk.model.buAllowed(workspaceId),
    queryFn: () => getBuAllowedModels(workspaceId),
  });

  const grants = React.useMemo(() => grantsQ.data ?? [], [grantsQ.data]);
  const restricted = React.useMemo(
    () => grants.filter((g) => g.visibility === "specific"),
    [grants],
  );
  const globals = React.useMemo(
    () => grants.filter((g) => g.visibility === "global").map((g) => g.model_id),
    [grants],
  );

  const serverSelected = React.useMemo(
    () =>
      new Set(
        restricted.filter((g) => g.businessUnitIds.includes(workspaceId)).map((g) => keyOf(g)),
      ),
    [restricted, workspaceId],
  );

  const [draft, setDraft] = React.useState<Set<string> | null>(null);
  const signature = React.useMemo(() => [...serverSelected].sort().join("|"), [serverSelected]);
  React.useEffect(() => setDraft(null), [signature]);

  const selected = draft ?? serverSelected;

  const saveM = useMutation({
    mutationFn: (entries: ModelAllowEntry[]) => setBuModelGrants(workspaceId, entries),
    onSuccess: () => {
      toast.success(`Model access updated for ${workspaceName}`);
      queryClient.invalidateQueries({ queryKey: ["model"] });
    },
    onError: (err) =>
      toast.error("Couldn't update model access", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  const save = () =>
    saveM.mutate(
      restricted
        .filter((g) => selected.has(keyOf(g)))
        .map((g) => ({ provider: g.provider, model_id: g.model_id })),
    );

  const dirty = draft !== null;

  // Read-only view: the derived list, with no hint that it was ever editable.
  if (!canManage) {
    const allowed = allowedQ.data ?? [];
    return (
      <AccessSection
        icon={Boxes}
        title="Models"
        blurb={`Granted to this ${BUSINESS_UNIT_LABEL.toLowerCase()} by the Organization Admin. Ask them to grant more.`}
        delay="0.05s"
      >
        {allowedQ.isLoading ? (
          <p className="text-muted-foreground text-[12.5px]">Loading…</p>
        ) : allowed.length === 0 ? (
          <p className="text-muted-foreground text-[12.5px]">No models granted yet.</p>
        ) : (
          <ul className="flex flex-wrap gap-1.5">
            {/* Deduped: `allowed` is one row per GRANT, and a model granted
                both org-wide and to this unit appears twice. As a flat list of
                model names those two rows are the same chip printed twice —
                and they collided on `keyOf`, which is provider::model_id. */}
            {[...new Map(allowed.map((e) => [keyOf(e), e] as const)).values()].map((e) => (
              <li
                key={keyOf(e)}
                className="border-line-soft bg-surface-1 text-muted-foreground rounded-full border px-2 py-0.5 font-mono text-[10.5px]"
              >
                {e.model_id}
              </li>
            ))}
          </ul>
        )}
      </AccessSection>
    );
  }

  return (
    <AccessSection
      icon={Boxes}
      title="Models"
      blurb={`Which restricted models ${workspaceName} may use. Global models are already included everywhere and can't be revoked from a single ${BUSINESS_UNIT_LABEL.toLowerCase()}.`}
      delay="0.05s"
      action={
        <div className="flex shrink-0 gap-2">
          {dirty && (
            <Button variant="ghost" size="sm" onClick={() => setDraft(null)} disabled={saveM.isPending}>
              Cancel
            </Button>
          )}
          <Button
            size="sm"
            className="h-7 font-mono text-[11px]"
            onClick={save}
            disabled={!dirty || saveM.isPending}
            aria-busy={saveM.isPending}
          >
            {saveM.isPending ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : null}
            {saveM.isPending ? "Saving…" : "Save"}
          </Button>
        </div>
      }
    >
      {grantsQ.isLoading ? (
        <p className="text-muted-foreground text-[12.5px]">Loading…</p>
      ) : (
        <div className="space-y-4">
          <div className="space-y-1.5">
            <p className="text-muted-foreground font-mono text-[10px] tracking-[0.12em] uppercase">
              Included everywhere
            </p>
            <GlobalList items={globals} empty="No models are global right now." />
          </div>

          <div className="space-y-1.5">
            <p className="text-muted-foreground font-mono text-[10px] tracking-[0.12em] uppercase">
              Restricted — grant individually
            </p>
            {restricted.length === 0 ? (
              <p className="text-muted-foreground text-[12.5px]">
                No restricted models. Mark one Specific on the Models page to grant it per{" "}
                {BUSINESS_UNIT_LABEL.toLowerCase()}.
              </p>
            ) : (
              <ul className="divide-line-soft border-line-soft divide-y rounded-xl border">
                {restricted.map((g) => {
                  const k = keyOf(g);
                  const id = `bu-model-${workspaceId}-${g.provider}-${g.model_id}`;
                  const on = selected.has(k);
                  return (
                    <li key={k} className="flex items-center gap-2.5 p-2.5">
                      <Checkbox
                        id={id}
                        checked={on}
                        disabled={saveM.isPending}
                        onCheckedChange={() => {
                          const next = new Set(selected);
                          if (on) next.delete(k);
                          else next.add(k);
                          setDraft(next);
                        }}
                      />
                      <Label
                        htmlFor={id}
                        className={cn(
                          "min-w-0 flex-1 cursor-pointer truncate font-mono text-[12px] font-normal",
                          !on && "text-muted-foreground",
                        )}
                      >
                        {g.model_id}
                      </Label>
                      <span className="text-muted-foreground shrink-0 font-mono text-[10.5px]">
                        {g.provider}
                      </span>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </div>
      )}
    </AccessSection>
  );
}

// ─── Connectors ───────────────────────────────────────────────────────────────

/** The connector twin of `BuModelAccessCard` — same rules, same shape. */
export function BuConnectorAccessCard({
  workspaceId,
  workspaceName,
  canManage,
}: {
  workspaceId: string;
  workspaceName: string;
  canManage: boolean;
}) {
  const queryClient = useQueryClient();

  const policyQ = useQuery({
    queryKey: qk.connectors.grants(null),
    queryFn: () => listConnectorGrants(),
    enabled: canManage,
  });
  const unitQ = useQuery({
    queryKey: qk.connectors.grants(workspaceId),
    queryFn: () => listConnectorGrants(workspaceId),
  });

  const policy: ConnectorGrant[] = React.useMemo(() => policyQ.data ?? [], [policyQ.data]);
  // Every kind is per-unit now — connectors have no "global" tier, so there is
  // no always-on list to render beside the selectable one.
  const restricted = policy;
  const globals: string[] = React.useMemo(() => [], []);

  const serverSelected = React.useMemo(
    () =>
      new Set(
        restricted.filter((g) => g.businessUnitIds.includes(workspaceId)).map((g) => g.kind),
      ),
    [restricted, workspaceId],
  );

  const [draft, setDraft] = React.useState<Set<string> | null>(null);
  const signature = React.useMemo(() => [...serverSelected].sort().join("|"), [serverSelected]);
  React.useEffect(() => setDraft(null), [signature]);

  const selected = draft ?? serverSelected;

  const saveM = useMutation({
    mutationFn: (kinds: ConnectorKind[]) => setBuConnectorGrants(workspaceId, kinds),
    onSuccess: () => {
      toast.success(`Connector access updated for ${workspaceName}`);
      queryClient.invalidateQueries({ queryKey: ["connectors"] });
    },
    onError: (err) =>
      toast.error("Couldn't update connector access", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  const save = () =>
    saveM.mutate(restricted.filter((g) => selected.has(g.kind)).map((g) => g.kind));

  const dirty = draft !== null;

  if (!canManage) {
    const granted = unitQ.data ?? [];
    return (
      <AccessSection
        icon={Plug}
        title="Connectors"
        blurb={`Permitted for this ${BUSINESS_UNIT_LABEL.toLowerCase()} by the Organization Admin. Ask them to permit more.`}
        delay="0.06s"
      >
        {unitQ.isLoading ? (
          <p className="text-muted-foreground text-[12.5px]">Loading…</p>
        ) : granted.length === 0 ? (
          <p className="text-muted-foreground text-[12.5px]">No connectors permitted yet.</p>
        ) : (
          <ul className="flex flex-wrap gap-1.5">
            {granted.map((g) => (
              <li
                key={g.kind}
                className="border-line-soft bg-surface-1 text-muted-foreground rounded-full border px-2 py-0.5 font-mono text-[10.5px]"
              >
                {CONNECTOR_KIND_LABEL[g.kind]}
              </li>
            ))}
          </ul>
        )}
      </AccessSection>
    );
  }

  return (
    <AccessSection
      icon={Plug}
      title="Connectors"
      blurb={`Which restricted integrations ${workspaceName} may connect and use. Global connectors are already permitted everywhere.`}
      delay="0.06s"
      action={
        <div className="flex shrink-0 gap-2">
          {dirty && (
            <Button variant="ghost" size="sm" onClick={() => setDraft(null)} disabled={saveM.isPending}>
              Cancel
            </Button>
          )}
          <Button
            size="sm"
            className="h-7 font-mono text-[11px]"
            onClick={save}
            disabled={!dirty || saveM.isPending}
            aria-busy={saveM.isPending}
          >
            {saveM.isPending ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : null}
            {saveM.isPending ? "Saving…" : "Save"}
          </Button>
        </div>
      }
    >
      {policyQ.isLoading ? (
        <p className="text-muted-foreground text-[12.5px]">Loading…</p>
      ) : (
        <div className="space-y-4">
          <div className="space-y-1.5">
            <p className="text-muted-foreground font-mono text-[10px] tracking-[0.12em] uppercase">
              Permitted everywhere
            </p>
            <GlobalList items={globals} empty="No connectors are global right now." />
          </div>

          <div className="space-y-1.5">
            <p className="text-muted-foreground font-mono text-[10px] tracking-[0.12em] uppercase">
              Restricted — permit individually
            </p>
            {restricted.length === 0 ? (
              <p className="text-muted-foreground text-[12.5px]">
                No restricted connectors. Mark one Specific on the Integrations page to permit it
                per {BUSINESS_UNIT_LABEL.toLowerCase()}.
              </p>
            ) : (
              <ul className="divide-line-soft border-line-soft divide-y rounded-xl border">
                {restricted.map((g) => {
                  const id = `bu-connector-${workspaceId}-${g.kind}`;
                  const on = selected.has(g.kind);
                  return (
                    <li key={g.kind} className="flex items-center gap-2.5 p-2.5">
                      <Checkbox
                        id={id}
                        checked={on}
                        disabled={saveM.isPending}
                        onCheckedChange={() => {
                          const next = new Set(selected);
                          if (on) next.delete(g.kind);
                          else next.add(g.kind);
                          setDraft(next);
                        }}
                      />
                      <Label
                        htmlFor={id}
                        className={cn(
                          "min-w-0 flex-1 cursor-pointer truncate text-[13px] font-normal",
                          !on && "text-muted-foreground",
                        )}
                      >
                        {CONNECTOR_KIND_LABEL[g.kind]}
                      </Label>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </div>
      )}
    </AccessSection>
  );
}
