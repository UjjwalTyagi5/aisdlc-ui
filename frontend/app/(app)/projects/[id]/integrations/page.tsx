"use client";

import * as React from "react";
import { use } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { KeyRound, Plug, ShieldCheck, Terminal } from "lucide-react";

import { PageTitle } from "@/components/app/page-title";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
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
import { LoadingState } from "@/components/ui/loading-state";
import { ApiErrorState } from "@/components/feedback/api-error-state";
import { qk } from "@/lib/api/query-keys";
import {
  listProjectIntegrations,
  saveProjectCredential,
  type ProjectIntegration,
} from "@/lib/api/project-integrations";
import { RequestAccessButton } from "@/components/requests/request-access-button";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";

/**
 * Prompts, not prose.
 *
 * A blank description gets "we need Slack", and the approver then has to come
 * back for the stage, the scope and the reason before they can decide. Naming
 * the three questions up front turns one round trip into none — and the person
 * asking usually knows all three, they just weren't asked.
 */
const CONNECTOR_TEMPLATE = [
  "Which connector:",
  "Which agent stage needs it:",
  "Read-only, or does it need to write:",
].join("\n");

const MCP_TEMPLATE = [
  "Which server, and where it runs:",
  "What our agents would call it for:",
  "Read-only, or does it need to write:",
].join("\n");
import { ProjectAccessList } from "@/components/app/project-access-list";
import { getProject } from "@/lib/api/projects";
import { useAccessScope } from "@/hooks/use-access-scope";
import { canManageBusinessUnit, canManageProject } from "@/lib/mock/access-scope";
import type { ProjectId } from "@/lib/schemas/ids";
import { PHASE_LABEL } from "@/lib/agents";
import type { Phase } from "@/lib/schemas/enums";

/**
 * The project's own Integrations screen — where consumption actually happens.
 *
 * Everything on it is READ-ONLY except the credentials. Which integrations a
 * project has is decided above it: the organization permits a kind, a
 * {BUSINESS_UNIT_LABEL} inherits or onboards it, and the Project Admin wires
 * it to stages in Settings → Tools per stage. Re-offering that choice here
 * would be a second place for it to disagree with itself.
 *
 * What IS configured here is YOUR credential. A shared tenant token
 * authenticates the organization; the repo bot, board account or database
 * role you present authenticates you, and it is keyed on (you, project,
 * integration) — a colleague configuring the same tool in the same project
 * has their own, and neither overwrites the other. You never see theirs.
 */
export default function ProjectIntegrationsPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const [editing, setEditing] = React.useState<ProjectIntegration | null>(null);

  const q = useQuery({
    queryKey: qk.projects.integrations(id),
    queryFn: () => listProjectIntegrations(id),
  });

  // BOTH ADMIN TIERS, which is why the project's unit has to be known here. A
  // Project Admin holds a binding on the project; a Business Unit Admin holds one
  // on the unit above it and none on the project, so a `canManageProject` check
  // alone would hide the control from the very person who runs the unit.
  // `assert_can_administer_project` accepts exactly this pair server-side.
  const projectQ = useQuery({
    queryKey: qk.projects.detail(id as ProjectId),
    queryFn: () => getProject(id as ProjectId),
  });
  const { scope } = useAccessScope();
  const canManageAccess =
    scope !== null &&
    (canManageProject(scope, id) ||
      canManageBusinessUnit(scope, projectQ.data?.workspaceId ?? null));

  if (q.isLoading) {
    return (
      <div className="w-full p-4 md:px-10 md:py-8">
        <LoadingState variant="list" rows={4} />
      </div>
    );
  }

  if (q.isError) {
    return (
      <div className="w-full p-4 md:px-10 md:py-8">
        <ApiErrorState
          title="Couldn't load this project's integrations"
          description={q.error instanceof Error ? q.error.message : undefined}
          onRetry={() => q.refetch()}
        />
      </div>
    );
  }

  const items = q.data ?? [];
  const connectors = items.filter((i) => i.kind === "connector");
  const servers = items.filter((i) => i.kind === "mcp");
  const missingCredentials = items.filter(
    (i) => i.needsProjectCredential && !i.credential,
  ).length;

  return (
    <div className="w-full space-y-8 p-4 md:px-10 md:py-8">
      <header className="flex flex-col items-start gap-1">
        <PageTitle>Integrations</PageTitle>
      </header>

      {missingCredentials > 0 && (
        <Card className="border-warning/40 bg-warning/5 flex items-start gap-3 p-4">
          <KeyRound className="text-warning mt-0.5 size-4 shrink-0" aria-hidden />
          <p className="text-[13px]">
            {missingCredentials} {missingCredentials === 1 ? "integration needs" : "integrations need"}{" "}
            a credential from you before your agent runs can use{" "}
            {missingCredentials === 1 ? "it" : "them"}.
          </p>
        </Card>
      )}

      {/* THE ONE DECISION ON THIS PAGE THAT IS NOT SOMEBODY ELSE'S. Everything
          below is read-only because it was settled above this project; what a
          project may DO with an integration its unit holds is settled here.
          Plain markup rather than <Section>, which renders a fixed integration
          list and takes no children. */}
      <section className="space-y-3">
        <div className="space-y-1">
          <h2 className="font-display text-[15px] font-semibold">
            What this project may do
          </h2>
          <p className="text-muted-foreground max-w-2xl text-[12.5px]">
            Each integration this project&apos;s {BUSINESS_UNIT_LABEL.toLowerCase()} was
            granted, and how much of that grant this project gets. It can be narrowed
            here, never widened — widening is an Organization Admin&apos;s decision.
          </p>
        </div>
        <ProjectAccessList projectId={id} canManage={canManageAccess} />
      </section>

      <Section
        title="Connectors"
        icon={Plug}
        blurb={`Wired to stages by this project's Admin, from what your ${BUSINESS_UNIT_LABEL.toLowerCase()} was granted.`}
        items={connectors}
        onConfigure={setEditing}
        empty="No connector is enabled on this project yet. Your Project Admin enables them in Settings → Tools per stage."
        action={
          <RequestAccessButton
            label="Request a connector"
            prefill={{
              type: "connector_access",
              title: "New connector for this project",
              description: CONNECTOR_TEMPLATE,
              projectId: id,
            }}
          />
        }
      />

      <Section
        title="MCP servers"
        icon={Terminal}
        blurb="Tool servers this project's agents may call."
        items={servers}
        onConfigure={setEditing}
        empty="No MCP server is assigned to this project yet."
        action={
          <RequestAccessButton
            label="Request an MCP server"
            prefill={{
              type: "mcp_server",
              title: "New MCP server for this project",
              description: MCP_TEMPLATE,
              projectId: id,
            }}
          />
        }
      />

      <CredentialDialog
        projectId={id}
        integration={editing}
        onClose={() => setEditing(null)}
      />
    </div>
  );
}

function Section({
  title,
  icon: Icon,
  blurb,
  items,
  empty,
  onConfigure,
  action,
}: {
  title: string;
  icon: React.ComponentType<{ className?: string }>;
  blurb: string;
  items: ProjectIntegration[];
  empty: string;
  onConfigure: (i: ProjectIntegration) => void;
  /** The section's own "ask for one that isn't here" control. */
  action?: React.ReactNode;
}) {
  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-0.5">
          <h2 className="font-display flex items-center gap-2 text-lg font-bold tracking-[-0.015em]">
            <Icon className="size-4" aria-hidden /> {title}
          </h2>
          <p className="text-muted-foreground text-[12.5px]">{blurb}</p>
        </div>
        {action}
      </div>

      {items.length === 0 ? (
        <Card className="text-muted-foreground p-6 text-center text-sm">{empty}</Card>
      ) : (
        <ul className="space-y-2">
          {items.map((i) => (
            <li key={`${i.kind}:${i.id}`}>
              <Card className="flex flex-wrap items-start justify-between gap-3 p-4">
                <div className="min-w-0 space-y-1">
                  {/* No origin badge. It read "org-wide" on every row because
                      `origin` was hardcoded to "organization" — every
                      integration is org-level and a unit holds it by GRANT,
                      not by owning it. A label with one possible value tells
                      the reader nothing and implies a distinction the cascade
                      does not make. */}
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-[13.5px] font-medium">{i.name}</span>
                  </div>
                  {i.description && (
                    <p className="text-muted-foreground text-[12.5px]">{i.description}</p>
                  )}
                  {i.stages.length > 0 && (
                    <p className="text-muted-foreground text-[11.5px]">
                      {i.stages.map((s) => PHASE_LABEL[s as Phase] ?? s).join(" · ")}
                    </p>
                  )}
                </div>

                <div className="flex shrink-0 items-center gap-2">
                  <CredentialState integration={i} />
                  {i.needsProjectCredential && (
                    <Button variant="outline" size="sm" onClick={() => onConfigure(i)}>
                      <KeyRound className="mr-1 size-3.5" aria-hidden />
                      {i.credential ? "Update" : "Configure"}
                    </Button>
                  )}
                </div>
              </Card>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** The credential's state in words, because "no badge" and "no credential
 *  needed" look identical and mean opposite things. */
function CredentialState({ integration }: { integration: ProjectIntegration }) {
  if (!integration.needsProjectCredential) {
    return (
      <Badge variant="outline" className="text-muted-foreground gap-1 font-mono text-[10px]">
        <ShieldCheck className="size-3" aria-hidden />
        Runs on the shared key
      </Badge>
    );
  }
  if (!integration.credential) {
    return (
      <Badge variant="outline" className="border-warning/50 text-warning gap-1 font-mono text-[10px]">
        <KeyRound className="size-3" aria-hidden />
        Needs a credential
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="text-muted-foreground gap-1 font-mono text-[10px]">
      <KeyRound className="size-3" aria-hidden />
      {integration.credential.account ?? integration.credential.label}
    </Badge>
  );
}

function CredentialDialog({
  projectId,
  integration,
  onClose,
}: {
  projectId: string;
  integration: ProjectIntegration | null;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [label, setLabel] = React.useState("");
  const [account, setAccount] = React.useState("");
  const [secret, setSecret] = React.useState("");

  // Reset to the integration being edited each time the dialog opens, so a
  // second Configure never shows the previous target's values.
  React.useEffect(() => {
    setLabel(integration?.credential?.label ?? "");
    setAccount(integration?.credential?.account ?? "");
    setSecret("");
  }, [integration]);

  const save = useMutation({
    mutationFn: () =>
      saveProjectCredential(projectId, {
        kind: integration!.kind,
        targetId: integration!.id,
        label: label.trim(),
        account: account.trim() || null,
        secret: secret || undefined,
      }),
    onSuccess: () => {
      toast.success("Credential saved");
      queryClient.invalidateQueries({ queryKey: qk.projects.integrations(projectId) });
      onClose();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const isUpdate = Boolean(integration?.credential);

  return (
    <Dialog open={integration !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>
            {isUpdate ? "Update" : "Configure"} {integration?.name} credential
          </DialogTitle>
          <DialogDescription>
            How this project authenticates to {integration?.name}. The organization already
            approved the integration; this identifies your team to it.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="pic-label">Name</Label>
            <Input
              id="pic-label"
              value={label}
              placeholder="Payments delivery bot"
              onChange={(e) => setLabel(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="pic-account">Account</Label>
            <Input
              id="pic-account"
              value={account}
              placeholder="svc-payments@acme.test"
              onChange={(e) => setAccount(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="pic-secret">{isUpdate ? "New secret" : "Secret"}</Label>
            <Input
              id="pic-secret"
              type="password"
              value={secret}
              autoComplete="off"
              placeholder={isUpdate ? "Leave blank to keep the current one" : "Token or key"}
              onChange={(e) => setSecret(e.target.value)}
            />
            <p className="text-muted-foreground text-[11.5px]">
              Stored in the tenant&apos;s secrets vault and never echoed back.
            </p>
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() => save.mutate()}
            disabled={!label.trim() || save.isPending || (!isUpdate && !secret)}
          >
            {save.isPending ? "Saving…" : "Save credential"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
