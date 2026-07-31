"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { ShieldCheck, UserPlus } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { createPlatformUser, setPlatformUserActive } from "@/lib/api/platform-client";
import type { PlatformUserRow } from "@/lib/api/platform";

export function PlatformUsersClient({
  initialUsers,
  currentUserId,
}: {
  initialUsers: PlatformUserRow[];
  currentUserId: string;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [pw, setPw] = useState("");
  const [role, setRole] = useState("platform_admin");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await createPlatformUser(email, pw, role);
      setOpen(false);
      setEmail("");
      setPw("");
      setRole("platform_admin");
      router.refresh();
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : "Failed to create user");
    } finally {
      setBusy(false);
    }
  }

  async function toggleActive(u: PlatformUserRow) {
    setBusy(true);
    try {
      await setPlatformUserActive(u.user_id, !u.active);
      router.refresh();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild>
            <Button size="sm" className="gap-2">
              <UserPlus className="size-4" aria-hidden />
              Add platform user
            </Button>
          </DialogTrigger>
          <DialogContent>
            <form onSubmit={create}>
              <DialogHeader>
                <DialogTitle>Add platform user</DialogTitle>
                <DialogDescription>
                  They sign in with this email and password and get cross-tenant access.
                </DialogDescription>
              </DialogHeader>
              <div className="space-y-4 py-4">
                <div className="space-y-1.5">
                  <Label htmlFor="pu-email">Email</Label>
                  <Input
                    id="pu-email"
                    type="email"
                    required
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="pu-pw">Temporary password</Label>
                  <Input
                    id="pu-pw"
                    type="password"
                    minLength={8}
                    required
                    value={pw}
                    onChange={(e) => setPw(e.target.value)}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="pu-role">Role</Label>
                  <Select value={role} onValueChange={setRole}>
                    <SelectTrigger id="pu-role">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="platform_admin">Platform admin</SelectItem>
                      <SelectItem value="platform_support">Platform support</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                {err && <p className="text-destructive text-xs">{err}</p>}
              </div>
              <DialogFooter>
                <Button type="submit" disabled={busy || pw.length < 8}>
                  Create user
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      </div>

      {initialUsers.length === 0 ? (
        <Card className="border-dashed">
          <CardContent className="text-muted-foreground py-10 text-center text-sm">
            No platform users yet. The env-seeded admin isn&apos;t listed here unless also added to
            the table.
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-2">
          {initialUsers.map((u) => (
            <Card key={u.user_id}>
              <CardContent className="flex items-center justify-between gap-4 py-3">
                <div className="flex items-center gap-3">
                  <ShieldCheck className="text-muted-foreground size-4" aria-hidden />
                  <div>
                    <p className="text-sm font-medium">{u.email}</p>
                    <p className="text-muted-foreground text-xs">{u.platform_role}</p>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <Badge variant={u.active ? "secondary" : "outline"}>
                    {u.active ? "Active" : "Disabled"}
                  </Badge>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={busy || u.user_id === currentUserId}
                    onClick={() => toggleActive(u)}
                  >
                    {u.active ? "Deactivate" : "Reactivate"}
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
