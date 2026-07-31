import { redirect } from "next/navigation";

import { PlatformShell } from "@/components/platform/platform-shell";
import { fetchPlatformUsers, type PlatformUserRow } from "@/lib/api/platform";
import { getSession } from "@/lib/auth/session";

import { PlatformUsersClient } from "./platform-users-client";

export default async function PlatformUsersPage() {
  const session = await getSession();
  if (!session) redirect("/login");
  if (!session.permissions.includes("platform:*")) redirect("/projects");

  let users: PlatformUserRow[] = [];
  try {
    users = await fetchPlatformUsers(session);
  } catch {
    // fall through to empty list; client shows create dialog regardless
  }

  return (
    <PlatformShell userName={session.user.name}>
      <div className="w-full space-y-6">
        <div className="space-y-1.5">
          <div className="text-brand-bright flex items-center gap-2 font-mono text-[11px] font-semibold tracking-[0.14em] uppercase">
            <span className="bg-brand-bright inline-block h-px w-5" aria-hidden />
            Application Team
          </div>
          <h1 className="font-display text-3xl font-bold tracking-tight">Platform users</h1>
          <p className="text-muted-foreground text-sm">
            Application-team members with cross-tenant{" "}
            <code className="font-mono text-xs">platform:*</code> access.
          </p>
        </div>
        <PlatformUsersClient initialUsers={users} currentUserId={session.user.id} />
      </div>
    </PlatformShell>
  );
}
