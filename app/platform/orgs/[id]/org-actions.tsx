"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { resetOrgAdminPassword, setOrgSuspended } from "@/lib/api/platform-client";

export function OrgActions({
  orgId,
  suspended,
  adminEmails,
}: {
  orgId: string;
  suspended: boolean;
  adminEmails: string[];
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [email, setEmail] = useState(adminEmails[0] ?? "");
  const [pw, setPw] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function toggleSuspend() {
    setBusy(true);
    setErr(null);
    try {
      await setOrgSuspended(orgId, !suspended);
      router.refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to update");
    } finally {
      setBusy(false);
    }
  }

  async function reset(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      await resetOrgAdminPassword(orgId, email, pw);
      setMsg(`Password reset for ${email}.`);
      setPw("");
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : "Failed to reset");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="border-destructive/30">
      <CardHeader>
        <CardTitle className="text-sm">Admin actions</CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="text-sm font-medium">
              {suspended ? "Reactivate organization" : "Suspend organization"}
            </p>
            <p className="text-muted-foreground text-xs">
              {suspended ? "Members can sign in again." : "Blocks all members from signing in."}
            </p>
          </div>
          <Button
            variant={suspended ? "default" : "destructive"}
            disabled={busy}
            onClick={toggleSuspend}
          >
            {suspended ? "Reactivate" : "Suspend"}
          </Button>
        </div>

        <form onSubmit={reset} className="space-y-3 border-t pt-4">
          <p className="text-sm font-medium">Reset an org admin&apos;s password</p>
          <div className="space-y-1.5">
            <Label htmlFor="admin-email">Admin email</Label>
            {adminEmails.length > 1 ? (
              <Select value={email} onValueChange={setEmail}>
                <SelectTrigger id="admin-email">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {adminEmails.map((a) => (
                    <SelectItem key={a} value={a}>
                      {a}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <Input id="admin-email" value={email} onChange={(e) => setEmail(e.target.value)} />
            )}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="admin-pw">New password</Label>
            <Input
              id="admin-pw"
              type="password"
              minLength={8}
              required
              value={pw}
              onChange={(e) => setPw(e.target.value)}
            />
          </div>
          {msg && <p className="text-xs text-emerald-600">{msg}</p>}
          {err && <p className="text-destructive text-xs">{err}</p>}
          <Button type="submit" size="sm" disabled={busy || pw.length < 8}>
            Reset password
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
