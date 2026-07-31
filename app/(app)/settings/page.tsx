import type { Metadata } from "next";

import { RestrictedAccess } from "@/components/auth/restricted-access";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getSession } from "@/lib/auth/session";
import { hasPermission } from "@/lib/auth/permissions";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";

export const metadata: Metadata = { title: "Settings" };

export default async function SettingsPage() {
  const session = await getSession();
  if (!hasPermission(session, "settings:manage")) {
    return <RestrictedAccess description="Org settings require the settings:manage permission." />;
  }

  return (
    <div className="max-w-4xl space-y-6 p-4 md:px-10 md:py-8">
      {/* Editorial page header — Mission Control elevation */}
      <div className="space-y-1">
        <p className="text-brand-bright font-mono text-[11px] font-semibold tracking-[0.14em] uppercase">
          {BUSINESS_UNIT_LABEL}
        </p>
        <h1 className="font-display text-2xl font-bold tracking-tight">Settings</h1>
        <p className="text-muted-foreground text-sm">
          Placeholder — profile, preferences, and {BUSINESS_UNIT_LABEL.toLowerCase()} settings expand
          in Chunk 13.
        </p>
      </div>

      <Card className="border-line-soft bg-panel-elevated shadow-none">
        <CardHeader>
          <CardTitle className="font-display text-base font-semibold">Account</CardTitle>
          <CardDescription>Auth0 session details appear here once Chunk 4 lands.</CardDescription>
        </CardHeader>
        <CardContent className="text-muted-foreground text-sm">
          Nothing to configure yet.
        </CardContent>
      </Card>
    </div>
  );
}
