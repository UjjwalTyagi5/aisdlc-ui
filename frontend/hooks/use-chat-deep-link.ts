"use client";

import * as React from "react";
import { useSearchParams } from "next/navigation";

/**
 * The conversation a `?session=` link is pointing at, and the drawer opened to show it.
 *
 * WHY IT IS ONE HOOK AND NOT EIGHT COPIES. Every agent page owns its own `chatOpen`
 * state and its own `useAgentChat`, so "open this session" is two steps on each of the
 * eight — open the drawer, then restore the transcript. Written out per page, the
 * eighth copy is the one that opens the drawer on a blank chat and looks, to whoever
 * clicked, exactly like a conversation that was lost.
 *
 * The returned id goes to `useAgentChat({ openSessionId })`, which restores the
 * transcript once. This hook only handles the half that page state owns.
 *
 * A page reached WITHOUT the parameter is untouched: no drawer opens, and the chat
 * behaves as it always has.
 */
export function useChatDeepLink(setChatOpen: (open: boolean) => void): string | undefined {
  const searchParams = useSearchParams();
  const sessionId = searchParams.get("session") ?? undefined;

  // Keyed on the id alone. Depending on the setter would re-open the drawer on every
  // render that produced a new function identity — including the render right after
  // somebody closed it.
  const opened = React.useRef<string | undefined>(undefined);
  React.useEffect(() => {
    if (!sessionId || opened.current === sessionId) return;
    opened.current = sessionId;
    setChatOpen(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  return sessionId;
}
