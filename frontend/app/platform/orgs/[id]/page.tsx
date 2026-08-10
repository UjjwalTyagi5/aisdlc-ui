import { notFound, redirect } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, Building2 } from "lucide-react";

import { PlatformShell } from "@/components/platform/platform-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { fetchOrgDetail } from "@/lib/api/platform";
import { getSession } from "@/lib/auth/session";
import { BUSINESS_UNIT_LABEL_PLURAL } from "@/lib/scope";

import { OrgActions } from "./org-actions";

export default async function OrgDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) redirect("/login");
  if (!session.permissions.includes("platform:*")) redirect("/projects");

  const { id } = await params;
  let detail;
  try {
    detail = await fetchOrgDetail(session, id);
  } catch {
    notFound();
  }

  return (
    <PlatformShell userName={session.user.name}>
      <div className="w-full space-y-8">
        <Button asChild variant="ghost" size="sm" className="-ml-2 gap-2">
          <Link href="/platform">
            <ArrowLeft className="size-4" aria-hidden />
            Back to organizations
          </Link>
        </Button>

        <section className="space-y-2">
          <div className="flex items-center gap-3">
            <span className="bg-brand-gradient grid size-11 place-items-center rounded-lg text-white shadow-sm">
              <Building2 className="size-5" aria-hidden />
            </span>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="font-display text-2xl font-bold tracking-tight">
                  {detail.display_name}
                </h1>
                {detail.suspended && <Badge variant="destructive">Suspended</Badge>}
              </div>
              <span className="text-muted-foreground font-mono text-xs">{detail.slug}</span>
            </div>
          </div>
        </section>

        <section className="grid gap-3 sm:grid-cols-3">
          <Stat label="Members" value={detail.member_count} />
          <Stat label="Runs" value={detail.run_count} />
          <Stat label="Total cost" value={`$${detail.total_cost_usd.toFixed(2)}`} />
        </section>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Organization admins</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1 text-sm">
            {detail.admins.length === 0 ? (
              <p className="text-muted-foreground text-xs">No org_admin assigned.</p>
            ) : (
              detail.admins.map((a) => (
                <p key={a.user_id} className="font-mono text-xs">
                  {a.email}
                </p>
              ))
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm">
              People{" "}
              <span className="text-muted-foreground font-normal">({detail.members.length})</span>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            {detail.members.length === 0 ? (
              <p className="text-muted-foreground text-xs">No members in this organization yet.</p>
            ) : (
              detail.members.map((m) => (
                <div
                  key={m.user_id}
                  className="flex items-center justify-between gap-3 border-b py-2 last:border-b-0"
                >
                  <span className="font-mono text-xs">{m.email ?? m.user_id}</span>
                  <div className="flex flex-wrap justify-end gap-1">
                    {m.roles.length === 0 ? (
                      <span className="text-muted-foreground text-xs">no roles</span>
                    ) : (
                      m.roles.map((r) => (
                        <Badge
                          key={r}
                          variant={r === "org_admin" || r === "admin" ? "default" : "secondary"}
                        >
                          {r}
                        </Badge>
                      ))
                    )}
                  </div>
                </div>
              ))
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm">{BUSINESS_UNIT_LABEL_PLURAL}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1 text-sm">
            {detail.workspaces.map((w) => (
              <div key={w.id} className="flex justify-between">
                <span>{w.display_name}</span>
                <span className="text-muted-foreground font-mono text-xs">{w.slug}</span>
              </div>
            ))}
          </CardContent>
        </Card>

        <OrgActions
          orgId={detail.org_id}
          suspended={detail.suspended}
          adminEmails={detail.admins.map((a) => a.email)}
        />
      </div>
    </PlatformShell>
  );
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <Card>
      <CardContent className="py-4">
        <p className="text-muted-foreground text-xs tracking-wider uppercase">{label}</p>
        <p className="text-2xl font-semibold tabular-nums">{value}</p>
      </CardContent>
    </Card>
  );
}
