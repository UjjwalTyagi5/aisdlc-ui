import { redirect } from "next/navigation";
import * as React from "react";

import { AppShell } from "@/components/app/app-shell";
import { SessionProvider } from "@/components/auth/session-provider";
import { MswInit } from "@/components/mocks/msw-init";
import { getSession } from "@/lib/auth/session";

export default async function AppGroupLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const session = await getSession();
  if (!session) {
    redirect("/login");
  }

  return (
    <SessionProvider value={session}>
      <MswInit>
        <AppShell>{children}</AppShell>
      </MswInit>
    </SessionProvider>
  );
}
