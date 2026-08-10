import type { Metadata } from "next";
import { redirect } from "next/navigation";
import Link from "next/link";
import { Building2, ShieldCheck } from "lucide-react";

import { PlatformShell } from "@/components/platform/platform-shell";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { fetchOrganizations, type PlatformOrg } from "@/lib/api/platform";
import { getSession } from "@/lib/auth/session";

import { CreateOrgDialog } from "./create-org-dialog";

export const metadata: Metadata = { title: "Platform Console" };

export default async function PlatformConsolePage() {
  const session = await getSession();
  if (!session) redirect("/login");
  if (!session.permissions.includes("platform:*")) redirect("/projects");

  let orgs: PlatformOrg[] = [];
  let loadError = false;
  try {
    orgs = await fetchOrganizations(session);
  } catch {
    loadError = true;
  }

  const activeCount = orgs.filter((o) => !o.suspended).length;

  return (
    <PlatformShell userName={session.user.name}>
      {/* Identity banner */}
      <section className="space-y-3">
        <Badge variant="secondary" className="gap-1.5">
          <ShieldCheck className="size-3.5" aria-hidden />
          Platform Admin
        </Badge>
        <div className="space-y-1.5">
          <h1 className="font-display text-3xl font-bold tracking-tight">
            Welcome back, {session.user.name}
          </h1>
          <p className="text-muted-foreground max-w-2xl text-sm">
            Signed in as <span className="text-foreground font-medium">{session.user.email}</span>.
            You have cross-tenant access to every organization on the platform.
          </p>
        </div>
      </section>

      {/* Organizations */}
      <section className="mt-10 space-y-5">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="space-y-1">
            <div className="text-brand-bright flex items-center gap-2 font-mono text-[11px] font-semibold tracking-[0.14em] uppercase">
              <span className="bg-brand-bright inline-block h-px w-5" aria-hidden />
              Tenants
            </div>
            <div className="flex items-baseline gap-3">
              <h2 className="font-display text-xl font-bold tracking-tight">Organizations</h2>
              {!loadError && (
                <span className="text-muted-foreground text-xs tabular-nums">
                  {orgs.length} total · {activeCount} active
                </span>
              )}
            </div>
          </div>
          <CreateOrgDialog />
        </div>

        {loadError ? (
          <Alert variant="destructive">
            <AlertTitle>Couldn&apos;t load organizations</AlertTitle>
            <AlertDescription>
              The platform API didn&apos;t respond. Confirm the backend is running and your session
              carries <code className="font-mono">platform:*</code>.
            </AlertDescription>
          </Alert>
        ) : orgs.length === 0 ? (
          <Card className="border-dashed">
            <CardContent className="flex flex-col items-center gap-2 py-16 text-center">
              <span className="bg-muted text-muted-foreground grid size-12 place-items-center rounded-xl border">
                <Building2 className="size-6" aria-hidden />
              </span>
              <p className="text-sm font-medium">No organizations yet</p>
              <p className="text-muted-foreground max-w-sm text-xs">
                Create your first organization to get started.
              </p>
            </CardContent>
          </Card>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
            {orgs.map((org) => (
              <Link key={org.id} href={`/platform/orgs/${org.id}`} className="group">
                <Card className="hover:border-brand-bright/50 h-full transition-colors hover:shadow-md">
                  <CardHeader className="flex flex-row items-start gap-3 space-y-0">
                    <span className="bg-brand-gradient grid size-10 shrink-0 place-items-center rounded-lg text-white shadow-sm">
                      <Building2 className="size-5" aria-hidden />
                    </span>
                    <div className="flex min-w-0 flex-1 flex-col">
                      <div className="flex items-center gap-2">
                        <CardTitle className="truncate text-sm">{org.display_name}</CardTitle>
                        {org.suspended && (
                          <Badge variant="destructive" className="shrink-0 text-[10px]">
                            Suspended
                          </Badge>
                        )}
                      </div>
                      <span className="text-muted-foreground truncate font-mono text-xs">
                        {org.slug}
                      </span>
                    </div>
                  </CardHeader>
                  <CardContent className="text-muted-foreground flex gap-4 pt-0 text-xs tabular-nums">
                    <span>
                      <span className="text-foreground font-medium">{org.member_count}</span>{" "}
                      members
                    </span>
                    <span>
                      <span className="text-foreground font-medium">{org.run_count}</span> runs
                    </span>
                  </CardContent>
                </Card>
              </Link>
            ))}
          </div>
        )}
      </section>
    </PlatformShell>
  );
}
