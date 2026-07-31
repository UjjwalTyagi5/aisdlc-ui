"use client";

import * as React from "react";
import Link from "next/link";
import { KeyRound, LogOut, User } from "lucide-react";

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useSession } from "@/hooks/use-session";
import { isLocalAuth, isMockAuth } from "@/lib/auth/mode";
import { effectivePlatformRole } from "@/lib/auth/effective-role";

import { PersonaBadge } from "./scope-indicator";

export function UserMenu() {
  const session = useSession({ required: true });
  const { user, role } = session;
  const platformRole = effectivePlatformRole(session);
  const logoutHref = isLocalAuth
    ? "/api/auth/logout"
    : isMockAuth
      ? "/api/auth/mock/logout"
      : "/auth/logout";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="rounded-full"
          aria-label={`Account menu for ${user.name}`}
        >
          {/* Avatar with brand-gradient fallback ring for elevation */}
          <Avatar className="size-8 ring-1 ring-line-soft">
            {user.avatarUrl && <AvatarImage src={user.avatarUrl} alt="" />}
            <AvatarFallback className="bg-brand-gradient-from/20 font-display text-xs font-semibold text-brand-bright">
              {user.initials}
            </AvatarFallback>
          </Avatar>
        </Button>
      </DropdownMenuTrigger>
      {/* Elevated panel: border-line-soft, bg-panel-elevated surface */}
      <DropdownMenuContent align="end" className="w-60 border-line-soft bg-panel-elevated">
        <DropdownMenuLabel className="flex flex-col gap-1">
          <span className="font-display text-sm font-semibold leading-tight">{user.name}</span>
          <span className="text-muted-foreground truncate text-xs font-normal leading-tight">
            {user.email}
          </span>
          {/* The platform role, not the coarse admin/member/viewer session role —
              "member" told a Business Unit Admin nothing about who they are
              acting as, and this menu is where people look to check. */}
          <span className="mt-0.5 flex flex-wrap items-center gap-1.5">
            <PersonaBadge role={platformRole} />
            {platformRole === null && (
              <span className="font-mono text-[10px] tracking-wider text-brand-bright uppercase">
                {role}
              </span>
            )}
          </span>
        </DropdownMenuLabel>
        <DropdownMenuSeparator className="bg-line-soft" />
        <DropdownMenuItem asChild>
          <Link href="/my-access">
            <KeyRound className="size-4" aria-hidden />
            My access
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <Link href="/settings">
            <User className="size-4" aria-hidden />
            Profile &amp; preferences
          </Link>
        </DropdownMenuItem>
        <DropdownMenuSeparator className="bg-line-soft" />
        <DropdownMenuItem asChild>
          <a href={logoutHref}>
            <LogOut className="size-4" aria-hidden />
            Sign out
          </a>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
