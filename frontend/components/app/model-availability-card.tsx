"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Building2, CheckCircle2, ChevronRight, Globe, KeyRound } from "lucide-react";

import { cn } from "@/lib/utils";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { RequestAccessButton } from "@/components/requests/request-access-button";
import { getModelAvailability } from "@/lib/api/models";
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

  // Memoised because `ungranted` below depends on it: `q.data ?? []` produces
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
   */
  const requestType = audience === "bu" ? "model_provider_access" : "model_credential";

  const ungranted = React.useMemo(() => {
    const held = new Set(rows.map((r) => `${r.provider}::${r.model_id}`));
    // Deduped on the way out as well as filtered: a provider listing the same
    // model_id twice would otherwise produce two identical "request this" rows
    // for one model, which reads as two different models with one name.
    const seen = new Set<string>();
    return catalog.flatMap((p) =>
      p.models.flatMap((m) => {
        const key = `${p.provider}::${m.model_id}`;
        if (held.has(key) || seen.has(key)) return [];
        seen.add(key);
        return [{ provider: p.provider, providerLabel: p.label, ...m }];
      }),
    );
  }, [catalog, rows]);

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
              {ungranted.length > 0 && " — the catalogue below is what you could ask for"}.
            </p>
            {ungranted.length === 0 && (
              <RequestAccessButton
                label="Request model access"
                prefill={{
                  type: requestType,
                  title: "Model access",
                  description: `${workspaceName} holds no models. Which we need, and what for:`,
                  workspaceId,
                }}
              />
            )}
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
        {ungranted.length > 0 && (
          <details className="border-line-soft group mt-4 rounded-xl border border-dashed">
            <summary className="text-muted-foreground hover:text-foreground flex cursor-pointer items-center gap-2 px-3 py-2.5 font-mono text-[11.5px] transition-colors">
              <ChevronRight
                className="size-3.5 shrink-0 transition-transform group-open:rotate-90"
                aria-hidden
              />
              {ungranted.length} more {ungranted.length === 1 ? "model" : "models"} exist that{" "}
              {workspaceName} wasn&apos;t granted
            </summary>
            <ul className="divide-line-soft border-line-soft divide-y border-t">
              {ungranted.map((m) => (
                <li
                  key={`${m.provider}::${m.model_id}`}
                  className="flex flex-wrap items-center gap-x-3 gap-y-2 p-3 opacity-75 transition-opacity hover:opacity-100"
                >
                  <span className="min-w-0 flex-1 truncate font-mono text-[12px]">
                    {m.model_id}
                  </span>
                  <span className="text-muted-foreground shrink-0 font-mono text-[10.5px]">
                    {m.providerLabel}
                  </span>
                  <RequestAccessButton
                    prefill={{
                      type: requestType,
                      title: `${m.label} access`,
                      description:
                        audience === "bu"
                          ? `Requesting ${m.label} (${m.providerLabel}) for ${workspaceName}. It isn't granted to us today.`
                          : `Requesting ${m.label} (${m.providerLabel}) for our project. ${workspaceName} doesn't hold it today.`,
                      workspaceId,
                    }}
                  />
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
