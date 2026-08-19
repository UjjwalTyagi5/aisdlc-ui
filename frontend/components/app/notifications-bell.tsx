"use client";

import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, Check } from "lucide-react";
import { formatDistanceToNow } from "date-fns";

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
import { listNotifications, markNotificationsRead } from "@/lib/api/notifications";
import { qk } from "@/lib/api/query-keys";
import { useUiStore } from "@/stores/ui-store";

/**
 * Bell with live unread count.
 *
 * The list is REAL now — request lifecycle events land in the notification
 * store and are addressed to an identity or a role
 * (`lib/mock/notification-fixtures.ts`). It previously showed two hard-coded
 * rows ("Design awaiting approval", "Test run failed") with nothing behind
 * them, which meant the bell could never tell you anything you did not already
 * know, and "Mark read" cleared a counter rather than any actual item.
 *
 * Two sources of unread, deliberately summed: the SSE stream still bumps
 * `useUiStore` for live run events, and the store carries the durable ones.
 * Opening the dropdown clears both — the count exists to get you to look, and
 * you have looked.
 */
export function NotificationsBell() {
  const queryClient = useQueryClient();
  const streamCount = useUiStore((s) => s.unreadCount);
  const resetStream = useUiStore((s) => s.resetUnread);

  const q = useQuery({
    queryKey: qk.notifications.list(),
    queryFn: listNotifications,
    staleTime: 15_000,
    // Cheap, and it is the one control whose whole job is to be current.
    refetchInterval: 30_000,
  });

  const items = q.data ?? [];
  const unreadStored = items.filter((n) => n.readAt === null).length;
  const count = streamCount + unreadStored;

  const markRead = useMutation({
    mutationFn: markNotificationsRead,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.notifications.list() }),
  });

  function clear() {
    if (streamCount > 0) resetStream();
    if (unreadStored > 0) markRead.mutate();
  }

  return (
    <DropdownMenu onOpenChange={(open) => open && count > 0 && clear()}>
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
      <DropdownMenuContent
        align="end"
        className="w-80 border-line-soft bg-panel-elevated max-h-[70vh] overflow-y-auto"
      >
        <DropdownMenuLabel className="flex items-center justify-between">
          <span className="font-display font-semibold">Notifications</span>
          {count > 0 && (
            <button
              type="button"
              onClick={clear}
              className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 font-mono text-xs"
            >
              <Check className="size-3" aria-hidden />
              Mark read
            </button>
          )}
        </DropdownMenuLabel>
        <DropdownMenuSeparator className="bg-line-soft" />

        {/* A FAILED FETCH IS NOT AN EMPTY BELL. Both rendered "Nothing yet." until
            now, which is the most misleading thing this control could say: it is the
            same sentence for "you are up to date" and "we could not ask". A schema
            mismatch broke this listing outright and it read as a quiet week. */}
        {q.isError ? (
          <p className="text-muted-foreground px-2 py-6 text-center text-[12.5px]">
            Couldn&apos;t load notifications.
          </p>
        ) : items.length === 0 ? (
          <p className="text-muted-foreground px-2 py-6 text-center text-[12.5px]">
            Nothing yet.
          </p>
        ) : (
          items.map((n) => {
            const row = (
              <>
                <span className="flex w-full items-center gap-2">
                  {n.readAt === null && (
                    <span className="bg-brand-bright size-1.5 shrink-0 rounded-full" aria-hidden />
                  )}
                  <span className="truncate text-sm font-medium">{n.title}</span>
                </span>
                {n.body && (
                  <span className="text-muted-foreground line-clamp-2 text-xs">{n.body}</span>
                )}
                <span className="text-muted-foreground font-mono text-[10.5px]">
                  {formatDistanceToNow(new Date(n.createdAt), { addSuffix: true })}
                </span>
              </>
            );
            return (
              <DropdownMenuItem
                key={n.id}
                asChild={!!n.href}
                className="flex flex-col items-start gap-0.5"
              >
                {n.href ? <Link href={n.href}>{row}</Link> : <div>{row}</div>}
              </DropdownMenuItem>
            );
          })
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
