"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  AlertCircle,
  Boxes,
  Eye,
  Info,
  Loader2,
  Lock,
  Pencil,
  Plus,
  Save,
  Send,
  Trash2,
} from "lucide-react";

import { cn } from "@/lib/utils";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { EmptyState } from "@/components/ui/empty-state";
import { ApiErrorState } from "@/components/feedback/api-error-state";
import { MarkdownMessage } from "@/components/app/markdown-message";
import {
  createAgentSkill,
  deleteAgentSkill,
  getAgentSkill,
  listAgentSkills,
  proposeAgentSkill,
  toggleAgentSkill,
  updateAgentSkill,
} from "@/lib/api/agent-skills";
import { getLintViolations } from "@/lib/api/agent-profiles";
import { qk } from "@/lib/api/query-keys";
import type { LintViolation } from "@/lib/schemas/agent-profiles";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";
import {
  SKILL_CAPS,
  SKILL_KEY_PATTERN,
  type SkillList,
  type SkillListItem,
  type SkillScope,
} from "@/lib/schemas/agent-skills";

import type { ScopeContext } from "./agent-editor";
import { InstructionSlot } from "./instruction-slot";

/** Brand-gradient commit button — the app's primary-commit idiom (see behavior-tab). */
const PRIMARY_CTA =
  "from-brand-gradient-from to-brand-gradient-to bg-gradient-to-br font-semibold text-white shadow-[0_4px_12px_-4px_oklch(0.6_0.2_35_/_0.5)] transition-shadow hover:shadow-[0_8px_20px_-6px_oklch(0.6_0.2_35_/_0.65)]";

const SCOPE_TIER_LABEL: Record<string, string> = {
  org: "Organization",
  workspace: BUSINESS_UNIT_LABEL,
  project: "Project",
  user: "you",
};

/** Skills whose engine doesn't yet accept authored/custom skills in the UI. */
const CUSTOM_SKILLS_UNSUPPORTED = new Set<string>(["testing"]);

function groupViolations(
  violations: LintViolation[],
): Record<string, LintViolation[]> {
  const byField: Record<string, LintViolation[]> = {};
  for (const v of violations) (byField[v.field] ??= []).push(v);
  return byField;
}

/** Lowercase slug derived from a display name — the create-dialog default key. */
function deriveKey(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64);
}

export interface SkillsTabProps {
  agentId: string;
  agentLabel: string;
  scopeContext: ScopeContext;
}

/** `"create"`'s `seed` is set only for an Override (a create-at-this-tier copying
 *  an inherited item) — `fromScope` names the ancestor tier it was copied from, for
 *  the dialog's explanatory copy. A plain "New skill" click sets `mode: "create"`
 *  with no `seed`. */
type EditorState =
  | {
      mode: "create";
      seed?: {
        skill_key: string;
        display_name: string;
        description: string;
        when_to_use: string;
        fromScope: string;
      };
    }
  | { mode: "edit"; skill: SkillListItem }
  | null;

/**
 * Cascade-tier skills manager for one agent (Org/Business Unit/Project — see
 * ScopeContext; Personal tier is sub-project 2). Two groups: locked vendor
 * "Built-in" skills (view + toggle) and authored custom skills for the current
 * tier OR inherited from an ancestor tier (origin_scope on each item — see
 * list_skills_merged). Parent remounts this per agent (key=agentId), so
 * dialog/toggle state resets cleanly when switching agents.
 */
export function SkillsTab({ agentId, agentLabel, scopeContext }: SkillsTabProps) {
  const { scope, scopeId, chain, isOwner, canPropose, scopeLabel, ownerRoleLabel } = scopeContext;
  const queryClient = useQueryClient();
  const listKey = qk.agentSkills.list(agentId, scope, scopeId);
  const authoringDisabled = CUSTOM_SKILLS_UNSUPPORTED.has(agentId);
  const canManage = isOwner;
  // A non-owner with propose-eligibility may author an update to an EXISTING,
  // non-inherited custom skill (it lands inactive server-side — see
  // agent_skills.py's update_skill `activate=owns`) and then send it for the
  // owner's approval via Propose. Deliberately NOT extended to creating a
  // brand-new skill_key here: list_skills_merged only surfaces a key's ACTIVE
  // row, so a non-owner's brand-new inactive draft would have no visible row to
  // attach a Propose action to after creating it — a real gap, left for
  // follow-up rather than shipping a "create it, then it vanishes" UX. Delete/
  // toggle stay owner-only below: both take effect immediately server-side, with
  // no draft/propose form of either (Critical #3 fix in agent_skills.py).
  const canProposeChange = !canManage && canPropose;
  const tierNoun = SCOPE_TIER_LABEL[scope] ?? scopeLabel;

  const [editor, setEditor] = React.useState<EditorState>(null);
  const [viewing, setViewing] = React.useState<SkillListItem | null>(null);
  const [deleting, setDeleting] = React.useState<SkillListItem | null>(null);

  const skillsQ = useQuery({
    queryKey: listKey,
    queryFn: () =>
      listAgentSkills(agentId, scope, scopeId, {
        workspaceId: chain.workspaceId,
        projectId: chain.projectId,
      }),
    enabled: scopeId !== null || scope === "org",
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: listKey });

  // ── Optimistic enable/disable toggle ───────────────────────────────────────
  const toggleMut = useMutation({
    mutationFn: (v: { skill: SkillListItem; enabled: boolean }) =>
      toggleAgentSkill({
        agent_id: agentId,
        scope,
        scope_id: scopeId,
        origin: v.skill.origin,
        skill_key: v.skill.skill_key,
        enabled: v.enabled,
        workspace_id: chain.workspaceId,
        project_id: chain.projectId,
      }),
    onMutate: async (v) => {
      await queryClient.cancelQueries({ queryKey: listKey });
      const prev = queryClient.getQueryData<SkillList>(listKey);
      queryClient.setQueryData<SkillList>(listKey, (old) =>
        old
          ? {
              skills: old.skills.map((s) =>
                s.origin === v.skill.origin && s.skill_key === v.skill.skill_key
                  ? { ...s, enabled: v.enabled }
                  : s,
              ),
            }
          : old,
      );
      return { prev };
    },
    onError: (err, _v, ctx) => {
      if (ctx?.prev) queryClient.setQueryData(listKey, ctx.prev);
      toast.error("Couldn't update skill", {
        description: err instanceof Error ? err.message : undefined,
      });
    },
    onSettled: () => invalidate(),
  });

  // ── Propose (non-owner filing a change for the owner's approval) ──────────
  // No optimistic update: proposing files a governance request, it doesn't
  // change the skill's visible state, so there's nothing to reconcile.
  const proposeMut = useMutation({
    mutationFn: (skill: SkillListItem) =>
      proposeAgentSkill(skill.skill_key, {
        agent_id: agentId,
        scope,
        scope_id: scopeId,
      }),
    onSuccess: (_r, skill) => {
      toast.success("Sent for approval", {
        description: `${ownerRoleLabel ?? "The owner"} needs to approve this change to "${skill.display_name}" at ${scopeLabel}.`,
      });
    },
    onError: (err) =>
      toast.error("Couldn't send for approval", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  const deleteMut = useMutation({
    mutationFn: (skill: SkillListItem) =>
      deleteAgentSkill(skill.skill_key, agentId, scope, scopeId),
    onSuccess: (_r, skill) => {
      setDeleting(null);
      invalidate();
      toast.success(`Removed "${skill.display_name}"`, {
        description: `No longer available to the ${agentLabel} agent in ${scopeLabel}.`,
      });
    },
    onError: (err) =>
      toast.error("Couldn't remove skill", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  const skills = skillsQ.data?.skills ?? [];
  const vendorSkills = skills.filter((s) => s.origin === "vendor");
  const customSkills = skills.filter((s) => s.origin === "custom");

  const togglingKey =
    toggleMut.isPending && toggleMut.variables
      ? `${toggleMut.variables.skill.origin}:${toggleMut.variables.skill.skill_key}`
      : null;

  const renderSwitch = (skill: SkillListItem) => (
    <Switch
      checked={skill.enabled}
      disabled={!canManage || togglingKey === `${skill.origin}:${skill.skill_key}`}
      onCheckedChange={(enabled) => toggleMut.mutate({ skill, enabled })}
      aria-label={`${skill.enabled ? "Disable" : "Enable"} ${skill.display_name}`}
    />
  );

  return (
    <div className="space-y-6">
      {!canManage && (
        <p className="text-muted-foreground flex items-center gap-1.5 text-xs">
          <Lock className="size-3" aria-hidden />
          You have read-only access to this tier.
        </p>
      )}

      {skillsQ.isError ? (
        <ApiErrorState
          title="Couldn't load skills"
          error={
            skillsQ.error && "code" in skillsQ.error && "message" in skillsQ.error
              ? (skillsQ.error as { code: string; message: string; requestId?: string })
              : undefined
          }
          description={
            skillsQ.error instanceof Error ? skillsQ.error.message : undefined
          }
          onRetry={() => skillsQ.refetch()}
        />
      ) : skillsQ.isLoading ? (
        <SkillsSkeleton />
      ) : (
        <>
          {/* ── Built-in (vendor) ── */}
          <section aria-labelledby="builtin-heading" className="space-y-3">
            <GroupHeader id="builtin-heading" title="Built-in" count={vendorSkills.length} />

            {authoringDisabled && (
              <p className="border-line-soft text-muted-foreground flex items-start gap-1.5 rounded-lg border border-dashed px-3 py-2.5 text-xs">
                <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden />
                <span>
                  The {agentLabel} agent loads its skills through its own engine, so
                  toggles apply but authoring custom skills here arrives later. You can
                  still view each skill&apos;s contents.
                </span>
              </p>
            )}

            {vendorSkills.length === 0 ? (
              <EmptyRow>No built-in skills for the {agentLabel} agent.</EmptyRow>
            ) : (
              <ul className="divide-line-soft border-line-soft divide-y rounded-lg border">
                {vendorSkills.map((skill) => (
                  <SkillRow
                    key={skill.skill_key}
                    skill={skill}
                    control={renderSwitch(skill)}
                    actions={
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setViewing(skill)}
                        aria-label={`View ${skill.display_name}`}
                      >
                        <Eye className="size-3.5" aria-hidden />
                        View
                      </Button>
                    }
                  />
                ))}
              </ul>
            )}
          </section>

          {/* ── This tier's custom skills ── */}
          <section aria-labelledby="custom-heading" className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <GroupHeader
                id="custom-heading"
                title={scope === "user" ? "Your personal skills" : `This ${tierNoun}`}
                count={customSkills.length}
              />
              {canManage && !authoringDisabled && (
                <Button size="sm" onClick={() => setEditor({ mode: "create" })}>
                  <Plus className="size-3.5" aria-hidden />
                  New skill
                </Button>
              )}
            </div>

            {customSkills.length === 0 ? (
              <EmptyState
                icon={Boxes}
                title={
                  scope === "user"
                    ? "No personal skills yet"
                    : `No ${tierNoun.toLowerCase()} skills yet`
                }
                description="Create one or rely on the built-ins."
                action={
                  canManage && !authoringDisabled ? (
                    <Button size="sm" onClick={() => setEditor({ mode: "create" })}>
                      <Plus className="size-3.5" aria-hidden />
                      New skill
                    </Button>
                  ) : undefined
                }
              />
            ) : (
              <ul className="divide-line-soft border-line-soft divide-y rounded-lg border">
                {customSkills.map((skill) => {
                  const inherited = skill.origin_scope !== null && skill.origin_scope !== scope;
                  return (
                    <SkillRow
                      key={skill.skill_key}
                      skill={skill}
                      originBadge={
                        inherited ? (
                          <Badge variant="outline" className="border-line-soft shrink-0 text-[10px]">
                            From {SCOPE_TIER_LABEL[skill.origin_scope!] ?? skill.origin_scope}
                          </Badge>
                        ) : undefined
                      }
                      control={renderSwitch(skill)}
                      actions={
                        canManage ? (
                          inherited ? (
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() =>
                                setEditor({
                                  mode: "create",
                                  seed: {
                                    skill_key: skill.skill_key,
                                    display_name: skill.display_name,
                                    description: skill.description ?? "",
                                    when_to_use: skill.when_to_use ?? "",
                                    fromScope: skill.origin_scope!,
                                  },
                                })
                              }
                              aria-label={`Override ${skill.display_name} at ${scopeLabel}`}
                            >
                              <Pencil className="size-3.5" aria-hidden />
                              Override
                            </Button>
                          ) : (
                            <>
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => setEditor({ mode: "edit", skill })}
                                aria-label={`Edit ${skill.display_name}`}
                              >
                                <Pencil className="size-3.5" aria-hidden />
                                Edit
                              </Button>
                              <Button
                                variant="ghost"
                                size="sm"
                                className="text-muted-foreground hover:text-destructive"
                                onClick={() => setDeleting(skill)}
                                aria-label={`Delete ${skill.display_name}`}
                              >
                                <Trash2 className="size-3.5" aria-hidden />
                              </Button>
                            </>
                          )
                        ) : canProposeChange && !inherited ? (
                          <>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => setEditor({ mode: "edit", skill })}
                              aria-label={`Edit ${skill.display_name}`}
                            >
                              <Pencil className="size-3.5" aria-hidden />
                              Edit
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => proposeMut.mutate(skill)}
                              disabled={
                                proposeMut.isPending &&
                                proposeMut.variables?.skill_key === skill.skill_key
                              }
                              aria-busy={
                                proposeMut.isPending &&
                                proposeMut.variables?.skill_key === skill.skill_key
                              }
                              aria-label={`Propose ${skill.display_name}`}
                            >
                              {proposeMut.isPending &&
                              proposeMut.variables?.skill_key === skill.skill_key ? (
                                <Loader2 className="size-3.5 animate-spin" aria-hidden />
                              ) : (
                                <Send className="size-3.5" aria-hidden />
                              )}
                              Propose
                            </Button>
                          </>
                        ) : (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => setViewing(skill)}
                            aria-label={`View ${skill.display_name}`}
                          >
                            <Eye className="size-3.5" aria-hidden />
                            View
                          </Button>
                        )
                      }
                    />
                  );
                })}
              </ul>
            )}
          </section>
        </>
      )}

      {/* Editor (create / edit) */}
      {editor && (
        <SkillEditorDialog
          key={editor.mode === "edit" ? `edit-${editor.skill.skill_key}` : "create"}
          state={editor}
          agentId={agentId}
          agentLabel={agentLabel}
          scope={scope}
          scopeId={scopeId}
          scopeLabel={scopeLabel}
          tierNoun={tierNoun}
          onClose={() => setEditor(null)}
          onSaved={() => {
            setEditor(null);
            invalidate();
          }}
        />
      )}

      {/* Read-only body viewer (built-ins / read-only custom) */}
      <SkillViewDialog
        skill={viewing}
        agentId={agentId}
        scope={scope}
        scopeId={scopeId}
        workspaceId={chain.workspaceId}
        onClose={() => setViewing(null)}
      />

      {/* Delete confirm */}
      <Dialog
        open={Boolean(deleting)}
        onOpenChange={(v) => !v && !deleteMut.isPending && setDeleting(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Remove skill?</DialogTitle>
            <DialogDescription>
              Removes &quot;{deleting?.display_name}&quot; from this {tierNoun.toLowerCase()}. This
              can&apos;t be undone from the UI.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              className="border-line-soft"
              onClick={() => setDeleting(null)}
              disabled={deleteMut.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => deleting && deleteMut.mutate(deleting)}
              disabled={deleteMut.isPending}
              aria-busy={deleteMut.isPending}
            >
              {deleteMut.isPending && (
                <Loader2 className="size-4 animate-spin" aria-hidden />
              )}
              Remove
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ───────────────────────── Group header + rows ─────────────────────────

function GroupHeader({
  id,
  title,
  count,
}: {
  id: string;
  title: string;
  count: number;
}) {
  return (
    <div className="flex items-center gap-2">
      <h3 id={id} className="text-xs font-semibold uppercase tracking-wider">
        {title}
      </h3>
      <span className="text-muted-foreground text-xs tabular-nums">{count}</span>
    </div>
  );
}

function EmptyRow({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-muted-foreground border-line-soft rounded-lg border border-dashed px-3 py-6 text-center text-xs">
      {children}
    </p>
  );
}

function SkillRow({
  skill,
  originBadge,
  control,
  actions,
}: {
  skill: SkillListItem;
  originBadge?: React.ReactNode;
  control: React.ReactNode;
  actions: React.ReactNode;
}) {
  return (
    <li className="flex items-center gap-3 px-3 py-2.5">
      <div className="min-w-0 flex-1 space-y-0.5">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium">{skill.display_name}</span>
          {skill.origin === "vendor" && (
            <Badge variant="secondary" className="shrink-0 text-[10px]">
              Built-in
            </Badge>
          )}
          {skill.runtime === "shell" && (
            <Badge variant="outline" className="border-line-soft shrink-0 text-[10px]">
              shell
            </Badge>
          )}
          {originBadge}
        </div>
        {skill.description && (
          <p className="text-muted-foreground truncate text-xs">{skill.description}</p>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-1">{actions}</div>
      <div className="shrink-0 pl-1">{control}</div>
    </li>
  );
}

function SkillsSkeleton() {
  return (
    <div className="space-y-3">
      <Skeleton className="h-4 w-24" />
      <div className="border-line-soft divide-line-soft divide-y rounded-lg border">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="flex items-center gap-3 px-3 py-3">
            <div className="flex-1 space-y-1.5">
              <Skeleton className="h-4 w-40" />
              <Skeleton className="h-3 w-64" />
            </div>
            <Skeleton className="h-5 w-9 rounded-full" />
          </div>
        ))}
      </div>
    </div>
  );
}

// ───────────────────────── Editor dialog ─────────────────────────

interface EditorFields {
  skill_key: string;
  display_name: string;
  description: string;
  when_to_use: string;
  body: string;
}

const EMPTY_FIELDS: EditorFields = {
  skill_key: "",
  display_name: "",
  description: "",
  when_to_use: "",
  body: "",
};

function SkillEditorDialog({
  state,
  agentId,
  agentLabel,
  scope,
  scopeId,
  scopeLabel,
  tierNoun,
  onClose,
  onSaved,
}: {
  state: Exclude<EditorState, null>;
  agentId: string;
  agentLabel: string;
  scope: SkillScope;
  scopeId: string | null;
  scopeLabel: string;
  tierNoun: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isEdit = state.mode === "edit";
  const editKey = isEdit ? state.skill.skill_key : null;

  const [fields, setFields] = React.useState<EditorFields>(() =>
    isEdit
      ? {
          skill_key: state.skill.skill_key,
          display_name: state.skill.display_name,
          description: state.skill.description ?? "",
          when_to_use: state.skill.when_to_use ?? "",
          body: "",
        }
      : state.mode === "create" && state.seed
        ? { ...state.seed, body: "" }
        : EMPTY_FIELDS,
  );
  const [lint, setLint] = React.useState<Record<string, LintViolation[]>>({});
  // Once the user hand-edits the key, stop auto-deriving it from the name. Locked
  // from the start for a seeded Override too (not just Edit) — an override MUST
  // keep the original skill_key, or editing the display name would silently
  // re-derive a DIFFERENT key and create a second, unrelated skill instead of
  // overriding the inherited one.
  const keyTouched = React.useRef(isEdit || (state.mode === "create" && Boolean(state.seed)));

  // Edit mode needs the body, which isn't in the list row — fetch full detail.
  const detailQ = useQuery({
    queryKey: qk.agentSkills.detail(
      "custom",
      editKey ?? "",
      agentId,
      scope,
      scopeId,
    ),
    queryFn: () => getAgentSkill("custom", editKey!, agentId, scope, scopeId),
    enabled: isEdit,
  });

  React.useEffect(() => {
    if (detailQ.data) {
      setFields({
        skill_key: detailQ.data.skill_key,
        display_name: detailQ.data.display_name,
        description: detailQ.data.description ?? "",
        when_to_use: detailQ.data.when_to_use ?? "",
        body: detailQ.data.body ?? "",
      });
    }
  }, [detailQ.data]);

  const setField = (key: keyof EditorFields, value: string) => {
    setFields((f) => ({ ...f, [key]: value }));
    setLint((l) => (l[key] ? { ...l, [key]: [] } : l));
  };

  const onNameChange = (value: string) => {
    setFields((f) => ({
      ...f,
      display_name: value,
      skill_key: keyTouched.current ? f.skill_key : deriveKey(value),
    }));
    setLint((l) => (l.display_name ? { ...l, display_name: [] } : l));
  };

  const saveMut = useMutation({
    mutationFn: () =>
      isEdit
        ? updateAgentSkill(editKey!, {
            agent_id: agentId,
            scope,
            scope_id: scopeId,
            display_name: fields.display_name,
            description: fields.description,
            when_to_use: fields.when_to_use,
            body: fields.body,
          })
        : createAgentSkill({
            agent_id: agentId,
            scope,
            scope_id: scopeId,
            skill_key: fields.skill_key,
            display_name: fields.display_name,
            description: fields.description,
            when_to_use: fields.when_to_use,
            body: fields.body,
          }),
    onSuccess: (skill) => {
      toast.success(
        isEdit ? `Saved "${skill.display_name}"` : `Created "${skill.display_name}"`,
        {
          description: isEdit
            ? `Published v${skill.active_version ?? skill.version ?? ""} for the ${agentLabel} agent in ${scopeLabel}.`
            : `Available to the ${agentLabel} agent in ${scopeLabel}.`,
        },
      );
      onSaved();
    },
    onError: (err) => {
      const violations = getLintViolations(err);
      if (violations) {
        setLint(groupViolations(violations));
        toast.error("Skill has issues to fix", {
          description: "See the highlighted fields below.",
        });
        return;
      }
      toast.error(isEdit ? "Couldn't save skill" : "Couldn't create skill", {
        description: err instanceof Error ? err.message : undefined,
      });
    },
  });

  const keyValid = SKILL_KEY_PATTERN.test(fields.skill_key);
  const overCap =
    fields.display_name.length > SKILL_CAPS.display_name ||
    fields.description.length > SKILL_CAPS.description ||
    fields.when_to_use.length > SKILL_CAPS.when_to_use ||
    fields.body.length > SKILL_CAPS.body;
  const canSave =
    fields.display_name.trim().length > 0 &&
    fields.body.trim().length > 0 &&
    (isEdit || keyValid) &&
    !overCap;

  const loadingDetail = isEdit && detailQ.isLoading;
  const nextVersion = isEdit
    ? (state.skill.active_version ?? state.skill.version ?? 0) + 1
    : null;

  return (
    <Dialog
      open
      onOpenChange={(v) => !v && !saveMut.isPending && onClose()}
    >
      <DialogContent className="max-h-[88vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit skill" : "New skill"}</DialogTitle>
          <DialogDescription>
            {isEdit
              ? `Update this skill for the ${agentLabel} agent across ${scopeLabel}.`
              : `A reusable instruction pack the ${agentLabel} agent loads on demand across ${scopeLabel}.`}
          </DialogDescription>
        </DialogHeader>

        {state.mode === "create" && state.seed && (
          <p className="text-muted-foreground text-xs">
            Overriding &quot;{state.seed.display_name}&quot; from{" "}
            {SCOPE_TIER_LABEL[state.seed.fromScope] ?? "an ancestor tier"}. Its name and
            description are copied below — re-enter the instructions body for your{" "}
            {tierNoun.toLowerCase()}.
          </p>
        )}

        {isEdit && detailQ.isError ? (
          <ApiErrorState
            title="Couldn't load this skill"
            description={
              detailQ.error instanceof Error ? detailQ.error.message : undefined
            }
            onRetry={() => detailQ.refetch()}
          />
        ) : loadingDetail ? (
          <div className="space-y-4">
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-40 w-full" />
          </div>
        ) : (
          <div className="space-y-4">
            <SkillTextField
              id="skill-display-name"
              label="Display name"
              value={fields.display_name}
              cap={SKILL_CAPS.display_name}
              placeholder="Story splitting"
              onChange={onNameChange}
              violations={lint.display_name}
            />

            <div className="space-y-1.5">
              <div className="flex items-baseline justify-between gap-3">
                <Label htmlFor="skill-key" className="text-sm font-medium">
                  Skill key
                </Label>
                {!keyValid && fields.skill_key.length > 0 && !isEdit && (
                  <span className="text-destructive text-[11px]">
                    a–z, 0–9, hyphens · 2–64 chars
                  </span>
                )}
              </div>
              <Input
                id="skill-key"
                value={fields.skill_key}
                disabled={isEdit}
                onChange={(e) => {
                  keyTouched.current = true;
                  setField("skill_key", e.target.value);
                }}
                aria-invalid={!isEdit && fields.skill_key.length > 0 && !keyValid}
                className={cn(
                  "font-mono",
                  isEdit && "bg-muted/40",
                  !isEdit &&
                    fields.skill_key.length > 0 &&
                    !keyValid &&
                    "border-destructive focus-visible:ring-destructive/40",
                )}
              />
              <p className="text-muted-foreground text-xs">
                {isEdit
                  ? "The key is fixed once a skill is created."
                  : "Auto-derived from the display name; edit it if you like. Lowercase letters, numbers and hyphens."}
              </p>
              <FieldViolations violations={lint.skill_key} />
            </div>

            <InstructionSlot
              id="skill-description"
              label="Description"
              helper="A one-line summary shown in the skills list."
              value={fields.description}
              cap={SKILL_CAPS.description}
              onChange={(v) => setField("description", v)}
              violations={lint.description}
              rows={2}
            />

            <InstructionSlot
              id="skill-when-to-use"
              label="When to use"
              helper="Tells the agent when to reach for this skill."
              value={fields.when_to_use}
              cap={SKILL_CAPS.when_to_use}
              onChange={(v) => setField("when_to_use", v)}
              violations={lint.when_to_use}
              rows={2}
            />

            <InstructionSlot
              id="skill-body"
              label="Instructions"
              helper="The skill body — markdown. Loaded into context when the agent uses this skill."
              value={fields.body}
              cap={SKILL_CAPS.body}
              onChange={(v) => setField("body", v)}
              violations={lint.body}
              rows={10}
            />

            {isEdit && (
              <p className="text-muted-foreground text-xs">
                Saving creates version {nextVersion}.
              </p>
            )}
          </div>
        )}

        <DialogFooter>
          <Button
            variant="outline"
            className="border-line-soft"
            onClick={onClose}
            disabled={saveMut.isPending}
          >
            Cancel
          </Button>
          <Button
            className={PRIMARY_CTA}
            onClick={() => saveMut.mutate()}
            disabled={!canSave || saveMut.isPending || loadingDetail}
            aria-busy={saveMut.isPending}
          >
            {saveMut.isPending ? (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            ) : (
              <Save className="size-4" aria-hidden />
            )}
            {isEdit ? "Save changes" : "Create skill"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function SkillTextField({
  id,
  label,
  value,
  cap,
  placeholder,
  onChange,
  violations,
}: {
  id: string;
  label: string;
  value: string;
  cap: number;
  placeholder?: string;
  onChange: (value: string) => void;
  violations?: LintViolation[];
}) {
  const over = value.length > cap;
  const hasErrors = Boolean(violations && violations.length > 0);
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between gap-3">
        <Label htmlFor={id} className="text-sm font-medium">
          {label}
        </Label>
        <span
          className={cn(
            "font-mono text-[11px] tabular-nums",
            over ? "text-destructive font-semibold" : "text-muted-foreground",
          )}
          aria-live="polite"
        >
          {value.length.toLocaleString()} / {cap.toLocaleString()}
        </span>
      </div>
      <Input
        id={id}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={over || hasErrors}
        className={cn(
          (over || hasErrors) && "border-destructive focus-visible:ring-destructive/40",
        )}
      />
      <FieldViolations violations={violations} />
    </div>
  );
}

function FieldViolations({ violations }: { violations?: LintViolation[] }) {
  if (!violations || violations.length === 0) return null;
  return (
    <ul className="space-y-1">
      {violations.map((v, idx) => (
        <li
          key={`${v.code}-${idx}`}
          className="text-destructive flex items-start gap-1.5 text-xs"
        >
          <AlertCircle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <span>{v.message}</span>
        </li>
      ))}
    </ul>
  );
}

// ───────────────────────── Read-only view dialog ─────────────────────────

/** Where a skill's content actually lives, so its detail fetch resolves there
 *  instead of 404ing — an inherited skill's row is at its origin_scope, not
 *  necessarily the tier currently being viewed. `origin_scope === scope` (the
 *  common case, and the only one for vendor skills, whose origin_scope is always
 *  null) reuses `scopeId` unchanged; "org" has no id; "workspace" needs the
 *  chain's own workspace id. "project" can't occur here — nothing inherits FROM
 *  project, it's the most specific tier. */
function resolveSkillScope(
  skill: SkillListItem | null,
  scope: SkillScope,
  scopeId: string | null,
  workspaceId: string | null | undefined,
): { scope: SkillScope; scopeId: string | null } {
  const origin = skill?.origin_scope;
  if (!origin || origin === scope) return { scope, scopeId };
  if (origin === "org") return { scope: "org", scopeId: null };
  if (origin === "workspace") return { scope: "workspace", scopeId: workspaceId ?? null };
  return { scope, scopeId };
}

function SkillViewDialog({
  skill,
  agentId,
  scope,
  scopeId,
  workspaceId,
  onClose,
}: {
  skill: SkillListItem | null;
  agentId: string;
  scope: SkillScope;
  scopeId: string | null;
  workspaceId: string | null | undefined;
  onClose: () => void;
}) {
  const open = Boolean(skill);
  const resolved = resolveSkillScope(skill, scope, scopeId, workspaceId);
  const detailQ = useQuery({
    queryKey: qk.agentSkills.detail(
      skill?.origin ?? "vendor",
      skill?.skill_key ?? "",
      agentId,
      resolved.scope,
      resolved.scopeId,
    ),
    queryFn: () =>
      getAgentSkill(skill!.origin, skill!.skill_key, agentId, resolved.scope, resolved.scopeId),
    enabled: open,
  });

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {skill?.display_name}
            {skill?.origin === "vendor" && (
              <Badge variant="secondary" className="text-[10px]">
                Built-in
              </Badge>
            )}
            {skill?.runtime === "shell" && (
              <Badge variant="outline" className="border-line-soft text-[10px]">
                shell
              </Badge>
            )}
          </DialogTitle>
          {skill?.when_to_use && (
            <DialogDescription>{skill.when_to_use}</DialogDescription>
          )}
        </DialogHeader>

        {detailQ.isError ? (
          <ApiErrorState
            title="Couldn't load this skill"
            description={
              detailQ.error instanceof Error ? detailQ.error.message : undefined
            }
            onRetry={() => detailQ.refetch()}
          />
        ) : detailQ.isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-11/12" />
            <Skeleton className="h-4 w-3/4" />
          </div>
        ) : detailQ.data?.body ? (
          <div className="border-line-soft bg-muted/30 rounded-lg border p-4">
            <MarkdownMessage content={detailQ.data.body} />
          </div>
        ) : (
          <p className="text-muted-foreground text-sm italic">
            This skill has no body content.
          </p>
        )}

        <DialogFooter>
          <Button variant="outline" className="border-line-soft" onClick={onClose}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
