"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Building2, CheckCircle2, ChevronRight, Globe, KeyRound } from "lucide-react";

import { cn } from "@/lib/utils";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { RequestAccessButton } from "@/components/requests/request-access-button";
import { getGrantedProviders, getModelAvailability } from "@/lib/api/models";
import { qk } from "@/lib/api/query-keys";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";
import type { CatalogProvider, ModelAvailability } from "@/lib/schemas/model";

/**
 * What this Business Unit has been given, and what (if anything) it still has
 * to do about it.
 *
 * Read-only. A Business Unit Admin used to curate their own subset here,
 * which meant the answer to "what can we use?" was partly their own doing —
 * so a model missing from a project could be the Org Admin's decision or
 * their own, and the page couldn't tell you which. Now the list is
 * consequence, not choice. Supplying a key where the organization didn't is
 * genuinely theirs to do, but that action lives at the page's single "Add
 * key" button (spec §5, Task 10) rather than per row here — this card's job
 * is answering "what's granted and what still needs a key," not launching
 * the flow that fixes it.
 */
export function ModelAvailabilityCard({
  workspaceId,
  workspaceName,
  /** Copy differs for a Project Admin: the credentials they add are theirs,
   *  and go to their {BUSINESS_UNIT_LABEL} Admin for approval first. */
  audience,
  catalog = [],
}: {
  workspaceId: string;
  workspaceName: string;
  audience: "bu" | "project";
  /**
   * The full model catalogue. Everything in here that this unit was NOT
   * granted is listed below the granted set, dimmed and requestable — the
   * page previously showed only what you hold, which left "we were never
   * granted Opus" and "Opus isn't on this platform" looking identical.
   *
   * This leaks nothing: the catalogue endpoint already answers in full to
   * every role. What is scoped is the GRANT, and that is still exactly the
   * `rows` below.
   */
  catalog?: CatalogProvider[];
}) {
  const q = useQuery({
    queryKey: qk.model.availability(workspaceId),
    queryFn: () => getModelAvailability(workspaceId),
    enabled: !!workspaceId,
  });

  // Providers this unit HOLDS — including any granted with nothing curated under it
  // yet, which `availability` (a per-model answer) structurally cannot report.
  const grantedQ = useQuery({
    queryKey: qk.model.grantedProviders(workspaceId),
    queryFn: () => getGrantedProviders(workspaceId),
    enabled: !!workspaceId,
  });

  // Memoised because `ungrantedProviders` below depends on it: `q.data ?? []` produces
  // a fresh array on every render when the query has no data, which would make
  // that memo recompute the whole catalogue diff each time.
  const rows = React.useMemo(() => q.data ?? [], [q.data]);
  const needsKey = rows.filter((r) => !r.centrallyCredentialed && !r.locallyCredentialed);

  /**
   * A Business Unit Admin and a Project Admin lack different things, so they
   * ask for different things (see lib/requests/routing.ts).
   *
   *   bu       → `model_provider_access`, decided by the Org Admin. What they
   *              lack is an org-wide grant; a project-scoped credential
   *              request would be answered by themselves.
   *   project  → `model_credential`, decided by the BU Admin above them.
   *
   * That `project` case only ever applies when this card is rendered with
   * genuine per-project state (`projects/[id]/models/page.tsx`). On
   * `admin/models` there is no per-project state for any role — its `scope`
   * derivation is entirely business-unit-based (`useScopedBusinessUnits`) —
   * so a request raised from here can never carry the `project_id`
   * `_apply_model_credential` requires to actually apply. Below, the
   * `audience === "project"` branch is therefore redirected to
   * `projects/[id]/models/page.tsx`, the correct, already-working,
   * already-project-scoped entry point for this exact ask, instead of
   * rendering a button that would raise a request nobody can ever approve.
   *
   * Both `RequestAccessButton` sites below sit inside `audience !== "project"`
   * branches, so `audience` is always `"bu"` wherever `requestType` is
   * actually read — the `model_credential` arm the ternary used to carry is
   * unreachable now, kept only as a record of the tier mapping.
   */
  const requestType = "model_provider_access";

  /**
   * PROVIDERS you could ask for, not models.
   *
   * This listed every ungranted MODEL — ~2,700 rows, each with its own "Request
   * access" button, one governance request per model. That is not the grant an
   * Organization Admin makes: they grant a PROVIDER to the unit and then curate which
   * of its models the unit gets. So the long list asked for something the approval
   * screen could not give, one row at a time.
   *
   * A provider is worth asking for when NOTHING from it currently reaches this unit.
   * Once anything does, the provider is already granted and "we want more of its
   * models" is a curation question for the Organization Admin, not another request.
   */
  const granted = React.useMemo(() => grantedQ.data ?? [], [grantedQ.data]);

  /**
   * GRANTED, BUT WITH NOTHING TO USE YET. The provider reached this unit — usually
   * because a request was approved — and the Organization Admin has not yet chosen
   * which of its models the unit gets. Naming that state is the difference between
   * "my request did nothing" and "my request landed, one step left, and it isn't mine".
   */
  const awaitingCuration = React.useMemo(() => {
    const labels = new Map(catalog.map((p) => [p.provider, p.label]));
    return granted
      .filter((g) => g.curatedCount === 0)
      .map((g) => ({ provider: g.provider, label: labels.get(g.provider) ?? g.provider }));
  }, [granted, catalog]);

  const ungrantedProviders = React.useMemo(() => {
    // Held, NOT "has models we can see". Keying this off the model list offered the
    // unit a request for the provider it had just been granted, because a provider
    // with nothing curated yet contributes no model rows.
    const held = new Set(granted.map((g) => g.provider));
    const seen = new Set<string>();
    return catalog.flatMap((p) => {
      if (held.has(p.provider) || seen.has(p.provider)) return [];
      seen.add(p.provider);
      return [{ provider: p.provider, label: p.label, modelCount: p.models.length }];
    });
  }, [catalog, granted]);

  return (
    <Card className="border-line-soft bg-panel-elevated">
      <CardHeader className="pb-3">
        <div className="flex items-start gap-3">
          <div
            aria-hidden
            className="border-line-soft bg-surface-2 text-muted-foreground grid size-9 shrink-0 place-items-center rounded-lg border"
          >
            <Building2 className="size-4" aria-hidden />
          </div>
          <div className="min-w-0 flex-1">
            <h3 className="font-display text-[14px] font-bold tracking-[-0.01em]">
              Models available to {workspaceName}
            </h3>
            <p className="text-muted-foreground mt-0.5 text-[12px]">
              Granted by your Organization Admin. You can&apos;t add to this list — ask them to
              grant more.
              {needsKey.length > 0 && (
                <>
                  {" "}
                  {needsKey.length} of them{" "}
                  {needsKey.length === 1 ? "needs" : "need"} credentials before{" "}
                  {needsKey.length === 1 ? "it" : "they"} can run.
                </>
              )}
            </p>
          </div>
        </div>
      </CardHeader>

      <CardContent className="pt-0">
        {q.isLoading ? (
          <p className="text-muted-foreground text-[12.5px]">Loading…</p>
        ) : q.isError ? (
          <p className="text-muted-foreground text-[12.5px]">
            Couldn&apos;t load the model list.{" "}
            <button
              onClick={() => q.refetch()}
              className="text-brand-bright underline underline-offset-2"
            >
              Retry
            </button>
          </p>
        ) : rows.length === 0 ? (
          <div className="space-y-3">
            <p className="text-muted-foreground text-[12.5px]">
              Your Organization Admin hasn&apos;t granted this {BUSINESS_UNIT_LABEL.toLowerCase()}{" "}
              any models yet. Nothing can be onboarded or run until they do
              {ungrantedProviders.length > 0 && " — the providers below are what you could ask for"}.
            </p>
            {ungrantedProviders.length === 0 &&
              (audience === "project" ? (
                <Link
                  href="/projects"
                  className="text-brand-bright text-[11.5px] underline underline-offset-2"
                >
                  Open your project&apos;s own Models page to request this
                </Link>
              ) : (
                <RequestAccessButton
                  label="Request model access"
                  prefill={{
                    type: requestType,
                    title: "Model access",
                    description: `${workspaceName} holds no models. Which we need, and what for:`,
                    workspaceId,
                  }}
                />
              ))}
          </div>
        ) : (
          <ul className="divide-line-soft border-line-soft divide-y rounded-xl border">
            {/* Index in the key, not just provider::model_id. The SAME model
                can reach a unit twice — once org-wide and once as a grant to
                this unit specifically, each its own row with its own badge —
                and keying on the pair alone made React treat them as one
                (duplicate-key warning, and the second row liable to be
                dropped). */}
            {rows.map((r, i) => (
              <AvailabilityRow
                key={`${r.provider}::${r.model_id}::${i}`}
                row={r}
                audience={audience}
              />
            ))}
          </ul>
        )}

        {rows.length > 0 && (
          <p className="text-muted-foreground mt-3 text-[11.5px]">
            {audience === "bu"
              ? "Models keyed centrally need nothing from you. For the rest, use the “Add key” button above."
              : `Models keyed centrally need nothing from you. For the rest, ask your ${BUSINESS_UNIT_LABEL} Admin to assign this project a key.`}
          </p>
        )}

        {/* Everything the platform offers that this scope wasn't given. Kept
            behind a disclosure rather than inline: it is the longer list and
            the less important one, and expanding the answer to "what can we
            use" with forty things you can't would bury it. */}
        {/* Granted, awaiting curation — above the "could ask for" list on purpose:
            this is a provider the unit HAS, and the reason its models are missing is
            an outstanding step by someone else, not a request still to raise. */}
        {awaitingCuration.length > 0 && (
          <ul className="border-line-soft divide-line-soft mt-4 divide-y rounded-xl border">
            {awaitingCuration.map((p) => (
              <li key={p.provider} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 p-3">
                <span className="min-w-0 flex-1 truncate font-mono text-[12px]">{p.label}</span>
                <Pill icon={Building2} tone="neutral" label="Granted to this business unit" />
                <span className="text-muted-foreground shrink-0 text-[11px]">
                  No models chosen yet — your Organization Admin picks which ones this{" "}
                  {BUSINESS_UNIT_LABEL.toLowerCase()} gets.
                </span>
              </li>
            ))}
          </ul>
        )}

        {ungrantedProviders.length > 0 && (
          <details className="border-line-soft group mt-4 rounded-xl border border-dashed">
            <summary className="text-muted-foreground hover:text-foreground flex cursor-pointer items-center gap-2 px-3 py-2.5 font-mono text-[11.5px] transition-colors">
              <ChevronRight
                className="size-3.5 shrink-0 transition-transform group-open:rotate-90"
                aria-hidden
              />
              {ungrantedProviders.length}{" "}
              {ungrantedProviders.length === 1 ? "provider" : "providers"} exist that{" "}
              {workspaceName} wasn&apos;t granted
            </summary>
            <ul className="divide-line-soft border-line-soft divide-y border-t">
              {ungrantedProviders.map((p) => (
                <li
                  key={p.provider}
                  className="flex flex-wrap items-center gap-x-3 gap-y-2 p-3 opacity-75 transition-opacity hover:opacity-100"
                >
                  <span className="min-w-0 flex-1 truncate font-mono text-[12px]">{p.label}</span>
                  <span className="text-muted-foreground shrink-0 font-mono text-[10.5px]">
                    {p.modelCount} {p.modelCount === 1 ? "model" : "models"}
                  </span>
                  {audience === "project" ? (
                    <Link
                      href="/projects"
                      className="text-brand-bright shrink-0 text-[11px] underline underline-offset-2"
                    >
                      Request from your project&apos;s Models page
                    </Link>
                  ) : (
                    <RequestAccessButton
                      prefill={{
                        type: requestType,
                        title: `${p.label} access`,
                        description: `Requesting the ${p.label} provider for ${workspaceName}. It isn't granted to us today.`,
                        workspaceId,
                        // Provider only, no modelId: this asks for the grant an
                        // Organization Admin actually makes. Which of the provider's
                        // models the unit then gets is theirs to curate afterwards.
                        providerModel: { provider: p.provider },
                      }}
                    />
                  )}
                </li>
              ))}
            </ul>
          </details>
        )}
      </CardContent>
    </Card>
  );
}

function AvailabilityRow({
  row,
  audience,
}: {
  row: ModelAvailability;
  audience: "bu" | "project";
}) {
  const covered = row.centrallyCredentialed || row.locallyCredentialed;
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1.5 p-3">
      <span className="min-w-0 flex-1 truncate font-mono text-[12px]">{row.model_id}</span>
      <span className="text-muted-foreground shrink-0 font-mono text-[10.5px]">{row.provider}</span>

      <Pill
        icon={row.visibility === "global" ? Globe : Building2}
        tone="neutral"
        label={row.visibility === "global" ? "Global" : `Granted to this ${BUSINESS_UNIT_LABEL.toLowerCase()}`}
      />

      {row.centrallyCredentialed ? (
        <Pill icon={CheckCircle2} tone="success" label="Keyed centrally — no setup needed" />
      ) : row.locallyCredentialed ? (
        <Pill icon={CheckCircle2} tone="success" label="Keyed here" />
      ) : (
        <Pill
          icon={KeyRound}
          tone="warning"
          label={audience === "bu" ? "Needs your credentials" : "Needs credentials"}
        />
      )}
      {!covered && <span className="sr-only">This model cannot run until a key is added.</span>}
    </li>
  );
}

function Pill({
  icon: Icon,
  tone,
  label,
}: {
  icon: React.ComponentType<{ className?: string }>;
  tone: "neutral" | "success" | "warning";
  label: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10px] font-semibold",
        tone === "success" && "text-success bg-success/10 border-success/30",
        tone === "warning" && "text-warning bg-warning/10 border-warning/40",
        tone === "neutral" && "text-muted-foreground bg-surface-1 border-line-soft",
      )}
    >
      <Icon className="size-2.5" aria-hidden />
      {label}
    </span>
  );
}
