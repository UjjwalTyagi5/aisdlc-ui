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
import type { Notification } from "@/lib/schemas";
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

  /**
   * Marking read used to be fire-and-forget: mutate, and invalidate on success. Three
   * ways that stuck.
   *
   *   · A FAILED CALL SAID NOTHING. There was no onError, so a refused or dropped
   *     request left the badge exactly as it was and the button looking dead. That is
   *     the "sometimes it does nothing" -- it did do something, and the something
   *     failed silently.
   *   · NOTHING MOVED UNTIL THE SERVER CAME BACK. The dots and the count are computed
   *     from the cached list, and that list only changes after the invalidate round
   *     trips, so the panel sat visibly unread for as long as the request took.
   *   · IT COULD FIRE TWICE. Opening the panel calls clear(), and the button calls it
   *     again -- with the pre-refetch `unreadStored`, which is still non-zero. The
   *     second call marks nothing and invalidates on top of the first.
   *
   * So: write the read state into the cache immediately, keep the previous list to put
   * back if the call fails, and refuse to start a second one while the first is in
   * flight.
   */
  const markRead = useMutation({
    mutationFn: markNotificationsRead,
    onMutate: async () => {
      await queryClient.cancelQueries({ queryKey: qk.notifications.list() });
      const previous = queryClient.getQueryData<Notification[]>(qk.notifications.list());
      const now = new Date().toISOString();
      queryClient.setQueryData<Notification[]>(qk.notifications.list(), (old) =>
        (old ?? []).map((n) => (n.readAt === null ? { ...n, readAt: now } : n)),
      );
      return { previous };
    },
    onError: (_err, _vars, context) => {
      // Put the unread state back. A badge that silently stays at zero after a failed
      // call is worse than one that comes back: the reader would never know there was
      // anything still waiting on them.
      if (context?.previous) {
        queryClient.setQueryData(qk.notifications.list(), context.previous);
      }
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: qk.notifications.list() }),
  });

  function clear() {
    if (streamCount > 0) resetStream();
    if (unreadStored > 0 && !markRead.isPending) markRead.mutate();
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
              disabled={markRead.isPending}
              className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 font-mono text-xs disabled:opacity-50"
            >
              <Check className="size-3" aria-hidden />
              {markRead.isPending ? "Marking…" : "Mark read"}
            </button>
          )}
        </DropdownMenuLabel>
        <DropdownMenuSeparator className="bg-line-soft" />

        {/* The one thing the old version could not say. */}
        {markRead.isError && (
          <p role="alert" className="text-destructive px-2 pb-1 pt-2 text-center text-[12.5px]">
            Couldn&apos;t mark these read. Try again.
          </p>
        )}

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
