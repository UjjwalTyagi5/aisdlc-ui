"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { AlertTriangle, Eye, Layers, Loader2, Lock, Pencil, Plus, Star, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Skeleton } from "@/components/ui/skeleton";
import {
  deleteTechStack,
  getProjectTechStack,
  getTechStackCatalog,
  listBusinessUnitTechStacks,
  selectProjectTechStack,
  setDefaultTechStack,
} from "@/lib/api/tech-stacks";
import { qk } from "@/lib/api/query-keys";
import { SOURCE_LABEL, type TechStack, type TechStackCatalog } from "@/lib/schemas/tech-stacks";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";

import type { ScopeContext } from "./agent-editor";
import { TechStackEditor, type TechStackEditorMode } from "./tech-stack-editor";

/**
 * TECH STACK · ALL AGENTS — the one choice every agent on a project designs and builds with.
 *
 * A Business Unit offers stacks as alternatives and marks one its default; a project follows
 * that default or picks another, or keeps one of its own. Exactly one applies. The panel is
 * the same on every agent's Skills tab because the choice is the project's, not an agent's.
 * Who may change what is the backend's answer (`can_manage`) — Agent Studio's tier ownership.
 */
export function TechStackPanel({ scopeContext }: { scopeContext: ScopeContext }) {
  const catalogQ = useQuery({
    queryKey: qk.techStacks.catalog(),
    queryFn: getTechStackCatalog,
    staleTime: 10 * 60_000,
  });
  const { scope, scopeId, chain } = scopeContext;
  const projectId = scope === "project" ? scopeId : scope === "user" ? chain.projectId : null;

  let body: React.ReactNode;
  if (scope === "workspace" && scopeId) {
    body = <BusinessUnitStacks workspaceId={scopeId} where={scopeContext.scopeLabel} catalog={catalogQ.data} />;
  } else if (projectId) {
    body = (
      <ProjectStacks
        projectId={projectId}
        where={scopeContext.projectName ?? scopeContext.scopeLabel}
        catalog={catalogQ.data}
      />
    );
  } else {
    body = (
      <p className="text-muted-foreground text-sm">
        Tech stacks are offered by each {BUSINESS_UNIT_LABEL} and chosen per project. Open a{" "}
        {BUSINESS_UNIT_LABEL} or a project to see them.
      </p>
    );
  }

  return (
    <section aria-labelledby="tech-stack-heading" className="space-y-3 rounded-lg border p-4">
      <div className="flex items-center gap-2">
        <Layers className="text-primary size-4" aria-hidden />
        <h3 id="tech-stack-heading" className="text-sm font-semibold">
          Tech stack · all agents
        </h3>
      </div>
      {body}
    </section>
  );
}

function StackChips({ stack, limit }: { stack: TechStack; limit?: number }) {
  const items = Object.values(stack.categories).flat();
  const shown = limit ? items.slice(0, limit) : items;
  return (
    <div className="flex flex-wrap gap-1">
      {shown.map((item) => (
        <Badge key={item} variant="secondary" className="font-normal">
          {item}
        </Badge>
      ))}
      {limit !== undefined && items.length > limit && (
        <span className="text-muted-foreground text-xs">+{items.length - limit} more</span>
      )}
    </div>
  );
}

/** Everything in a stack, category by category — what a project admin reads before choosing. */
export function TechStackDetails({ stack, catalog }: { stack: TechStack; catalog: TechStackCatalog | undefined }) {
  const labels = new Map((catalog?.categories ?? []).map((c) => [c.id, c.label]));
  return (
    <div className="space-y-3">
      {stack.description && <p className="text-muted-foreground text-sm">{stack.description}</p>}
      <dl className="space-y-2">
        {Object.entries(stack.categories).map(([cid, items]) => (
          <div key={cid} className="grid gap-1 sm:grid-cols-[11rem_1fr]">
            <dt className="text-muted-foreground text-xs font-medium tracking-wide uppercase">{labels.get(cid) ?? cid}</dt>
            <dd className="flex flex-wrap gap-1">
              {items.map((i) => (
                <Badge key={i} variant="secondary" className="font-normal">
                  {i}
                </Badge>
              ))}
            </dd>
          </div>
        ))}
      </dl>
      {stack.notes && (
        <div>
          <p className="text-muted-foreground text-xs font-medium tracking-wide uppercase">Notes</p>
          <p className="text-sm whitespace-pre-wrap">{stack.notes}</p>
        </div>
      )}
    </div>
  );
}

function failed(err: unknown) {
  toast.error(err instanceof Error ? err.message : "That change could not be saved.");
}

function StackActions({ stack, canManage, onView, onEdit, onDelete, extra }: {
  stack: TechStack;
  canManage: boolean;
  onView: () => void;
  onEdit: () => void;
  onDelete: () => void;
  extra?: React.ReactNode;
}) {
  return (
    <div className="flex shrink-0 items-center gap-1">
      <Button size="sm" variant="ghost" className="h-8" onClick={onView} aria-label={`View ${stack.name}`}>
        <Eye className="size-4" aria-hidden />
      </Button>
      {canManage && (
        <>
          {extra}
          <Button size="sm" variant="ghost" className="h-8" onClick={onEdit} aria-label={`Edit ${stack.name}`}>
            <Pencil className="size-4" aria-hidden />
          </Button>
          <Button size="sm" variant="ghost" className="h-8" onClick={onDelete} aria-label={`Delete ${stack.name}`}>
            <Trash2 className="size-4" aria-hidden />
          </Button>
        </>
      )}
    </div>
  );
}

/** The view, editor and delete-confirmation dialogs both tiers share. */
function useStackDialogs(catalog: TechStackCatalog | undefined) {
  const queryClient = useQueryClient();
  const [viewing, setViewing] = React.useState<TechStack | null>(null);
  const [editing, setEditing] = React.useState<TechStackEditorMode | null>(null);
  const [deleting, setDeleting] = React.useState<TechStack | null>(null);
  const remove = useMutation({
    mutationFn: (s: TechStack) => deleteTechStack(s.id),
    onSuccess: (_r, s) => {
      toast.success(`Deleted ${s.name}`);
      void queryClient.invalidateQueries({ queryKey: qk.techStacks.all });
    },
    onError: failed,
    onSettled: () => setDeleting(null),
  });

  const dialogs = (
    <>
      <Dialog open={!!viewing} onOpenChange={(o) => !o && setViewing(null)}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>{viewing?.name}</DialogTitle>
            <DialogDescription>What agents design and build with when this stack is in use.</DialogDescription>
          </DialogHeader>
          {viewing && <TechStackDetails stack={viewing} catalog={catalog} />}
        </DialogContent>
      </Dialog>
      {editing && (
        <TechStackEditor
          key={editing.kind === "edit" ? editing.stack.id : "new"}
          open
          mode={editing}
          catalog={catalog}
          onOpenChange={(o) => !o && setEditing(null)}
        />
      )}
      <Dialog open={!!deleting} onOpenChange={(o) => !o && setDeleting(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete {deleting?.name}?</DialogTitle>
            <DialogDescription>
              Projects that use it will follow their {BUSINESS_UNIT_LABEL} default instead, and will be told so.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleting(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={remove.isPending}
              onClick={() => deleting && remove.mutate(deleting)}
            >
              {remove.isPending && <Loader2 className="size-4 animate-spin" aria-hidden />}
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
  return { dialogs, setViewing, setEditing, setDeleting };
}

function ReadOnlyNote() {
  return (
    <p className="text-muted-foreground flex items-center gap-1.5 text-xs">
      <Lock className="size-3" aria-hidden />
      You have read-only access to these tech stacks.
    </p>
  );
}

function LoadError({ what, error }: { what: string; error: unknown }) {
  return (
    <p role="alert" className="text-destructive text-sm">
      Couldn&apos;t load {what}. {error instanceof Error ? error.message : ""}
    </p>
  );
}

function BusinessUnitStacks({ workspaceId, where, catalog }: {
  workspaceId: string;
  where: string;
  catalog: TechStackCatalog | undefined;
}) {
  const queryClient = useQueryClient();
  const q = useQuery({
    queryKey: qk.techStacks.businessUnit(workspaceId),
    queryFn: () => listBusinessUnitTechStacks(workspaceId),
  });
  const setDefault = useMutation({
    mutationFn: ({ id, on }: { id: string; on: boolean }) => setDefaultTechStack(id, on),
    onSuccess: (s) => {
      toast.success(s.is_default ? `${s.name} is now the default` : `${s.name} is no longer the default`);
      void queryClient.invalidateQueries({ queryKey: qk.techStacks.all });
    },
    onError: failed,
  });
  const { dialogs, setViewing, setEditing, setDeleting } = useStackDialogs(catalog);

  if (q.isLoading) return <Skeleton className="h-20 w-full" />;
  if (q.isError || !q.data) return <LoadError what={`this ${BUSINESS_UNIT_LABEL}'s tech stacks`} error={q.error} />;
  const { items, can_manage: canManage } = q.data;

  return (
    <div className="space-y-3">
      <p className="text-muted-foreground text-sm">
        The stacks {where} offers its projects. The default is what a project follows until its admin picks
        another.
      </p>
      {!canManage && <ReadOnlyNote />}
      {items.length === 0 ? (
        <p className="text-muted-foreground text-sm">No tech stacks yet, so agents recommend one freely.</p>
      ) : (
        <ul className="space-y-2">
          {items.map((s) => (
            <li key={s.id} className="flex items-start justify-between gap-3 rounded-md border p-3">
              <div className="min-w-0 space-y-1.5">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{s.name}</span>
                  {s.is_default && <Badge>Default</Badge>}
                </div>
                {s.description && <p className="text-muted-foreground text-xs">{s.description}</p>}
                <StackChips stack={s} limit={8} />
              </div>
              <StackActions
                stack={s}
                canManage={canManage}
                onView={() => setViewing(s)}
                onEdit={() => setEditing({ kind: "edit", stack: s })}
                onDelete={() => setDeleting(s)}
                extra={
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-8"
                    disabled={setDefault.isPending}
                    aria-label={s.is_default ? `Remove ${s.name} as the default` : `Make ${s.name} the default`}
                    onClick={() => setDefault.mutate({ id: s.id, on: !s.is_default })}
                  >
                    <Star className={s.is_default ? "size-4 fill-current" : "size-4"} aria-hidden />
                  </Button>
                }
              />
            </li>
          ))}
        </ul>
      )}
      {canManage && (
        <Button
          size="sm"
          variant="outline"
          onClick={() => setEditing({ kind: "create", scope: "workspace", scopeId: workspaceId, where })}
        >
          <Plus className="size-4" aria-hidden />
          New tech stack
        </Button>
      )}
      {dialogs}
    </div>
  );
}

const FOLLOW_DEFAULT = "__bu_default__";

/** A small tag inside a radio's label — a <span>, since a label holds phrasing content only. */
function Tag({ children }: { children: React.ReactNode }) {
  return <span className="text-muted-foreground rounded border px-1.5 py-0.5 text-[10px] font-medium">{children}</span>;
}

function ProjectStacks({ projectId, where, catalog }: {
  projectId: string;
  where: string;
  catalog: TechStackCatalog | undefined;
}) {
  const queryClient = useQueryClient();
  const q = useQuery({ queryKey: qk.techStacks.project(projectId), queryFn: () => getProjectTechStack(projectId) });
  const select = useMutation({
    mutationFn: (id: string | null) => selectProjectTechStack(projectId, id),
    onSuccess: (view) => {
      queryClient.setQueryData(qk.techStacks.project(projectId), view);
      toast.success(
        view.effective.stack ? `Agents now follow ${view.effective.stack.name}` : "Agents now recommend a stack freely",
      );
    },
    onError: failed,
  });
  const { dialogs, setViewing, setEditing, setDeleting } = useStackDialogs(catalog);

  if (q.isLoading) return <Skeleton className="h-24 w-full" />;
  if (q.isError || !q.data) return <LoadError what="this project's tech stack" error={q.error} />;
  const view = q.data;
  const canManage = view.can_manage;
  const buDefault = view.options.business_unit.find((s) => s.is_default) ?? null;
  const locked = !canManage || select.isPending;

  const option = (s: TechStack, own: boolean) => (
    <li key={s.id} className="flex items-start justify-between gap-3 rounded-md border p-3">
      <div className="flex min-w-0 items-start gap-3">
        <RadioGroupItem value={s.id} id={`ts-opt-${s.id}`} disabled={locked} className="mt-1" />
        <div className="min-w-0 space-y-1.5">
          <Label htmlFor={`ts-opt-${s.id}`} className="flex flex-wrap items-center gap-2 font-medium">
            {s.name}
            {s.is_default && <Tag>{BUSINESS_UNIT_LABEL} default</Tag>}
            {own && <Tag>This project</Tag>}
          </Label>
          <StackChips stack={s} limit={8} />
        </div>
      </div>
      <StackActions
        stack={s}
        canManage={canManage && own}
        onView={() => setViewing(s)}
        onEdit={() => setEditing({ kind: "edit", stack: s })}
        onDelete={() => setDeleting(s)}
      />
    </li>
  );

  return (
    <div className="space-y-3">
      <p className="text-sm">
        Agents follow: <span className="font-semibold">{view.effective.stack?.name ?? "no tech stack"}</span>
        <span className="text-muted-foreground"> · {SOURCE_LABEL[view.effective.source]}</span>
      </p>
      {view.effective.warning && (
        <p
          role="alert"
          className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-sm text-amber-900 dark:text-amber-200"
        >
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
          {view.effective.warning}
        </p>
      )}
      {!canManage && <ReadOnlyNote />}
      <RadioGroup
        value={view.selection.tech_stack_id ?? FOLLOW_DEFAULT}
        onValueChange={(v) => select.mutate(v === FOLLOW_DEFAULT ? null : v)}
        aria-label="Tech stack for this project"
      >
        <ul className="space-y-2">
          <li className="flex items-start gap-3 rounded-md border p-3">
            <RadioGroupItem value={FOLLOW_DEFAULT} id="ts-opt-default" disabled={locked} className="mt-1" />
            <Label htmlFor="ts-opt-default" className="font-medium">
              Follow the {BUSINESS_UNIT_LABEL} default
              <span className="text-muted-foreground block text-xs font-normal">
                {buDefault ? buDefault.name : "None set, so agents recommend a stack freely"}
              </span>
            </Label>
          </li>
          {view.options.business_unit.map((s) => option(s, false))}
          {view.options.project.map((s) => option(s, true))}
        </ul>
      </RadioGroup>
      {canManage && (
        <Button
          size="sm"
          variant="outline"
          onClick={() => setEditing({ kind: "create", scope: "project", scopeId: projectId, where })}
        >
          <Plus className="size-4" aria-hidden />
          New project tech stack
        </Button>
      )}
      {dialogs}
    </div>
  );
}
