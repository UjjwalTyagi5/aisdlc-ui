"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ChevronDown,
  ChevronRight,
  Loader2,
  Lock,
  RotateCcw,
  Save,
  Send,
  Upload,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Separator } from "@/components/ui/separator";
import { ApiErrorState } from "@/components/feedback/api-error-state";
import {
  createAgentProfileDraft,
  getLintViolations,
  listAgentProfileVersions,
  previewAgentProfile,
  proposeAgentProfilePublish,
  publishAgentProfile,
  unpublishAgentProfile,
} from "@/lib/api/agent-profiles";
import { qk } from "@/lib/api/query-keys";
import type {
  AgentProfileSummaryEntry,
  AgentProfileVersion,
  LintViolation,
  ProfileScope,
} from "@/lib/schemas/agent-profiles";

import type { ScopeContext } from "./agent-editor";
import {
  CUSTOM_INSTRUCTIONS_UNSUPPORTED,
  FIELD_CAPS,
  type ProfileField,
} from "./agents";
import { CompositionPreview } from "./composition-preview";
import { InstructionSlot } from "./instruction-slot";
import { VersionHistory } from "./version-history";

/** Brand-gradient CTA — the app's primary-commit button idiom (see integrations). */
const PRIMARY_CTA =
  "from-brand-gradient-from to-brand-gradient-to bg-gradient-to-br font-semibold text-white shadow-[0_4px_12px_-4px_oklch(0.6_0.2_35_/_0.5)] transition-shadow hover:shadow-[0_8px_20px_-6px_oklch(0.6_0.2_35_/_0.65)]";

const SCOPE_TIER_LABEL: Record<ProfileScope, string> = {
  org: "the organization",
  workspace: "the business unit",
  project: "the project",
  user: "you",
};

interface Fields {
  prompt_prepend: string;
  prompt_append: string;
  output_contract_extra: string;
}

function fieldsFrom(entry: AgentProfileSummaryEntry): Fields {
  return {
    prompt_prepend: entry.active?.prompt_prepend ?? "",
    prompt_append: entry.active?.prompt_append ?? "",
    output_contract_extra: entry.active?.output_contract_extra ?? "",
  };
}

export interface BehaviorTabProps {
  agentId: string;
  agentLabel: string;
  summary: AgentProfileSummaryEntry;
  scopeContext: ScopeContext;
}

/**
 * Cascade-tier behavior editor for one agent (Org/Business Unit/Project/
 * Personal — see ScopeContext). Parent remounts this per agent+tier
 * (key=agentId-scope-scopeId), so field state safely initializes from the
 * active version. Save creates a draft (version N+1); the tier's owner
 * publishes directly, anyone else proposes it for the owner's approval (see
 * AGENT_DEFAULT_OWNER_ROLE, lib/governance.ts).
 */
export function BehaviorTab({
  agentId,
  agentLabel,
  summary,
  scopeContext,
}: BehaviorTabProps) {
  const { scope, scopeId, chain, isOwner, canPropose, ownerRoleLabel, scopeLabel } = scopeContext;
  const queryClient = useQueryClient();
  const disabled = CUSTOM_INSTRUCTIONS_UNSUPPORTED.has(agentId);
  const editable = (isOwner || canPropose) && !disabled;

  const [fields, setFields] = React.useState<Fields>(() => fieldsFrom(summary));
  const baseline = React.useRef<Fields>(fieldsFrom(summary));
  const [lint, setLint] = React.useState<Partial<Record<ProfileField, LintViolation[]>>>(
    {},
  );
  const [advancedOpen, setAdvancedOpen] = React.useState(
    () => (summary.active?.output_contract_extra ?? "").length > 0,
  );
  const [publishTarget, setPublishTarget] =
    React.useState<AgentProfileVersion | null>(null);
  const [resetOpen, setResetOpen] = React.useState(false);

  const dirty =
    fields.prompt_prepend !== baseline.current.prompt_prepend ||
    fields.prompt_append !== baseline.current.prompt_append ||
    fields.output_contract_extra !== baseline.current.output_contract_extra;

  const overCap =
    fields.prompt_prepend.length > FIELD_CAPS.prompt_prepend ||
    fields.prompt_append.length > FIELD_CAPS.prompt_append ||
    fields.output_contract_extra.length > FIELD_CAPS.output_contract_extra;

  const setField = (key: ProfileField, value: string) => {
    setFields((f) => ({ ...f, [key]: value }));
    // Clear stale server lint for this field as the user edits it.
    setLint((l) => (l[key] ? { ...l, [key]: undefined } : l));
  };

  // ── Version history ──────────────────────────────────────────────────────
  const versionsQ = useQuery({
    queryKey: qk.agentProfiles.versions(agentId, scope, scopeId),
    queryFn: () => listAgentProfileVersions(agentId, scope, scopeId),
    enabled: scopeId !== null || scope === "org",
  });
  const versions = versionsQ.data?.versions ?? [];
  const activeVersion = versions.find((v) => v.is_active) ?? null;
  // Newest version is a publishable draft when it isn't the active one.
  const topDraft = versions[0] && !versions[0].is_active ? versions[0] : null;

  // ── Composition preview (debounced) ────────────────────────────────────────
  const [debounced, setDebounced] = React.useState<Fields>(fields);
  React.useEffect(() => {
    const t = setTimeout(() => setDebounced(fields), 500);
    return () => clearTimeout(t);
  }, [fields]);

  const previewQ = useQuery({
    queryKey: [
      "agent-profiles",
      "preview",
      agentId,
      scope,
      scopeId,
      chain.workspaceId,
      chain.projectId,
      chain.userId,
      debounced.prompt_prepend,
      debounced.prompt_append,
      debounced.output_contract_extra,
    ],
    queryFn: () =>
      previewAgentProfile({
        agent_id: agentId,
        scope,
        scope_id: scopeId,
        workspace_id: chain.workspaceId,
        project_id: chain.projectId,
        user_id: chain.userId,
        prompt_prepend: debounced.prompt_prepend,
        prompt_append: debounced.prompt_append,
        output_contract_extra: debounced.output_contract_extra,
      }),
    enabled: !disabled && (scopeId !== null || scope === "org"),
    staleTime: 30_000,
  });

  // ── Mutations ──────────────────────────────────────────────────────────────
  const invalidate = () => {
    queryClient.invalidateQueries({
      queryKey: qk.agentProfiles.summary(scope, scopeId),
    });
    queryClient.invalidateQueries({
      queryKey: qk.agentProfiles.versions(agentId, scope, scopeId),
    });
  };

  const draftMut = useMutation({
    mutationFn: () =>
      createAgentProfileDraft({
        agent_id: agentId,
        scope,
        scope_id: scopeId,
        prompt_prepend: fields.prompt_prepend,
        prompt_append: fields.prompt_append,
        output_contract_extra: fields.output_contract_extra,
      }),
    onSuccess: (v) => {
      setLint({});
      // Saved content becomes the new dirty baseline so Save disables until
      // the user edits again (avoids accidental duplicate drafts).
      baseline.current = { ...fields };
      invalidate();
      toast.success(`Draft saved as v${v.version}`, {
        description: isOwner
          ? "Publish it to apply."
          : `Propose it — ${ownerRoleLabel ?? "the owner"} will need to approve.`,
      });
    },
    onError: (err) => {
      const violations = getLintViolations(err);
      if (violations) {
        const byField: Partial<Record<ProfileField, LintViolation[]>> = {};
        for (const v of violations) {
          const key = v.field as ProfileField;
          (byField[key] ??= []).push(v);
        }
        setLint(byField);
        toast.error("Draft has issues to fix", {
          description: "See the highlighted fields below.",
        });
        return;
      }
      toast.error("Couldn't save draft", {
        description: err instanceof Error ? err.message : undefined,
      });
    },
  });

  const publishMut = useMutation({
    mutationFn: (id: string) => publishAgentProfile(id),
    onSuccess: (v) => {
      setPublishTarget(null);
      invalidate();
      toast.success(`Published v${v.version}`, {
        description: `Live for the ${agentLabel} agent across ${scopeLabel}.`,
      });
    },
    onError: (err) =>
      toast.error("Couldn't publish", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  const proposeMut = useMutation({
    mutationFn: (id: string) =>
      proposeAgentProfilePublish(id, {
        scope: scope as Exclude<ProfileScope, "user">,
        agentId,
        agentLabel,
        workspaceId: scopeContext.workspaceId,
        workspaceName: scopeContext.workspaceName,
        projectId: scopeContext.projectId,
        projectName: scopeContext.projectName,
      }),
    onSuccess: () => {
      setPublishTarget(null);
      invalidate();
      toast.success("Sent for approval", {
        description: `${ownerRoleLabel ?? "The owner"} needs to approve this change to ${SCOPE_TIER_LABEL[scope]}'s ${agentLabel} default.`,
      });
    },
    onError: (err) =>
      toast.error("Couldn't send for approval", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  const unpublishMut = useMutation({
    mutationFn: (id: string) => unpublishAgentProfile(id),
    onSuccess: () => {
      setResetOpen(false);
      invalidate();
      toast.success(`${agentLabel} reset to default`, {
        description: "The agent now uses its built-in behavior.",
      });
    },
    onError: (err) =>
      toast.error("Couldn't reset", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  const committing = publishMut.isPending || proposeMut.isPending;
  const publishingId = committing ? (publishMut.variables ?? proposeMut.variables ?? null) : null;

  const isRollback =
    isOwner &&
    publishTarget &&
    activeVersion &&
    publishTarget.version < activeVersion.version;

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
      {/* ── Editor column ── */}
      <div className="space-y-5">
        {!editable && (
          <p className="text-muted-foreground flex items-center gap-1.5 text-xs">
            <Lock className="size-3" aria-hidden />
            You have read-only access to this tier.
          </p>
        )}

        {editable && !isOwner && (
          <p className="border-line-soft bg-muted/30 text-muted-foreground rounded-lg border border-dashed px-3 py-2 text-xs">
            You don&apos;t own {SCOPE_TIER_LABEL[scope]}&apos;s default — saving a draft
            here lets you propose it to {ownerRoleLabel ?? "its owner"} for approval.
          </p>
        )}

        {summary.inherited_from && !activeVersion && (
          <p className="border-line-soft text-muted-foreground rounded-lg border border-dashed px-3 py-2.5 text-xs">
            No {scopeLabel} override yet — currently inheriting the{" "}
            {SCOPE_TIER_LABEL[summary.inherited_from]} default.
          </p>
        )}

        {disabled && (
          <p className="border-line-soft text-muted-foreground rounded-lg border border-dashed px-3 py-2.5 text-xs">
            The {agentLabel} agent doesn&apos;t support custom instructions yet. Its
            version history is shown below.
          </p>
        )}

        <InstructionSlot
          id="prompt-prepend"
          label="Behavior instructions"
          helper="Prepended to the agent's base prompt. Shapes how it works — e.g. 'Never run gap analysis unless asked.'"
          value={fields.prompt_prepend}
          cap={FIELD_CAPS.prompt_prepend}
          disabled={disabled}
          readOnly={!editable}
          onChange={(v) => setField("prompt_prepend", v)}
          violations={lint.prompt_prepend}
          rows={5}
        />

        <InstructionSlot
          id="prompt-append"
          label="Additional output rules"
          helper="Appended after the base prompt — final reminders and output preferences."
          value={fields.prompt_append}
          cap={FIELD_CAPS.prompt_append}
          disabled={disabled}
          readOnly={!editable}
          onChange={(v) => setField("prompt_append", v)}
          violations={lint.prompt_append}
          rows={4}
        />

        {/* Advanced disclosure */}
        <div className="border-line-soft rounded-lg border">
          <button
            type="button"
            onClick={() => setAdvancedOpen((o) => !o)}
            aria-expanded={advancedOpen}
            className="hover:bg-accent/40 flex w-full items-center gap-2 rounded-lg px-3 py-2.5 text-left text-sm font-medium transition-colors"
          >
            {advancedOpen ? (
              <ChevronDown className="text-muted-foreground size-4" aria-hidden />
            ) : (
              <ChevronRight className="text-muted-foreground size-4" aria-hidden />
            )}
            Advanced
            <span className="text-muted-foreground text-xs font-normal">
              · output contract
            </span>
          </button>
          {advancedOpen && (
            <div className="border-line-soft border-t p-3">
              <InstructionSlot
                id="output-contract-extra"
                label="Output contract extras"
                helper="Extra requirements added to the agent's output contract."
                value={fields.output_contract_extra}
                cap={FIELD_CAPS.output_contract_extra}
                disabled={disabled}
                readOnly={!editable}
                onChange={(v) => setField("output_contract_extra", v)}
                violations={lint.output_contract_extra}
                rows={4}
              />
            </div>
          )}
        </div>

        {/* Actions */}
        {editable && (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                onClick={() => draftMut.mutate()}
                disabled={!dirty || overCap || draftMut.isPending}
                aria-busy={draftMut.isPending}
              >
                {draftMut.isPending ? (
                  <Loader2 className="size-4 animate-spin" aria-hidden />
                ) : (
                  <Save className="size-4" aria-hidden />
                )}
                Save draft
              </Button>

              <Button
                className={PRIMARY_CTA}
                disabled={!topDraft || committing}
                onClick={() => topDraft && setPublishTarget(topDraft)}
              >
                {isOwner ? (
                  <Upload className="size-4" aria-hidden />
                ) : (
                  <Send className="size-4" aria-hidden />
                )}
                {isOwner ? "Publish" : `Propose to ${ownerRoleLabel ?? "owner"}`}
              </Button>

              {isOwner && activeVersion && (
                <Button
                  variant="ghost"
                  className="text-muted-foreground ml-auto"
                  onClick={() => setResetOpen(true)}
                >
                  <RotateCcw className="size-4" aria-hidden />
                  Reset to default
                </Button>
              )}
            </div>
            <p className="text-muted-foreground text-xs">
              {topDraft
                ? isOwner
                  ? `Draft v${topDraft.version} is ready. Publishing applies it to ${scopeLabel} for the ${agentLabel} agent, on new turns within about a minute.`
                  : `Draft v${topDraft.version} is ready. Proposing sends it to ${ownerRoleLabel ?? "the owner"} for approval.`
                : `Save a draft to review it, then ${isOwner ? "publish to apply it" : "propose it for approval"}.`}
            </p>
          </>
        )}

        <Separator className="opacity-60" />

        {versionsQ.isError ? (
          <ApiErrorState
            title="Couldn't load version history"
            error={
              versionsQ.error && "code" in versionsQ.error && "message" in versionsQ.error
                ? (versionsQ.error as { code: string; message: string; requestId?: string })
                : undefined
            }
            description={
              versionsQ.error instanceof Error ? versionsQ.error.message : undefined
            }
            onRetry={() => versionsQ.refetch()}
          />
        ) : (
          <VersionHistory
            versions={versions}
            activeVersion={activeVersion}
            canManage={editable && isOwner}
            onRequestPublish={(v) => setPublishTarget(v)}
            publishingId={publishingId}
          />
        )}
      </div>

      {/* ── Preview column ── */}
      {!disabled && (
        <aside className="lg:sticky lg:top-6 lg:self-start">
          <CompositionPreview
            preview={previewQ.data}
            isLoading={previewQ.isLoading}
            isFetching={previewQ.isFetching}
          />
        </aside>
      )}

      {/* Publish / propose / rollback confirm */}
      <Dialog
        open={Boolean(publishTarget)}
        onOpenChange={(v) => !v && !committing && setPublishTarget(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {isRollback
                ? `Roll back to v${publishTarget?.version}?`
                : isOwner
                  ? `Publish version ${publishTarget?.version}?`
                  : `Propose version ${publishTarget?.version}?`}
            </DialogTitle>
            <DialogDescription>
              {isRollback
                ? `The ${agentLabel} agent will revert to v${publishTarget?.version} for ${scopeLabel}, on new agent turns within about a minute. You can roll forward again anytime.`
                : isOwner
                  ? `Applies to ${scopeLabel} for the ${agentLabel} agent, on new agent turns within about a minute. You can roll back anytime.`
                  : `Sends this draft to ${ownerRoleLabel ?? "the owner"} for approval. It only takes effect for ${scopeLabel} once approved.`}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              className="border-line-soft"
              onClick={() => setPublishTarget(null)}
              disabled={committing}
            >
              Cancel
            </Button>
            <Button
              className={PRIMARY_CTA}
              onClick={() =>
                publishTarget &&
                (isOwner
                  ? publishMut.mutate(publishTarget.id)
                  : proposeMut.mutate(publishTarget.id))
              }
              disabled={committing}
              aria-busy={committing}
            >
              {committing ? (
                <Loader2 className="size-4 animate-spin" aria-hidden />
              ) : isOwner ? (
                <Upload className="size-4" aria-hidden />
              ) : (
                <Send className="size-4" aria-hidden />
              )}
              {isRollback ? "Roll back" : isOwner ? "Publish" : "Propose"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Reset confirm */}
      <Dialog
        open={resetOpen}
        onOpenChange={(v) => !v && !unpublishMut.isPending && setResetOpen(false)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reset {agentLabel} to default?</DialogTitle>
            <DialogDescription>
              The {agentLabel} agent will fall back to its built-in behavior across{" "}
              {scopeLabel}. Your saved versions are kept — you can publish one again
              later.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              className="border-line-soft"
              onClick={() => setResetOpen(false)}
              disabled={unpublishMut.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => activeVersion && unpublishMut.mutate(activeVersion.id)}
              disabled={unpublishMut.isPending || !activeVersion}
              aria-busy={unpublishMut.isPending}
            >
              {unpublishMut.isPending && (
                <Loader2 className="size-4 animate-spin" aria-hidden />
              )}
              Reset to default
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
