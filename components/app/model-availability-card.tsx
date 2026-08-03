"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Building2, CheckCircle2, Globe, KeyRound } from "lucide-react";

import { cn } from "@/lib/utils";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { getModelAvailability } from "@/lib/api/models";
import { qk } from "@/lib/api/query-keys";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";
import type { ModelAvailability } from "@/lib/schemas/model";

/**
 * What this Business Unit has been given, and what (if anything) it still has
 * to do about it.
 *
 * Read-only on purpose. A Business Unit Admin used to curate their own subset
 * here, which meant the answer to "what can we use?" was partly their own
 * doing — so a model missing from a project could be the Org Admin's decision
 * or their own, and the page couldn't tell you which. Now the list is
 * consequence, not choice, and the only action left on it is the one that is
 * genuinely theirs: supplying a key where the organization didn't.
 */
export function ModelAvailabilityCard({
  workspaceId,
  workspaceName,
  /** Copy differs for a Project Admin: the credentials they add are theirs,
   *  and go to their {BUSINESS_UNIT_LABEL} Admin for approval first. */
  audience,
}: {
  workspaceId: string;
  workspaceName: string;
  audience: "bu" | "project";
}) {
  const q = useQuery({
    queryKey: qk.model.availability(workspaceId),
    queryFn: () => getModelAvailability(workspaceId),
    enabled: !!workspaceId,
  });

  const rows = q.data ?? [];
  const needsKey = rows.filter((r) => !r.centrallyCredentialed && !r.locallyCredentialed);

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
          <p className="text-muted-foreground text-[12.5px]">
            Your Organization Admin hasn&apos;t granted this {BUSINESS_UNIT_LABEL.toLowerCase()} any
            models yet. Nothing can be onboarded or run until they do.
          </p>
        ) : (
          <ul className="divide-line-soft border-line-soft divide-y rounded-xl border">
            {rows.map((r) => (
              <AvailabilityRow key={`${r.provider}::${r.model_id}`} row={r} audience={audience} />
            ))}
          </ul>
        )}

        {rows.length > 0 && (
          <p className="text-muted-foreground mt-3 text-[11.5px]">
            {audience === "bu"
              ? "Models keyed centrally need nothing from you. For the rest, add a provider below with your own credentials."
              : `Models keyed centrally need nothing from you. For the rest, add a provider below — your ${BUSINESS_UNIT_LABEL} Admin approves it before it goes live.`}
          </p>
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
