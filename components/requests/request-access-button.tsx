"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Send } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  RaiseRequestDialog,
  type RaiseRequestPrefill,
} from "@/components/requests/raise-request-dialog";
import { useAccessScope } from "@/hooks/use-access-scope";
import { listProjects } from "@/lib/api/projects";
import { qk } from "@/lib/api/query-keys";
import { canRaiseType } from "@/lib/requests/routing";

/**
 * "Request access", raised from the thing it is about.
 *
 * WHY A BUTTON AND NOT A LINK TO REQUESTS & APPROVALS. Sending someone to a
 * blank form loses the one fact the page already had — WHICH model, WHICH
 * connector. They then retype it as prose, and the approver has to map that
 * prose back onto a real catalogue entry before they can decide. Raised here,
 * the subject is exact by construction.
 *
 * RENDERS NOTHING when this role may not raise this type. That includes the
 * Organization Admin, for whom the whole notion is a category error: the chain
 * ends at them, so a request they raised would have no one to decide it. They
 * grant the thing directly instead, on the same screen.
 */
export function RequestAccessButton({
  prefill,
  label = "Request access",
  size = "sm",
  variant = "outline",
  className,
}: {
  prefill: RaiseRequestPrefill;
  label?: string;
  size?: "sm" | "default";
  variant?: "outline" | "ghost" | "default";
  className?: string;
}) {
  const [open, setOpen] = React.useState(false);
  const { role } = useAccessScope();

  // The dialog's project picker. Fetched only once the dialog is opened —
  // a hundred connector tiles must not each fire a project list on mount.
  const projectsQ = useQuery({
    queryKey: qk.projects.list({ pageSize: 100 }),
    queryFn: () => listProjects({ pageSize: 100 }),
    staleTime: 60_000,
    enabled: open,
  });

  if (!canRaiseType(role, prefill.type)) return null;

  return (
    <>
      <Button
        size={size}
        variant={variant}
        className={cn("border-line-soft gap-1.5 font-mono text-[11px]", className)}
        onClick={() => setOpen(true)}
      >
        <Send className="size-3.5" aria-hidden />
        {label}
      </Button>
      <RaiseRequestDialog
        open={open}
        onOpenChange={setOpen}
        projects={projectsQ.data?.items ?? []}
        prefill={prefill}
      />
    </>
  );
}
