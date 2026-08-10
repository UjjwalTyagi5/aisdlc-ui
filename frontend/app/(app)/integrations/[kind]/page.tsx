"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";

import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { LoadingState } from "@/components/ui/loading-state";
import { ErrorState } from "@/components/ui/error-state";
import { RestrictedAccess } from "@/components/auth/restricted-access";
import { UnitAccessList } from "@/components/app/unit-access-list";
import { useAccessScope } from "@/hooks/use-access-scope";
import { listIntegrationAccess } from "@/lib/api/integration-access";
import { qk } from "@/lib/api/query-keys";
import { BUSINESS_UNIT_LABEL_PLURAL } from "@/lib/scope";

/**
 * ONE integration, in full — connector or MCP server, on the same screen.
 *
 * The route segment is a connector kind (`jira`) or an MCP server id
 * (`mcp_postgres`), told apart by the `mcp_` prefix the ids already carry. A
 * second route would have duplicated a page whose entire content is the access
 * list, and the two are governed by identical rules — granted to units,
 * consumed by projects. Learning it twice would be the cost of a distinction
 * that stops mattering the moment you are looking at one.
 *
 * NO CREDENTIALS HERE. Neither admin tier authenticates to an integration: the
 * organization decides which exist and who may use them, and each PROJECT
 * supplies the identity it calls with (/projects/[id]/integrations).
 *
 * NO SPEND EITHER, unlike a model provider. Connectors are billed by their own
 * vendor (PRD §34.5), so a figure here would be invented — the one thing a cost
 * surface must never be.
 */
export default function IntegrationDetailPage() {
  const params = useParams<{ kind: string }>();
  const id = decodeURIComponent(params.kind);
  const kind: "connector" | "mcp" = id.startsWith("mcp_") ? "mcp" : "connector";
  const { role } = useAccessScope();

  const accessQ = useQuery({
    queryKey: qk.integrationAccess.list(),
    queryFn: () => listIntegrationAccess(),
  });

  const row = (accessQ.data ?? []).find((r) => r.kind === kind && r.id === id);

  if (role !== null && role !== "org_admin" && role !== "bu_admin") {
    return (
      <RestrictedAccess description="Integration access is the Organization and Business Unit Admins' — it names every business unit's access to this integration." />
    );
  }

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
          {row?.name ?? id}
        </h1>
        <p className="text-muted-foreground mt-1 text-[13px]">
          {kind === "mcp" ? "MCP server" : "Connector"} · which{" "}
          {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()} may use it, and which projects do.
        </p>
      </div>

      {accessQ.isError ? (
        <ErrorState title="Couldn't load this integration" onRetry={() => accessQ.refetch()} />
      ) : accessQ.isLoading ? (
        <LoadingState variant="card" />
      ) : !row ? (
        <ErrorState
          title="No such integration"
          description="It may have been removed, or granted to no business unit — a grant reaching nobody is not a grant."
        />
      ) : (
        <Card className="border-line-soft bg-panel-elevated">
          <CardHeader className="pb-2">
            <h2 className="font-display text-[14px] font-bold tracking-[-0.01em]">Access</h2>
            <p className="text-muted-foreground text-[12px]">
              Which {BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()} may use this. Expand one to see —
              and change — the projects using it.
            </p>
          </CardHeader>
          <CardContent className="pt-0">
            <UnitAccessList
              kind={kind}
              targetId={id}
              name={row.name}
              canRevoke={role === "org_admin"}
            />
          </CardContent>
        </Card>
      )}
    </div>
  );
}
