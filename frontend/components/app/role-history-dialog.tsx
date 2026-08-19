"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { ArrowRight, History } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { LoadingState } from "@/components/ui/loading-state";
import { ApiErrorState } from "@/components/feedback/api-error-state";
import { api } from "@/lib/api/client";
import { ROLE_META, type PlatformRole } from "@/lib/roles";

const RoleChange = z.object({
  at: z.string().nullable(),
  kind: z.enum(["granted", "changed", "removed"]),
  from: z.string().nullable(),
  to: z.string().nullable(),
  scopeKind: z.string().nullable(),
  scopeName: z.string().nullable(),
  actorId: z.string().nullable(),
  actorEmail: z.string().nullable(),
});
type RoleChange = z.infer<typeof RoleChange>;

const listRoleHistory = (userId: string) =>
  api(`/users/${encodeURIComponent(userId)}/role-history`, {
    schema: z.array(RoleChange),
  });

function roleLabel(role: string | null): string {
  if (!role) return "—";
  const meta = ROLE_META[role as PlatformRole];
  return meta ? meta.label : role;
}

/**
 * Who held what, when it changed, and who changed it.
 *
 * THREE KINDS, NOT ONE LIST OF GRANTS. A first grant has no "from" — there was
 * nothing before it — a change has both sides, and a removal has no "to". Rendering
 * all three as "granted X" would make the most important row, somebody's access being
 * taken away, read as though they had gained something.
 *
 * The actor is named on every row because "who made me a Project Admin" is the
 * question actually asked afterwards, and a history that only says WHAT changed sends
 * the reader to ask around. A missing actor means a SYSTEM action — onboarding
 * seeding, a worker — which is a real answer and is said rather than left blank.
 */
export function RoleHistoryDialog({
  userId,
  displayName,
  open,
  onOpenChange,
}: {
  userId: string;
  displayName: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const q = useQuery({
    queryKey: ["role-history", userId],
    queryFn: () => listRoleHistory(userId),
    // Only when the dialog is open: this is an audit read, not something every row
    // on the Users page should fetch on render.
    enabled: open,
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[80vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <History className="size-4" aria-hidden />
            Role history
          </DialogTitle>
          <DialogDescription>
            Every role change for {displayName}, newest first.
          </DialogDescription>
        </DialogHeader>

        {q.isLoading && <LoadingState variant="list" rows={3} />}

        {q.isError && (
          <ApiErrorState
            title="Couldn't load the role history"
            description={q.error instanceof Error ? q.error.message : undefined}
            onRetry={() => q.refetch()}
          />
        )}

        {q.data && q.data.length === 0 && (
          <p className="text-muted-foreground py-6 text-center text-[12.5px]">
            No role changes recorded for {displayName}.
          </p>
        )}

        {q.data && q.data.length > 0 && (
          <ol className="space-y-2">
            {q.data.map((e, i) => (
              <li
                key={`${e.at}-${i}`}
                className="border-line-soft rounded-lg border px-3 py-2.5"
              >
                <div className="flex flex-wrap items-center gap-2">
                  {e.kind === "changed" ? (
                    <>
                      <Badge variant="outline" className="font-mono text-[10px]">
                        {roleLabel(e.from)}
                      </Badge>
                      <ArrowRight className="text-muted-foreground size-3" aria-hidden />
                      <Badge className="font-mono text-[10px]">{roleLabel(e.to)}</Badge>
                    </>
                  ) : e.kind === "granted" ? (
                    <>
                      <span className="text-muted-foreground text-[12px]">Given</span>
                      <Badge className="font-mono text-[10px]">{roleLabel(e.to)}</Badge>
                    </>
                  ) : (
                    <>
                      <span className="text-muted-foreground text-[12px]">Removed</span>
                      <Badge variant="outline" className="font-mono text-[10px]">
                        {roleLabel(e.from)}
                      </Badge>
                    </>
                  )}
                  {e.scopeName && (
                    <span className="text-muted-foreground text-[11.5px]">
                      in {e.scopeName}
                    </span>
                  )}
                </div>
                <p className="text-muted-foreground/80 mt-1 font-mono text-[10.5px]">
                  {e.at ? new Date(e.at).toLocaleString() : "unknown time"}
                  {" · "}
                  {/* A missing actor is a system action, not a gap. */}
                  {e.actorEmail ?? (e.actorId ? e.actorId : "system")}
                </p>
              </li>
            ))}
          </ol>
        )}
      </DialogContent>
    </Dialog>
  );
}
