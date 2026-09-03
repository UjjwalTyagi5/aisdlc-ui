"use client";

import * as React from "react";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  Boxes, CheckCircle2, FileCode2, GitBranch, GitPullRequest, MessageSquare,
  Rocket, ScrollText, ShieldCheck, ShieldAlert, Sparkles,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";
import { AgentChatDrawer } from "@/components/app/agent-chat-drawer";
import { CodeViewer } from "@/components/app/code-viewer";
import { DeployTargetDialog } from "@/components/app/deploy-target-dialog";
import { DeploymentApprovals } from "@/components/app/deployment-approvals";
import { ModelSelector } from "@/components/app/model-selector";
import { RequireRole } from "@/components/auth/require-role";
import { useAgentChat } from "@/hooks/use-agent-chat";
import { useSession } from "@/hooks/use-session";
import { getProject } from "@/lib/api/projects";
import { getRelease } from "@/lib/api/deployment";
import { qk } from "@/lib/api/query-keys";
import type { PrepareDeployResult, DeploymentArtifact } from "@/lib/schemas/deployment";
import type { ProjectId } from "@/lib/schemas";

type Tab = "readiness" | "artifacts" | "runbooks" | "compliance" | "deployments";

const RISK: Record<string, string> = {
  critical: "bg-destructive/15 text-destructive border-destructive/30",
  high: "bg-orange-500/15 text-orange-500 border-orange-500/30",
  medium: "bg-warning/15 text-warning border-warning/30",
  low: "bg-sky-500/15 text-sky-500 border-sky-500/30",
  none: "bg-success/15 text-success border-success/30",
};
const DECISION: Record<string, { label: string; cls: string }> = {
  go: { label: "GO", cls: "bg-success/15 text-success border-success/30" },
  no_go: { label: "NO-GO", cls: "bg-destructive/15 text-destructive border-destructive/30" },
  conditional: { label: "Conditional", cls: "bg-warning/15 text-warning border-warning/30" },
};
const DEPLOY_VIA_LABEL: Record<string, string> = {
  azure_pipelines: "Azure Pipelines", github_actions: "GitHub Actions", argocd: "Argo CD", unknown: "—",
};

export default function DeploymentPage() {
  const params = useParams<{ id: string }>();
  const id = params.id as ProjectId;
  useSession({ required: true });

  const projectQ = useQuery({ queryKey: qk.projects.detail(id), queryFn: () => getProject(id) });

  const [prepared, setPrepared] = React.useState<PrepareDeployResult | null>(null);
  const [pickerOpen, setPickerOpen] = React.useState(false);
  const [chatOpen, setChatOpen] = React.useState(false);
  const [tab, setTab] = React.useState<Tab>("readiness");
  const [agentModel, setAgentModel] = React.useState<string>();

  const chat = useAgentChat({
    agent: "deployment",
    projectId: id,
    sessionKey: id,
    context: { page: "Deployment", project_id: id },
  });

  const releaseQ = useQuery({
    queryKey: qk.deployment.release(id, chat.sessionId ?? ""),
    queryFn: () => getRelease(id, chat.sessionId ?? ""),
    enabled: !!prepared && !!chat.sessionId,
    refetchInterval: chat.busy ? 4000 : false,
  });
  const prevBusy = React.useRef(chat.busy);
  React.useEffect(() => {
    if (prevBusy.current && !chat.busy) releaseQ.refetch();
    prevBusy.current = chat.busy;
  }, [chat.busy, releaseQ]);

  const onPrepared = (r: PrepareDeployResult) => {
    setPrepared(r);
    setTab("readiness");
    void chat.send(
      `Assess deployment readiness and generate the deployment package for ${r.repo_name} @ ${r.branch} targeting ${r.environment} (deploy via ${r.deploy_via}).`,
    );
  };
  const openPr = () => {
    setChatOpen(false);
    void chat.send("Open the deployment PR now with the staged files.");
  };

  if (projectQ.isLoading) return <div className="w-full p-4 md:px-10 md:py-8"><LoadingState variant="card" /></div>;
  if (projectQ.isError || !projectQ.data)
    return <div className="w-full p-4 md:px-10 md:py-8"><ErrorState title="Project not found"
      description={projectQ.error instanceof Error ? projectQ.error.message : "Unknown error."} onRetry={() => projectQ.refetch()} /></div>;

  const rel: DeploymentArtifact | null = releaseQ.data?.release ?? null;
  const ctx = rel?.context;
  const targetChip = prepared
    ? `${prepared.repo_name} @ ${prepared.branch}`
    : ctx ? `${ctx.repo_name} @ ${ctx.source_branch}` : null;
  const deployVia = prepared?.deploy_via ?? ctx?.deploy_via ?? "unknown";

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b px-4 py-3 md:px-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-xl font-semibold tracking-tight">Deployment</h1>
            <p className="text-muted-foreground text-xs">
              {targetChip ? (
                <span className="inline-flex flex-wrap items-center gap-1">
                  <GitBranch className="size-3" aria-hidden />
                  <span className="font-mono">{targetChip}</span>
                  <span className="opacity-50">·</span>
                  <Rocket className="size-3" aria-hidden />
                  <span>Deploying via {DEPLOY_VIA_LABEL[deployVia] ?? deployVia}</span>
                  {prepared && <><span className="opacity-50">·</span><span>{prepared.environment}</span></>}
                </span>
              ) : <span>Assess release readiness &amp; generate a deployment PR.</span>}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <ModelSelector
              aria-label="Deployment agent model"
              projectId={id}
              value={agentModel}
              onValueChange={setAgentModel}
            />
            <Button variant="outline" size="sm" onClick={() => setPickerOpen(true)}>
              <Rocket className="size-4" aria-hidden />{prepared ? "New deployment" : "Set up deployment"}
            </Button>
            <Button variant="outline" size="sm" onClick={() => setChatOpen(true)}>
              <MessageSquare className="size-4" aria-hidden />Chat
            </Button>
          </div>
        </div>
      </div>

      {!prepared ? (
        <div className="flex-1 overflow-auto">
          <div className="mx-auto max-w-3xl px-4 py-12">
            <div className="mx-auto max-w-xl">
              <EmptyState icon={Rocket} title="No deployment yet"
                description="Set up a deployment: pick a branch/PR + environment. The agent detects your deploy connector, generates the package, assesses readiness, and prepares a deployment PR."
                action={<Button onClick={() => setPickerOpen(true)}><Rocket className="size-4" aria-hidden />Set up deployment</Button>} />
            </div>
            {/* Anything already waiting on an approver is waiting whether or not this
                browser has prepared a target. Hiding it behind the empty state would
                hide the one thing on this page that needs somebody to act. */}
            <div className="mt-10">
              <h2 className="mb-3 text-sm font-medium">Deployment requests</h2>
              <DeploymentApprovals projectId={id} />
            </div>
          </div>
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex items-center gap-1 border-b px-2 py-1.5">
            <TabBtn active={tab === "readiness"} onClick={() => setTab("readiness")} icon={ShieldCheck}>Readiness</TabBtn>
            <TabBtn active={tab === "artifacts"} onClick={() => setTab("artifacts")} icon={FileCode2}>
              Artifacts{rel && rel.generated_files.length > 0 && <Count n={rel.generated_files.length} />}
            </TabBtn>
            <TabBtn active={tab === "runbooks"} onClick={() => setTab("runbooks")} icon={ScrollText}>Runbooks</TabBtn>
            <TabBtn active={tab === "compliance"} onClick={() => setTab("compliance")} icon={Boxes}>Compliance</TabBtn>
            <TabBtn active={tab === "deployments"} onClick={() => setTab("deployments")} icon={ShieldAlert}>Deployments</TabBtn>
            {rel?.pr_url && (
              <a className="ml-auto" href={rel.pr_url} target="_blank" rel="noreferrer">
                <Button variant="outline" size="sm"><GitPullRequest className="size-4" aria-hidden />View deployment PR</Button>
              </a>
            )}
          </div>
          <div className="min-h-0 flex-1 overflow-auto">
            {tab === "deployments" ? (
              <div className="p-4"><DeploymentApprovals projectId={id} /></div>
            ) : !rel && chat.busy ? (
              <div className="mx-auto max-w-xl px-4 py-12"><EmptyState icon={Sparkles} title="Assessing…"
                description="Cloning, detecting the connector, generating the deployment package, and scoring release risk. This takes a moment." variant="plain" /></div>
            ) : !rel ? (
              <div className="mx-auto max-w-xl px-4 py-12"><EmptyState icon={ShieldCheck} title="Preparing…"
                description="The agent is starting the assessment." variant="plain" /></div>
            ) : tab === "readiness" ? (
              <ReadinessView rel={rel} />
            ) : tab === "artifacts" ? (
              <ArtifactsView rel={rel} busy={chat.busy} onOpenPr={openPr} />
            ) : tab === "runbooks" ? (
              <RunbooksView rel={rel} />
            ) : (
              <ComplianceView rel={rel} />
            )}
          </div>
        </div>
      )}

      <DeployTargetDialog open={pickerOpen} onOpenChange={setPickerOpen} projectId={id} onPrepared={onPrepared} />

      <AgentChatDrawer
        open={chatOpen}
        onOpenChange={setChatOpen}
        context={{ page: "Deployment", artifactTitle: targetChip ?? undefined }}
        messages={chat.messages}
        onSend={chat.send}
        busy={chat.busy}
        sessions={chat.sessions}
        activeSessionId={chat.sessionId}
        onSelectSession={chat.selectSession}
        onNewChat={chat.newChat}
        attachments={chat.attachments}
        onAttachFiles={chat.attachFiles}
        onRemoveAttachment={chat.removeAttachment}
        disabledReason={prepared ? undefined : "Set up a deployment first."}
        starterSuggestions={["Is this branch safe to deploy to production?", "Generate a rollback runbook.", "Open the deployment PR now."]}
      />
    </div>
  );
}

function TabBtn({ active, onClick, icon: Icon, children }: { active: boolean; onClick: () => void; icon: React.ComponentType<{ className?: string }>; children: React.ReactNode }) {
  return <button type="button" onClick={onClick}
    className={cn("flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors", active ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:bg-accent/50")}>
    <Icon className="size-4" aria-hidden />{children}</button>;
}
function Count({ n }: { n: number }) { return <span className="bg-muted text-muted-foreground ml-1 rounded-full px-1.5 text-[10px]">{n}</span>; }
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="space-y-2"><h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{title}</h3>{children}</section>;
}
function Prose({ children }: { children: React.ReactNode }) {
  return <div className="rounded-lg border bg-surface-1 p-4 text-sm leading-relaxed whitespace-pre-wrap">{children}</div>;
}

function ReadinessView({ rel }: { rel: DeploymentArtifact }) {
  const dec = DECISION[rel.release_decision];
  return (
    <div className="mx-auto max-w-3xl space-y-6 p-4 md:p-6">
      <div className="flex flex-wrap items-center gap-3">
        <Badge variant="outline" className={cn("border px-3 py-1 text-sm", dec?.cls)}>Decision: {dec?.label ?? rel.release_decision}</Badge>
        <Badge variant="outline" className={cn("border px-3 py-1 text-sm", RISK[rel.risk_score])}>{rel.risk_score} risk</Badge>
        <Badge variant="outline" className="px-3 py-1 text-sm capitalize">{rel.readiness}</Badge>
      </div>
      {rel.release_justification && <p className="text-muted-foreground text-sm">{rel.release_justification}</p>}
      {rel.gate_summary.length > 0 && (
        <Section title="Gate summary">
          <ul className="space-y-1.5">
            {rel.gate_summary.map((g, i) => (
              <li key={i} className="flex items-center gap-2 text-sm">
                <Badge variant="outline" className={cn("text-[10px]", g.status === "pass" && "border-success/30 text-success", g.status === "fail" && "border-destructive/30 text-destructive")}>{g.status}</Badge>
                <span className="font-medium">{g.name}</span>
                {g.note && <span className="text-muted-foreground truncate">— {g.note}</span>}
              </li>
            ))}
          </ul>
        </Section>
      )}
      {rel.risk_rationale && <Section title="Risk rationale"><Prose>{rel.risk_rationale}</Prose></Section>}
      {rel.summary && <Section title="Summary"><Prose>{rel.summary}</Prose></Section>}
    </div>
  );
}

function ArtifactsView({ rel, busy, onOpenPr }: { rel: DeploymentArtifact; busy: boolean; onOpenPr: () => void }) {
  const [sel, setSel] = React.useState<string | null>(rel.generated_files[0]?.path ?? null);
  const file = rel.generated_files.find((f) => f.path === sel) ?? rel.generated_files[0] ?? null;
  if (rel.generated_files.length === 0)
    return <div className="mx-auto max-w-xl px-4 py-12"><EmptyState icon={FileCode2} title="No files generated" description="The agent didn't stage deployment files for this run." variant="plain" /></div>;
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between gap-2 border-b px-3 py-2">
        <span className="text-muted-foreground text-xs">{rel.generated_files.length} generated file(s) — staged for the deployment PR</span>
        {rel.pr_url ? (
          <span className="text-success inline-flex items-center gap-1.5 text-xs"><CheckCircle2 className="size-4" aria-hidden />PR opened</span>
        ) : (
          <RequireRole capability="run:trigger" fallback={<Button size="sm" disabled>Open deployment PR</Button>}>
            <Button size="sm" onClick={onOpenPr} disabled={busy}><GitPullRequest className="size-4" aria-hidden />Open deployment PR</Button>
          </RequireRole>
        )}
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-[260px_1fr] overflow-hidden">
        <aside className="min-h-0 overflow-auto border-r p-2">
          {rel.generated_files.map((f) => (
            <button key={f.path} type="button" onClick={() => setSel(f.path)}
              className={cn("flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left font-mono text-[11px] transition-colors", f.path === sel ? "bg-accent text-accent-foreground" : "hover:bg-accent/50")}>
              <FileCode2 className="size-3.5 shrink-0 opacity-60" aria-hidden /><span className="truncate">{f.path}</span>
            </button>
          ))}
        </aside>
        <div className="min-h-0 overflow-hidden">
          {file && <CodeViewer content={file.contents} filename={file.path} />}
        </div>
      </div>
    </div>
  );
}

function RunbooksView({ rel }: { rel: DeploymentArtifact }) {
  return (
    <div className="mx-auto max-w-3xl space-y-6 p-4 md:p-6">
      <Section title="Deploy runbook"><Prose>{rel.deploy_runbook || "—"}</Prose></Section>
      <Section title="Rollback runbook"><Prose>{rel.rollback_runbook || "—"}</Prose></Section>
    </div>
  );
}

function ComplianceView({ rel }: { rel: DeploymentArtifact }) {
  const ce = rel.compliance_evidence;
  return (
    <div className="mx-auto max-w-3xl space-y-6 p-4 md:p-6">
      <Section title="Compliance evidence">
        <div className="space-y-2 rounded-lg border bg-surface-1 p-4 text-sm">
          {ce.captured_at && <p className="text-muted-foreground text-xs">Captured {new Date(ce.captured_at).toLocaleString()}</p>}
          {ce.gate_approvals.length > 0 && <p><span className="font-medium">Gate approvals:</span> {ce.gate_approvals.join(", ")}</p>}
          {ce.test_summary && <p><span className="font-medium">Tests:</span> {ce.test_summary}</p>}
          {ce.security_summary && <p><span className="font-medium">Security:</span> {ce.security_summary}</p>}
          <p><span className="font-medium">SBOM present:</span> {ce.sbom_present ? "yes" : "no"}</p>
          {ce.notes && <p className="text-muted-foreground">{ce.notes}</p>}
        </div>
      </Section>
      {rel.iac_findings.length > 0 && (
        <Section title="IaC findings">
          <ul className="space-y-2">
            {rel.iac_findings.map((f, i) => (
              <li key={i} className="rounded-lg border bg-surface-1 p-3 text-sm">
                <div className="flex items-center gap-2">
                  <Badge variant="outline" className={cn("text-[10px]", RISK[f.severity])}>{f.severity}</Badge>
                  <span className="font-mono text-xs">{f.file}</span>
                </div>
                <p className="mt-1">{f.description}</p>
                {f.remediation && <p className="text-muted-foreground text-xs">Fix: {f.remediation}</p>}
              </li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  );
}
