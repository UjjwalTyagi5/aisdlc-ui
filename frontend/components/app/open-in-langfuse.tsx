"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { listLangfuseLinks } from "@/lib/api/traces";
import type { LangfuseLink } from "@/lib/schemas/trace";
import { qk } from "@/lib/api/query-keys";

/**
 * What control (if any) this viewer's links deserve.
 *
 * Pulled out of the component so the rule can be tested without a DOM — this repo runs
 * vitest in the node environment throughout, and the rule is about WHO is offered a way
 * out, not about markup. "none" is the important case: it is what renders for everybody
 * without a Langfuse grant, and it fails silently if it ever regresses.
 */
export function pickLangfuseControl(
  links: LangfuseLink[],
  state: { loading: boolean; error: boolean },
): { kind: "none" } | { kind: "single"; href: string } | { kind: "menu" } {
  // Unknown is not the same as allowed. An in-flight or failed lookup must not render a
  // link somebody may not be able to follow.
  if (state.loading || state.error) return { kind: "none" };
  if (links.length === 0) return { kind: "none" };
  if (links.length === 1) return { kind: "single", href: links[0]!.url };
  return { kind: "menu" };
}

/**
 * "Open in Langfuse" — the way out of this product into the full tracing UI, for the
 * people who can actually get in.
 *
 * WHY THE SERVER DECIDES WHO SEES IT. The obvious implementation gates on `trace:view`,
 * and would be right today: the four roles holding that permission are exactly the four
 * that receive a Langfuse grant. But they are two independent systems, and the moment
 * either list changes the button starts sending people to an access-denied page — a
 * worse outcome than no button, because the product looks broken rather than correctly
 * restrictive. `GET /traces/langfuse` answers with the projects the viewer can genuinely
 * open, resolved against Langfuse's own memberships, and returns nothing for everyone
 * else. Rendering nothing is then the honest default.
 *
 * SCOPE COMES WITH IT. That endpoint reuses the same binding lookup the trace list does,
 * so this can never name a project whose traces the viewer is not allowed to see.
 *
 * Deliberately silent while loading and on error: a control that appears late is better
 * than one that appears and then vanishes, and an error here means "we could not tell
 * whether you have access", which must not render as "you do".
 */
export function OpenInLangfuse({ className }: { className?: string }) {
  const q = useQuery({
    queryKey: qk.traces.langfuseLinks(),
    queryFn: listLangfuseLinks,
    // Membership changes when somebody is appointed, not minute to minute.
    staleTime: 5 * 60_000,
    retry: false,
  });

  const links = q.data ?? [];
  const control = pickLangfuseControl(links, { loading: q.isLoading, error: q.isError });
  if (control.kind === "none") return null;

  // One project is the common case — send them straight there rather than making them
  // choose from a menu of one.
  if (control.kind === "single") {
    const only = links[0]!;
    return (
      <Button variant="outline" size="sm" asChild className={className}>
        <a href={only.url} target="_blank" rel="noreferrer">
          <ExternalLink className="size-4" aria-hidden />
          Open in Langfuse
        </a>
      </Button>
    );
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" className={className}>
          <ExternalLink className="size-4" aria-hidden />
          Open in Langfuse
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuLabel className="text-muted-foreground text-[11px] font-normal">
          Pick a project to open
        </DropdownMenuLabel>
        {links.map((l) => (
          <DropdownMenuItem key={l.projectId} asChild>
            <a href={l.url} target="_blank" rel="noreferrer">
              <span className="truncate">{l.projectName}</span>
              {/* An invitation is access they do not hold YET — following the link is
                  what turns it into a membership, so say so rather than letting the
                  sign-in screen be the first they hear of it. */}
              {l.access === "invited" && (
                <span className="text-muted-foreground ml-auto text-[10px] uppercase">
                  sign in to join
                </span>
              )}
            </a>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
