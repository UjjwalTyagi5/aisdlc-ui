"use client";

import * as React from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";

import { useRaiseForApproval } from "@/hooks/use-raise-for-approval";
import { listArtifacts } from "@/lib/api/artifacts";
import { qk } from "@/lib/api/query-keys";
import type { Artifact, ProjectId } from "@/lib/schemas";

/**
 * Which document the page has open in its centre, kept in the address as `?doc=<id>`.
 *
 * In the address rather than in state, so a reload keeps the document open and a copied link
 * opens it for someone else — the same way `?history=` keeps the Testing page's work and
 * `?artifact=` keeps Design's.
 */
export function useOpenDocument(param = "doc") {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const openId = searchParams.get(param);

  const set = React.useCallback((id: string | null) => {
    const qs = new URLSearchParams(searchParams.toString());
    if (id) qs.set(param, id); else qs.delete(param);
    const q = qs.toString();
    router.replace(`${pathname}${q ? `?${q}` : ""}`, { scroll: false });
  }, [param, pathname, router, searchParams]);

  const open = React.useCallback((id: string) => set(id), [set]);
  const close = React.useCallback(() => set(null), [set]);
  return { openId, open, close };
}

/**
 * EVERYTHING A PAGE NEEDS TO OPEN ITS DOCUMENTS — the one wiring every agent page shares, so a
 * row in the Documents panel behaves the same on all of them.
 *
 * The open document is shown OVER the page's own centre, which stays mounted underneath, in a
 * viewer that cannot be mistaken for the page (`DocumentPreview`) — closing it returns to
 * exactly where the work was, tabs and all.
 *
 * The rows come from the SAME query the Documents panel reads (`qk.artifacts.forProject`), so
 * resolving the open document costs no request, and an approval anywhere updates the header
 * of the open one. A `?doc=` that names a document which no longer exists (deleted, or a
 * stale link) is dropped rather than left pointing at nothing.
 */
export function useDocumentView(projectId: ProjectId) {
  const { openId, open, close } = useOpenDocument();
  const documentsQ = useQuery({
    queryKey: qk.artifacts.forProject(projectId),
    queryFn: () => listArtifacts(projectId),
  });
  const approvals = useRaiseForApproval(projectId);
  const openDoc: Artifact | null = openId ? documentsQ.data?.find((a) => a.id === openId) ?? null : null;

  React.useEffect(() => {
    if (openId && documentsQ.isSuccess && !openDoc) close();
  }, [openId, documentsQ.isSuccess, openDoc, close]);

  return {
    openId,
    openDoc,
    approvals,
    /** For a Documents panel row: open it in the centre. */
    select: React.useCallback((a: Artifact) => open(a.id), [open]),
    close,
  };
}
