"use client";

import * as React from "react";
import { use } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { CheckCircle2, KeyRound, Plug, ShieldCheck, Terminal, XCircle } from "lucide-react";

import { cn } from "@/lib/utils";
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
  setProjectIntegrationInstance,
  testProjectCredential,
  type ProjectIntegration,
  type ProjectIntegrationCredentialTestResult,
} from "@/lib/api/project-integrations";
import { RequestAccessButton } from "@/components/requests/request-access-button";

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
  const servers = items.filter((i) => i.kind === "mcp");

  // WIRED ONLY. The API returns everything the business unit was granted, which
  // put ten connectors on a page whose own blurb promises the ones this
  // project's Admin wired to stages — and asked members for credentials against
  // tools no agent run here will ever call.
  //
  // A credential saved against a connector that is later unwired does not
  // disappear; it stops being shown, and reappears if the Admin wires the tool
  // again. This page answers "what does this project use", not "what could it".
  const wiredConnectors = items.filter(
    (i) => i.kind === "connector" && i.stages.length > 0,
  );

  // Only what the viewer can actually act on: a credential this project's runs
  // would really use. Counting unwired ones sent people to configure tools no
  // stage calls.
  const missingCredentials = [...wiredConnectors, ...servers].filter(
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

      <Section
        title="Connectors"
        icon={Plug}
        blurb="Wired to stages by this project's Admin — these are what your agent runs actually call."
        items={wiredConnectors}
        onConfigure={setEditing}
        empty="No connector is wired to a stage yet. Your Project Admin enables them in Settings → Tools per stage."
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

/**
 * What each connector needs asked for, beyond the credential's own name.
 *
 * Mirrors exactly what the matching `auth_adapter()` reads on the backend
 * (backend/config/connectors/*.py): asking for a field nobody consumes trains
 * people to fill in boxes that do nothing, and omitting one the connector needs
 * produces a credential that saves cleanly and then fails at run time.
 *
 * Only the kinds that support a personal credential appear here. Everything
 * else — MCP servers, and the connectors that authenticate through a shared org
 * app — falls back to GENERIC_FIELDS below.
 */
const CREDENTIAL_FIELDS: Record<
  string,
  {
    baseUrl?: { label: string; placeholder: string };
    account?: { label: string; placeholder: string; optional?: boolean };
    secretLabel?: string;
    hint?: string;
  }
> = {
  jira: {
    baseUrl: { label: "Site URL", placeholder: "https://your-org.atlassian.net" },
    account: { label: "Account email", placeholder: "you@company.com" },
    secretLabel: "API token",
    hint: "Atlassian → Account settings → Security → Create and manage API tokens.",
  },
  confluence: {
    // NO trailing /wiki — ConfluenceConnector appends /wiki/api/v2 (and
    // /wiki/rest/api for v1) itself, so a URL that already carries it produces
    // /wiki/wiki/... and a 404 that reads like a wrong site.
    baseUrl: { label: "Site URL", placeholder: "https://your-org.atlassian.net" },
    account: { label: "Account email", placeholder: "you@company.com" },
    secretLabel: "API token",
    hint: "The same kind of token Jira uses, configured separately here. Site URL without /wiki.",
  },
  azure_devops: {
    baseUrl: { label: "Organization URL", placeholder: "https://dev.azure.com/your-org" },
    secretLabel: "Personal Access Token",
    hint: "One connection powers boards, repos and CI/CD.",
  },
  sonarqube: {
    baseUrl: { label: "Server URL", placeholder: "https://sonar.your-company.com" },
    secretLabel: "User token",
    hint: "SonarQube → My Account → Security → Generate Token.",
  },
  slack: {
    // No URL: slack.com/api is fixed, and the workspace is implied by the token
    // itself. No channel either — notify_slack requires one per call by design
    // (REQ-M6-04), so there is no default to configure here.
    account: { label: "Workspace", placeholder: "acme-eng", optional: true },
    secretLabel: "Bot token",
    hint: "api.slack.com → your app → OAuth & Permissions → Bot User OAuth Token (starts xoxb-).",
  },
  figma: {
    // No URL: api.figma.com is fixed. `account` carries the default file — a
    // share URL or a bare key; extract_file_key() on the backend accepts either.
    account: {
      // NOT an account, despite sharing the `account` slot every other connector
      // uses for one — the label says so because somebody typed their email here
      // and it stored cleanly, then surfaced a session later as "no default file is
      // configured". The backend now rejects an unparseable value outright.
      label: "Default Figma file URL (not an account)",
      placeholder: "https://www.figma.com/design/abc123/Product",
      optional: true,
    },
    secretLabel: "Personal access token",
    hint: "Figma → Settings → Security → Personal access tokens. Read-only: the Design agent reads your screens and never writes to them. Leave the file blank to pass a file URL to the agent per request.",
  },
  github: {
    // No URL: api.github.com is fixed. `account` carries owner/repo, which is
    // what every GitHub Issues call is scoped to.
    account: { label: "Owner / repo", placeholder: "acme/payments-api", optional: true },
    secretLabel: "Personal Access Token",
    hint: "Scopes: repo. Yours authenticates as you, rather than as the shared GitHub App.",
  },
  github_actions: {
    account: { label: "Owner / org", placeholder: "acme", optional: true },
    secretLabel: "Personal Access Token",
    hint: "Scopes: repo, workflow. GitHub's API host is fixed, so there is no URL to give.",
  },
};

/** For MCP servers and any connector with no per-kind form of its own. */
const GENERIC_FIELDS: (typeof CREDENTIAL_FIELDS)[string] = {
  account: { label: "Account", placeholder: "svc-payments@acme.test", optional: true },
};

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
  const [baseUrl, setBaseUrl] = React.useState("");
  const [secret, setSecret] = React.useState("");
  const [testResult, setTestResult] = React.useState<ProjectIntegrationCredentialTestResult | null>(
    null,
  );

  // Reset to the integration being edited each time the dialog opens, so a
  // second Configure never shows the previous target's values. baseUrl seeds
  // from the PROJECT's pinned instance, not from the credential — it stopped
  // being the credential's in migration 0032.
  React.useEffect(() => {
    setLabel(integration?.credential?.label ?? "");
    setAccount(integration?.credential?.account ?? "");
    setBaseUrl(integration?.baseUrl ?? "");
    setSecret("");
    setTestResult(null);
  }, [integration]);

  const spec =
    (integration?.kind === "connector" ? CREDENTIAL_FIELDS[integration.id] : undefined) ??
    GENERIC_FIELDS;

  // Everyone SEES which instance they are authenticating against — a token
  // typed blind against an unnamed server is how you send it to the wrong one.
  // Only a project administrator may CHANGE it; for everyone else the field
  // renders as read-only context.
  const canEditInstance = Boolean(integration?.canManageInstance);
  const instanceChanged = (integration?.baseUrl ?? "") !== baseUrl.trim();

  const test = useMutation({
    mutationFn: () =>
      testProjectCredential(projectId, {
        kind: integration!.kind,
        targetId: integration!.id,
        secret,
        // Only meaningful for someone who may pin the instance — the server
        // ignores it from anyone else. Sent so first-time setup can try a URL
        // before saving it, rather than probing the empty stored value.
        baseUrl: canEditInstance ? baseUrl.trim() || null : null,
        account: account.trim() || null,
      }),
    onSuccess: setTestResult,
    onError: (e: Error) => setTestResult({ ok: false, message: e.message }),
  });

  const save = useMutation({
    // Two writes, because they are two decisions with two authorities: the
    // instance is the project's (admins only) and the credential is the
    // caller's. The instance goes first — a credential saved against the old
    // URL would be briefly pointing at the wrong server.
    mutationFn: async () => {
      if (canEditInstance && spec.baseUrl && instanceChanged) {
        await setProjectIntegrationInstance(projectId, {
          kind: integration!.kind,
          targetId: integration!.id,
          baseUrl: baseUrl.trim() || null,
        });
      }
      return saveProjectCredential(projectId, {
        kind: integration!.kind,
        targetId: integration!.id,
        label: label.trim(),
        account: account.trim() || null,
        secret: secret || undefined,
      });
    },
    onSuccess: () => {
      toast.success("Credential saved");
      queryClient.invalidateQueries({ queryKey: qk.projects.integrations(projectId) });
      onClose();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const isUpdate = Boolean(integration?.credential);
  // An unpinned instance blocks only the people who could fix it. A contributor
  // staring at "no instance set" needs to be told to ask an admin, not to have
  // the Save button quietly disabled with no explanation.
  const missingRequired =
    !label.trim() ||
    (Boolean(spec.baseUrl) && canEditInstance && !baseUrl.trim()) ||
    (Boolean(spec.account) && !spec.account?.optional && !account.trim()) ||
    (!isUpdate && !secret);

  return (
    <Dialog open={integration !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>
            {isUpdate ? "Update" : "Configure"} {integration?.name} credential
          </DialogTitle>
          <DialogDescription>
            How you authenticate to {integration?.name} on this project. The organization already
            approved the integration; this identifies you to it.
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

          {spec.baseUrl && (
            <div className="space-y-2">
              <Label htmlFor="pic-base-url">{spec.baseUrl.label}</Label>
              <Input
                id="pic-base-url"
                value={baseUrl}
                autoComplete="off"
                readOnly={!canEditInstance}
                aria-readonly={!canEditInstance}
                placeholder={
                  canEditInstance ? spec.baseUrl.placeholder : "Not set for this project yet"
                }
                className={cn(!canEditInstance && "bg-muted/50 text-muted-foreground")}
                onChange={(e) => {
                  if (!canEditInstance) return;
                  setBaseUrl(e.target.value);
                  setTestResult(null);
                }}
              />
              <p className="text-muted-foreground text-[11.5px]">
                {canEditInstance ? (
                  <>
                    This project&apos;s instance — you set it for everyone here, and another
                    project can point somewhere else.
                  </>
                ) : baseUrl ? (
                  <>
                    Set for this project by its Admin. Your credential authenticates you
                    against this instance.
                  </>
                ) : (
                  <>
                    No instance set yet — ask this project&apos;s Admin to add one before
                    your credential can be used.
                  </>
                )}
              </p>
            </div>
          )}

          {spec.account && (
            <div className="space-y-2">
              <Label htmlFor="pic-account">
                {spec.account.label}
                {spec.account.optional && (
                  <span className="text-muted-foreground/60"> (optional)</span>
                )}
              </Label>
              <Input
                id="pic-account"
                value={account}
                autoComplete="off"
                placeholder={spec.account.placeholder}
                onChange={(e) => {
                  setAccount(e.target.value);
                  setTestResult(null);
                }}
              />
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="pic-secret">
              {isUpdate
                ? `New ${(spec.secretLabel ?? "secret").toLowerCase()}`
                : (spec.secretLabel ?? "Secret")}
            </Label>
            <Input
              id="pic-secret"
              type="password"
              value={secret}
              autoComplete="off"
              placeholder={isUpdate ? "Leave blank to keep the current one" : "Token or key"}
              onChange={(e) => {
                setSecret(e.target.value);
                setTestResult(null);
              }}
            />
            {spec.hint && <p className="text-muted-foreground text-[11.5px]">{spec.hint}</p>}
            <p className="text-muted-foreground text-[11.5px]">
              Yours alone — a colleague on this project keeps their own, and never sees
              this one.
            </p>
          </div>

          {secret && (
            <div className="space-y-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => test.mutate()}
                disabled={test.isPending}
              >
                {test.isPending ? "Testing…" : "Test connection"}
              </Button>
              {testResult && (
                <p
                  className={cn(
                    "flex items-center gap-1.5 text-[12.5px]",
                    testResult.ok ? "text-success" : "text-destructive",
                  )}
                >
                  {testResult.ok ? (
                    <CheckCircle2 className="size-3.5 shrink-0" aria-hidden />
                  ) : (
                    <XCircle className="size-3.5 shrink-0" aria-hidden />
                  )}
                  {testResult.message}
                </p>
              )}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => save.mutate()} disabled={missingRequired || save.isPending}>
            {save.isPending ? "Saving…" : "Save credential"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
