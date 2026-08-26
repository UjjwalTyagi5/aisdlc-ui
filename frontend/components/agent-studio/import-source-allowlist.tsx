"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { LoadingState } from "@/components/ui/loading-state";
import { EmptyState } from "@/components/ui/empty-state";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ApiErrorState } from "@/components/feedback/api-error-state";
import { PageTitle } from "@/components/app/page-title";
import { useRawSession } from "@/components/auth/session-provider";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import { createImportSource, listImportSources } from "@/lib/api/agent-skills";

/** Not exported elsewhere — this screen is the only reader/writer of this key. */
const IMPORT_SOURCES_QUERY_KEY = ["agent-skills", "import-sources"] as const;

/**
 * Org Admin allowlist management for external Skill import sources (Agent
 * Studio import + supply-chain screening, Task 7).
 *
 * Mirrors the backend's read/write split exactly (Task 4): `GET
 * /agent-skills/import-sources` sits behind the router's `artifact:view`
 * floor only — any tenant member — so the table below renders for anyone who
 * reaches this page. `POST` is Org-Admin-only (`is_org_wide`), so the add
 * form is gated on `effectivePlatformRole(session) === "org_admin"`, the same
 * check `agent-studio.tsx` already uses for its own Org-Admin-only affordances.
 *
 * Deliberately small — a table plus a two-field form, not a rich curation
 * dialog like `ProviderModelCurationDialog`. The backend only exposes list +
 * create (no edit/delete), so that is the entire surface here too.
 */
export function ImportSourceAllowlist() {
  const session = useRawSession();
  const isOrgAdmin = effectivePlatformRole(session) === "org_admin";
  const queryClient = useQueryClient();

  const sourcesQ = useQuery({
    queryKey: IMPORT_SOURCES_QUERY_KEY,
    queryFn: listImportSources,
  });

  const [label, setLabel] = React.useState("");
  const [pattern, setPattern] = React.useState("");

  const createM = useMutation({
    mutationFn: () =>
      createImportSource({ source_pattern: pattern.trim(), label: label.trim() }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: IMPORT_SOURCES_QUERY_KEY });
      setLabel("");
      setPattern("");
      toast.success("Import source added");
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "Couldn't add import source");
    },
  });

  const sources = sourcesQ.data?.sources ?? [];
  const canSubmit = label.trim().length > 0 && pattern.trim().length > 0;

  return (
    <div className="w-full space-y-6 p-4 md:px-10 md:py-8">
      <PageTitle>Import sources</PageTitle>

      <header className="space-y-1.5">
        <h2 className="font-display text-[22px] font-bold tracking-[-0.02em]">
          Import sources
        </h2>
        <p className="text-muted-foreground max-w-[620px] text-[13.5px]">
          External sources approved for importing Skills into Agent Studio
          (Skills tab → Import). Every member of the organization can see this
          list; only an Organization Admin can add to it.
        </p>
      </header>

      {isOrgAdmin && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Add a source</CardTitle>
          </CardHeader>
          <CardContent>
            <form
              className="flex flex-wrap items-end gap-3"
              onSubmit={(e) => {
                e.preventDefault();
                if (!canSubmit || createM.isPending) return;
                createM.mutate();
              }}
            >
              <div className="min-w-[180px] flex-1 space-y-1.5">
                <Label htmlFor="import-source-label">Label</Label>
                <Input
                  id="import-source-label"
                  value={label}
                  onChange={(e) => setLabel(e.target.value)}
                  placeholder="Acme"
                  autoComplete="off"
                />
              </div>
              <div className="min-w-[260px] flex-[2] space-y-1.5">
                <Label htmlFor="import-source-pattern">
                  Source pattern (URL prefix)
                </Label>
                <Input
                  id="import-source-pattern"
                  value={pattern}
                  onChange={(e) => setPattern(e.target.value)}
                  placeholder="https://github.com/acme-org/"
                  autoComplete="off"
                />
              </div>
              <Button type="submit" disabled={!canSubmit || createM.isPending}>
                Add
              </Button>
            </form>
          </CardContent>
        </Card>
      )}

      {sourcesQ.isLoading ? (
        <LoadingState variant="table" rows={4} label="Loading import sources" />
      ) : sourcesQ.isError ? (
        <ApiErrorState
          title="Couldn't load import sources"
          onRetry={() => sourcesQ.refetch()}
        />
      ) : sources.length === 0 ? (
        <EmptyState
          title="No import sources yet"
          description={
            isOrgAdmin
              ? "Add one above to let Skills be imported from it."
              : "Your Organization Admin hasn't approved any external import sources yet."
          }
        />
      ) : (
        <Card>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Label</TableHead>
                <TableHead>Pattern</TableHead>
                <TableHead>Added by</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sources.map((s) => (
                <TableRow key={s.id}>
                  <TableCell className="font-medium">{s.label}</TableCell>
                  <TableCell className="font-mono text-[12.5px]">
                    {s.source_pattern}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {s.created_by ?? "—"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}
    </div>
  );
}
