"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ChevronDown,
  Loader2,
  ShieldCheck,
  Trash2,
  UserPlus,
  Users,
  X,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  addWorkspaceMember,
  listWorkspaceMembers,
  removeWorkspaceMember,
  updateWorkspaceMemberRole,
} from "@/lib/api/workspaces";
import { qk } from "@/lib/api/query-keys";
import type { WorkspaceMemberOut } from "@/lib/schemas/workspace";
import { ROLE_META, ROLE_ORDER, type PlatformRole } from "@/lib/roles";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";

// ─── Role catalog ────────────────────────────────────────────────────────────
// Derived from the platform's twelve roles (PRD §33.1 plus Scrum Master) so this picker can
// never drift from the role model.
//
// Only delivery-tier roles are offered here: per the ownership cascade
// (§14.3) a Business Unit owner grants Project Admin, builders and custom —
// never a governance role, which would be tier escalation (§14.5).
//
// `contributor` is excluded for a different reason than the governance roles:
// it is not a job but the absence of one, the placeholder a person carries
// between being onboarded and being given a role here.
const ROLES = ROLE_ORDER.filter(
  (r) => ROLE_META[r].tier === "delivery" && r !== "contributor",
).map((r) => ({
  name: r,
  label: ROLE_META[r].label,
  description: ROLE_META[r].oneLiner,
}));

type RoleName = PlatformRole;

function roleLabelFor(name: string): string {
  return ROLES.find((r) => r.name === name)?.label ?? name;
}

function roleBadgeClass(name: string): string {
  if (name === "admin") return "border-primary/40 bg-primary/10 text-primary";
  if (name === "tech_lead") return "border-info/30 bg-info/10 text-info";
  return "border-line-soft text-foreground";
}

// ─── Avatar color ─────────────────────────────────────────────────────────────
const AVATAR_COLORS = [
  "bg-[oklch(0.55_0.18_30)]",   // brand
  "bg-[oklch(0.5_0.14_240)]",   // blue
  "bg-[oklch(0.48_0.13_160)]",  // teal
  "bg-[oklch(0.5_0.12_290)]",   // violet
  "bg-[oklch(0.46_0.11_100)]",  // olive
];

function avatarColor(userId: string): string {
  let n = 0;
  for (let i = 0; i < userId.length; i++) n += userId.charCodeAt(i);
  return AVATAR_COLORS[n % AVATAR_COLORS.length]!;
}

// ─── Props ───────────────────────────────────────────────────────────────────
export interface WorkspaceMembersSheetProps {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  workspaceId: string;
  workspaceName: string;
}

// ─── Main component ──────────────────────────────────────────────────────────
export function WorkspaceMembersSheet({
  open,
  onOpenChange,
  workspaceId,
  workspaceName,
}: WorkspaceMembersSheetProps) {
  const queryClient = useQueryClient();

  const membersQ = useQuery({
    queryKey: qk.workspaces.members(workspaceId),
    queryFn: () => listWorkspaceMembers(workspaceId),
    enabled: open,
    staleTime: 30_000,
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: qk.workspaces.members(workspaceId) });

  const removeMutation = useMutation({
    mutationFn: ({ uid }: { uid: string }) => removeWorkspaceMember(workspaceId, uid),
    onSuccess: (_data, { uid }) => {
      const member = (membersQ.data ?? []).find((m) => m.userId === uid);
      toast.success(`Removed ${member?.email ?? member?.displayName ?? uid}`);
      invalidate();
      queryClient.invalidateQueries({ queryKey: qk.workspaces.list() });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Couldn't remove member"),
  });

  const roleMutation = useMutation({
    mutationFn: ({ uid, roleName }: { uid: string; roleName: string }) =>
      updateWorkspaceMemberRole(workspaceId, uid, { roleName }),
    onSuccess: (_data, { roleName }) => {
      toast.success(`Role updated to ${roleLabelFor(roleName)}`);
      invalidate();
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Couldn't update role"),
  });

  const members = membersQ.data ?? [];

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="border-line-soft bg-panel-elevated flex w-full flex-col p-0 sm:max-w-md"
      >
        {/* Header */}
        <SheetHeader className="border-line-soft border-b px-6 py-5">
          <div className="flex items-center gap-3">
            <span className="from-brand-gradient-from to-brand-gradient-to grid size-9 shrink-0 place-items-center rounded-xl bg-gradient-to-br">
              <Users className="size-4 text-white" aria-hidden />
            </span>
            <div>
              <SheetTitle className="font-display text-[17px] font-bold tracking-[-0.01em]">
                {workspaceName}
              </SheetTitle>
              <SheetDescription className="mt-0.5 font-mono text-[11px] tracking-wider uppercase">
                {membersQ.isLoading
                  ? "Loading members…"
                  : `${members.length} member${members.length !== 1 ? "s" : ""}`}
              </SheetDescription>
            </div>
          </div>
        </SheetHeader>

        {/* Members list */}
        <ScrollArea className="min-h-0 flex-1">
          {membersQ.isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="text-muted-foreground size-5 animate-spin" aria-hidden />
            </div>
          ) : members.length === 0 ? (
            <div className="flex flex-col items-center gap-2 py-14 text-center">
              <Users className="text-muted-foreground size-8" aria-hidden />
              <p className="font-display text-sm font-semibold">No members yet</p>
              <p className="text-muted-foreground max-w-[22ch] text-[13px]">
                Add people below to give them access to this {BUSINESS_UNIT_LABEL.toLowerCase()}.
              </p>
            </div>
          ) : (
            <ul>
              {members.map((m) => (
                <MemberRow
                  key={m.userId}
                  member={m}
                  busy={removeMutation.isPending || roleMutation.isPending}
                  onRemove={(uid) => removeMutation.mutate({ uid })}
                  onChangeRole={(uid, roleName) => roleMutation.mutate({ uid, roleName })}
                />
              ))}
            </ul>
          )}
        </ScrollArea>

        {/* Add member form */}
        <AddMemberForm workspaceId={workspaceId} onAdded={invalidate} />
      </SheetContent>
    </Sheet>
  );
}

// ─── Member row ───────────────────────────────────────────────────────────────
function MemberRow({
  member: m,
  busy,
  onRemove,
  onChangeRole,
}: {
  member: WorkspaceMemberOut;
  busy: boolean;
  onRemove: (uid: string) => void;
  onChangeRole: (uid: string, roleName: string) => void;
}) {
  const [confirmRemove, setConfirmRemove] = React.useState(false);
  const displayName = m.displayName ?? m.email ?? m.userId;
  const sub = m.email && m.displayName ? m.email : m.userId;

  return (
    <li className="border-line-soft hover:bg-surface-1 flex items-center gap-3 border-b px-5 py-3 last:border-b-0">
      {/* Avatar */}
      <Avatar className="size-9 shrink-0">
        <AvatarFallback
          className={cn(
            "font-mono text-[11px] font-semibold text-white",
            avatarColor(m.userId),
          )}
        >
          {m.initials}
        </AvatarFallback>
      </Avatar>

      {/* Identity */}
      <div className="min-w-0 flex-1">
        <div className="truncate text-[13.5px] font-semibold">{displayName}</div>
        <div className="text-muted-foreground truncate font-mono text-[11px]">{sub}</div>
      </div>

      {/* Role badge / change dropdown */}
      {confirmRemove ? (
        /* Inline confirm reveal — avoids a modal for a simple destructive action */
        <div className="flex shrink-0 items-center gap-1.5">
          <span className="text-muted-foreground font-mono text-[11px]">Remove?</span>
          <Button
            size="sm"
            variant="destructive"
            className="h-7 px-2.5 text-[12px]"
            disabled={busy}
            onClick={() => {
              setConfirmRemove(false);
              onRemove(m.userId);
            }}
          >
            Yes, remove
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="h-7 px-1.5"
            onClick={() => setConfirmRemove(false)}
          >
            <X className="size-3.5" aria-hidden />
          </Button>
        </div>
      ) : (
        <div className="flex shrink-0 items-center gap-1.5">
          {/* Role change dropdown */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                className={cn(
                  "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10.5px] font-semibold transition-opacity hover:opacity-80 focus-visible:outline-none focus-visible:ring-1",
                  roleBadgeClass(m.roleName),
                )}
                disabled={busy}
                aria-label="Change role"
              >
                {m.roleName === "admin" && <ShieldCheck className="size-3" aria-hidden />}
                {roleLabelFor(m.roleName)}
                <ChevronDown className="size-3 opacity-60" aria-hidden />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="border-line-soft bg-panel-elevated w-52">
              <DropdownMenuLabel className="text-muted-foreground font-mono text-[10px] tracking-widest uppercase">
                Change role
              </DropdownMenuLabel>
              <DropdownMenuSeparator className="bg-line-soft" />
              {ROLES.map((r) => (
                <DropdownMenuItem
                  key={r.name}
                  onSelect={() => {
                    if (r.name !== m.roleName) onChangeRole(m.userId, r.name);
                  }}
                  className={cn(
                    "gap-2",
                    r.name === m.roleName && "text-brand-bright",
                  )}
                >
                  {/* Project Admin is the only delivery role carrying
                      member:manage, and is the fallback approver on every
                      agent for its project (PRD §14.1, §33.2). */}
                  {r.name === "project_admin" && (
                    <ShieldCheck className="size-3.5" aria-hidden />
                  )}
                  <span className="flex-1">
                    <span className="block font-medium">{r.label}</span>
                    <span className="text-muted-foreground block font-mono text-[10px]">
                      {r.description}
                    </span>
                  </span>
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>

          {/* Remove */}
          <Button
            variant="ghost"
            size="icon"
            className="text-muted-foreground hover:text-destructive size-7 shrink-0"
            disabled={busy}
            aria-label={`Remove ${displayName}`}
            onClick={() => setConfirmRemove(true)}
          >
            <Trash2 className="size-3.5" aria-hidden />
          </Button>
        </div>
      )}
    </li>
  );
}

// ─── Add member form ──────────────────────────────────────────────────────────
function AddMemberForm({
  workspaceId,
  onAdded,
}: {
  workspaceId: string;
  onAdded: () => void;
}) {
  const [userId, setUserId] = React.useState("");
  const [roleName, setRoleName] = React.useState<RoleName>("developer");
  const [error, setError] = React.useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => addWorkspaceMember(workspaceId, { userId: userId.trim(), roleName }),
    onSuccess: (member) => {
      toast.success(`Added ${member.email ?? member.displayName ?? member.userId}`);
      setUserId("");
      setError(null);
      onAdded();
    },
    onError: (e) => {
      const msg = e instanceof Error ? e.message : "Couldn't add member";
      setError(msg);
    },
  });

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!userId.trim()) return;
    setError(null);
    mutation.mutate();
  }

  return (
    <form
      onSubmit={onSubmit}
      className="border-line-soft border-t px-6 py-5 space-y-4"
    >
      <div className="flex items-center gap-2">
        <UserPlus className="text-muted-foreground size-3.5 shrink-0" aria-hidden />
        <span className="font-display text-[13px] font-semibold">Add a member</span>
      </div>

      {error && (
        <p className="text-destructive rounded-md bg-destructive/10 px-3 py-2 font-mono text-[11.5px]">
          {error}
        </p>
      )}

      <div className="space-y-3">
        <div className="space-y-1.5">
          <Label htmlFor="ws-member-id" className="text-[11px]">
            User ID or email
          </Label>
          <Input
            id="ws-member-id"
            value={userId}
            onChange={(e) => setUserId(e.target.value)}
            placeholder="auth0|abc123  ·  person@company.com"
            className="border-line-soft font-mono text-[12.5px]"
            autoComplete="off"
            disabled={mutation.isPending}
          />
        </div>

        <div className="flex items-end gap-2">
          <div className="flex-1 space-y-1.5">
            <Label htmlFor="ws-member-role" className="text-[11px]">
              Role
            </Label>
            <Select
              value={roleName}
              onValueChange={(v) => setRoleName(v as RoleName)}
              disabled={mutation.isPending}
            >
              <SelectTrigger id="ws-member-role" className="border-line-soft h-9">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ROLES.map((r) => (
                  <SelectItem key={r.name} value={r.name}>
                    <span className="flex flex-col">
                      <span className="font-medium">{r.label}</span>
                    </span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <Button
            type="submit"
            disabled={!userId.trim() || mutation.isPending}
            className="from-brand-gradient-from to-brand-gradient-to h-9 bg-gradient-to-br font-semibold text-white shadow-[0_4px_12px_-4px_oklch(0.6_0.2_35_/_0.5)]"
          >
            {mutation.isPending ? (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            ) : (
              <UserPlus className="size-4" aria-hidden />
            )}
            Add
          </Button>
        </div>
      </div>
    </form>
  );
}
