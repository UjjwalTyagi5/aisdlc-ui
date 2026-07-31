"use client";

import * as React from "react";
import { Bell, Check } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useUiStore } from "@/stores/ui-store";

/**
 * Bell with live unread count. The SSE workspace stream bumps the count via
 * `useUiStore.bumpUnread()` (see `<StreamSubscriber />`); opening the
 * dropdown clears it.
 *
 * Inbox content is still illustrative — a full notifications page is post-MVP.
 */
export function NotificationsBell() {
  const count = useUiStore((s) => s.unreadCount);
  const reset = useUiStore((s) => s.resetUnread);

  return (
    <DropdownMenu onOpenChange={(open) => open && count > 0 && reset()}>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          aria-label={`Notifications (${count} unread)`}
          className="relative"
        >
          <Bell className="size-4" aria-hidden />
          {count > 0 && (
            <span
              aria-hidden
              className={cn(
                "bg-destructive text-destructive-foreground absolute -right-0.5 -top-0.5 grid min-w-4 place-items-center rounded-full px-1 font-mono text-[10px] font-semibold leading-none",
              )}
            >
              {count > 9 ? "9+" : count}
            </span>
          )}
        </Button>
      </DropdownMenuTrigger>
      {/* Elevated panel: border-line-soft, bg-panel-elevated surface */}
      <DropdownMenuContent align="end" className="w-80 border-line-soft bg-panel-elevated">
        <DropdownMenuLabel className="flex items-center justify-between">
          <span className="font-display font-semibold">Notifications</span>
          {count > 0 && (
            <button
              type="button"
              onClick={reset}
              className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 font-mono text-xs"
            >
              <Check className="size-3" aria-hidden />
              Mark read
            </button>
          )}
        </DropdownMenuLabel>
        <DropdownMenuSeparator className="bg-line-soft" />
        <DropdownMenuItem className="flex flex-col items-start gap-0.5">
          <span className="text-sm font-medium">Design awaiting approval</span>
          <span className="font-mono text-muted-foreground text-xs">ingest · live</span>
        </DropdownMenuItem>
        <DropdownMenuItem className="flex flex-col items-start gap-0.5">
          <span className="text-sm font-medium">Test run failed</span>
          <span className="font-mono text-muted-foreground text-xs">checkout · 1h ago</span>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
