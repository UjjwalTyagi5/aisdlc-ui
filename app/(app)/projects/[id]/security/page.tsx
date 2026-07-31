"use client";

import * as React from "react";
import { useParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Boxes,
  Check,
  ChevronDown,
  Copy,
  GitBranch,
  GitPullRequest,
  ListChecks,
  MessageSquare,
  ScrollText,
  ShieldCheck,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { AgentChatDrawer } from "@/components/app/agent-chat-drawer";
import { ScanTargetDialog } from "@/components/app/scan-target-dialog";
import { RequireRole } from "@/components/auth/require-role";
import { useAgentChat } from "@/hooks/use-agent-chat";
import { useSession } from "@/hooks/use-session";
import { getProject } from "@/lib/api/projects";
import { listScans, getScan } from "@/lib/api/security";
import { qk } from "@/lib/api/query-keys";
import type {
  PrepareScanResult,
  Severity,
  SecurityArtifact,
} from "@/lib/schemas/security";
import type { ProjectId } from "@/lib/schemas";

type Tab = "summary" | "findings" | "sbom";

const SIGNOFF_META: Record<string, { label: string; cls: string }> = {
  pass: { label: "Pass", cls: "bg-success/15 text-success border-success/30" },
  fail: { label: "Fail", cls: "bg-destructive/15 text-destructive border-destructive/30" },
  conditional: { label: "Conditional", cls: "bg-warning/15 text-warning border-warning/30" },
};

const RISK_META: Record<string, { label: string; cls: string }> = {
  critical: { label: "Critical risk", cls: "bg-destructive/15 text-destructive border-destructive/30" },
  high: { label: "High risk", cls: "bg-orange-500/15 text-orange-500 border-orange-500/30" },
  medium: { label: "Medium risk", cls: "bg-warning/15 text-warning border-warning/30" },
  low: { label: "Low risk", cls: "bg-sky-500/15 text-sky-500 border-sky-500/30" },
  none: { label: "No risk", cls: "bg-success/15 text-success border-success/30" },
};

const SEV_META: Record<Severity, { label: string; dot: string; text: string }> = {
  critical: { label: "Critical", dot: "bg-destructive", text: "text-destructive" },
  high: { label: "High", dot: "bg-orange-500", text: "text-orange-500" },
  medium: { label: "Medium", dot: "bg-warning", text: "text-warning" },
  low: { label: "Low", dot: "bg-sky-500", text: "text-sky-500" },
  info: { label: "Info", dot: "bg-muted-foreground", text: "text-muted-foreground" },
};
const SEV_ORDER: Severity[] = ["critical", "high", "medium", "low", "info"];
const REACH_TONE: Record<string, string> = {
  reachable: "text-destructive",
  conditionally_reachable: "text-warning",
  unreachable: "text-muted-foreground",
  unknown: "text-muted-foreground",
};

export default function SecurityPage() {
  const params = useParams<{ id: string }>();
  const id = params.id as ProjectId;
  const queryClient = useQueryClient();
  useSession({ required: true });

  const projectQ = useQuery({
    queryKey: qk.projects.detail(id),
    queryFn: () => getProject(id),
  });
  const scansQ = useQuery({
    queryKey: qk.security.scans(id),
    queryFn: () => listScans(id),
  });

  const [tab, setTab] = React.useState<Tab>("summary");
  const [pickerOpen, setPickerOpen] = React.useState(false);
  const [chatOpen, setChatOpen] = React.useState(false);
  const [prepared, setPrepared] = React.useState<PrepareScanResult | null>(null);
  const [activeScanId, setActiveScanId] = React.useState<string | null>(null);

  React.useEffect(() => {
    const newest = scansQ.data?.[0]?.id;
    if (!prepared && !activeScanId && newest) setActiveScanId(newest);
  }, [scansQ.data, prepared, activeScanId]);

  const scanQ = useQuery({
    queryKey: qk.security.scan(id, activeScanId ?? ""),
    queryFn: () => getScan(id, activeScanId!),
    enabled: !!activeScanId,
  });

  const chat = useAgentChat({
    agent: "security",
    context: { page: "Security", project_id: id },
    onArtifact: () => scansQ.refetch(),
  });

  const prevBusy = React.useRef(chat.busy);
  React.useEffect(() => {
    if (prevBusy.current && !chat.busy) {
      scansQ.refetch().then((r) => {
        const newest = r.data?.[0]?.id;
        if (newest) {
          setActiveScanId(newest);
          setPrepared(null);
          setTab("summary");
          queryClient.invalidateQueries({ queryKey: qk.security.scan(id, newest) });
        }
      });
    }
    prevBusy.current = chat.busy;
  }, [chat.busy, id, queryClient, scansQ]);

  const onPrepared = (result: PrepareScanResult) => {
    setPrepared(result);
    setActiveScanId(null);
    setChatOpen(true);
    void chat.send("Please run the security scan and submit your review.");
  };
  const runScan = () => {
    setChatOpen(true);
    void chat.send("Please run the security scan and submit your review.");
  };

  if (projectQ.isLoading) {
    return (
      <div className="w-full space-y-6 p-4 md:px-10 md:py-8">
        <div className="bg-muted h-8 w-64 animate-pulse rounded" />
        <LoadingState variant="card" />
      </div>
    );
  }
  if (projectQ.isError || !projectQ.data) {
    return (
      <div className="w-full p-4 md:px-10 md:py-8">
        <ErrorState
          title="Project not found"
          description={projectQ.error instanceof Error ? projectQ.error.message : "Unknown error."}
          onRetry={() => projectQ.refetch()}
        />
      </div>
    );
  }

  const artifact = scanQ.data ?? null;
  const scans = scansQ.data ?? [];
  const ctx = artifact?.context;
  const targetChip = prepared
    ? prepared.mode === "pr"
      ? `PR #${prepared.pr_id} · ${prepared.branch}`
      : prepared.branch
    : ctx
      ? ctx.mode === "pr"
        ? `PR #${ctx.pr_id} · ${ctx.branch}`
        : ctx.branch
      : null;
  const repoName = prepared?.repo_name ?? ctx?.repo_name ?? "";
  const hasScan = !!artifact;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b px-4 py-3 md:px-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-xl font-semibold tracking-tight">Security</h1>
            <p className="text-muted-foreground text-xs">
              {targetChip ? (
                <span className="inline-flex items-center gap-1">
                  {prepared?.mode === "pr" || ctx?.mode === "pr" ? (
                    <GitPullRequest className="size-3" aria-hidden />
                  ) : (
                    <GitBranch className="size-3" aria-hidden />
                  )}
                  <span className="font-mono">{repoName}</span>
                  <span className="opacity-50">·</span>
                  <span className="font-mono">{targetChip}</span>
                </span>
              ) : (
                <span>Read-only security scan of a branch or pull request.</span>
              )}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {scans.length > 0 && (
              <ScanSwitcher
                scans={scans}
                activeId={activeScanId}
                onSelect={(sid) => {
                  setActiveScanId(sid);
                  setPrepared(null);
                  setTab("summary");
                }}
              />
            )}
            <Button variant="outline" size="sm" onClick={() => setPickerOpen(true)}>
              <ShieldCheck className="size-4" aria-hidden />
              Select target
            </Button>
            <RequireRole capability="run:trigger">
              <Button size="sm" onClick={runScan} disabled={!prepared || chat.busy}>
                <ShieldCheck className="size-4" aria-hidden />
                Run scan
              </Button>
            </RequireRole>
            <Button variant="outline" size="sm" onClick={() => setChatOpen(true)}>
              <MessageSquare className="size-4" aria-hidden />
              Chat
            </Button>
          </div>
        </div>
      </div>

      {!prepared && !hasScan && !scansQ.isLoading ? (
        <div className="flex-1 overflow-auto">
          <div className="mx-auto max-w-xl px-4 py-12">
            <EmptyState
              icon={ShieldCheck}
              title="No scan yet"
              description="Select a branch or an open PR, then run the scan. Findings, an SBOM, a risk score, and a sign-off decision appear here."
              action={
                <Button onClick={() => setPickerOpen(true)}>
                  <ShieldCheck className="size-4" aria-hidden />
                  Select target
                </Button>
              }
            />
          </div>
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex items-center gap-1 border-b px-2 py-1.5">
            <TabBtn active={tab === "summary"} onClick={() => setTab("summary")} icon={ScrollText}>
              Summary
            </TabBtn>
            <TabBtn active={tab === "findings"} onClick={() => setTab("findings")} icon={ListChecks}>
              Findings
              {artifact && artifact.findings.length > 0 && (
                <span className="bg-muted text-muted-foreground ml-1 rounded-full px-1.5 text-[10px]">
                  {artifact.findings.length}
                </span>
              )}
            </TabBtn>
            <TabBtn active={tab === "sbom"} onClick={() => setTab("sbom")} icon={Boxes}>
              SBOM
              {artifact && artifact.sbom.length > 0 && (
                <span className="bg-muted text-muted-foreground ml-1 rounded-full px-1.5 text-[10px]">
                  {artifact.sbom.length}
                </span>
              )}
            </TabBtn>
          </div>

          <div className="min-h-0 flex-1 overflow-auto">
            {scanQ.isLoading && activeScanId ? (
              <LoadingState variant="card" />
            ) : tab === "summary" ? (
              <SummaryView artifact={artifact} busy={chat.busy} />
            ) : tab === "findings" ? (
              <FindingsView artifact={artifact} />
            ) : (
              <SbomView artifact={artifact} />
            )}
          </div>
        </div>
      )}

      <ScanTargetDialog
        open={pickerOpen}
        onOpenChange={setPickerOpen}
        projectId={id}
        onPrepared={onPrepared}
      />

      <AgentChatDrawer
        open={chatOpen}
        onOpenChange={setChatOpen}
        context={{ page: "Security", artifactTitle: targetChip ?? undefined }}
        messages={chat.messages}
        onSend={chat.send}
        busy={chat.busy}
        disabledReason={
          prepared || hasScan ? undefined : "Select a branch or PR to scan first (Select target)."
        }
        starterSuggestions={[
          "Run the security scan and submit your review.",
          "Focus on dependency CVEs and secrets.",
          "Is this branch safe to deploy?",
        ]}
      />
    </div>
  );
}

function TabBtn({
  active,
  onClick,
  icon: Icon,
  children,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ComponentType<{ className?: string }>;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
        active ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:bg-accent/50",
      )}
    >
      <Icon className="size-4" aria-hidden />
      {children}
    </button>
  );
}

function ScanSwitcher({
  scans,
  activeId,
  onSelect,
}: {
  scans: { id: string; label: string; risk_score: string; signoff: string; findings_count: number; created_at: string }[];
  activeId: string | null;
  onSelect: (id: string) => void;
}) {
  const active = scans.find((s) => s.id === activeId);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" className="max-w-[220px]">
          <ScrollText className="size-4 shrink-0" aria-hidden />
          <span className="truncate">{active ? active.label : "Past scans"}</span>
          <ChevronDown className="size-3.5 shrink-0 opacity-60" aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-72">
        <DropdownMenuLabel>Past scans</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {scans.map((s) => (
          <DropdownMenuItem key={s.id} onClick={() => onSelect(s.id)} className="flex-col items-start gap-0.5">
            <span className="flex w-full items-center justify-between gap-2">
              <span className="truncate font-mono text-xs">{s.label}</span>
              <Badge variant="outline" className={cn("text-[10px]", SIGNOFF_META[s.signoff]?.cls)}>
                {SIGNOFF_META[s.signoff]?.label ?? s.signoff}
              </Badge>
            </span>
            <span className="text-muted-foreground text-[10px]">
              {s.findings_count} findings · risk {s.risk_score} · {new Date(s.created_at).toLocaleString()}
            </span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function SummaryView({ artifact, busy }: { artifact: SecurityArtifact | null; busy: boolean }) {
  if (!artifact) {
    return (
      <div className="mx-auto max-w-xl px-4 py-12">
        <EmptyState
          icon={ShieldCheck}
          title={busy ? "Scanning…" : "Run the scan"}
          description={
            busy
              ? "The agent is scanning the branch. Findings, an SBOM, and a sign-off will appear here."
              : "Select a target and run the scan to generate findings, an SBOM, a risk score, and a sign-off."
          }
        />
      </div>
    );
  }
  const sign = SIGNOFF_META[artifact.signoff.decision];
  const risk = RISK_META[artifact.risk_score];
  const m = artifact.metrics;
  return (
    <div className="mx-auto max-w-3xl space-y-6 p-4 md:p-6">
      <div className="flex flex-wrap items-center gap-3">
        <Badge variant="outline" className={cn("border px-3 py-1 text-sm", sign?.cls)}>
          Sign-off: {sign?.label ?? artifact.signoff.decision}
        </Badge>
        <Badge variant="outline" className={cn("border px-3 py-1 text-sm", risk?.cls)}>
          {risk?.label ?? artifact.risk_score}
        </Badge>
        <span className="text-muted-foreground text-xs">
          {m.total} findings · {m.critical} critical · {m.high} high
        </span>
      </div>

      {artifact.signoff.rationale && (
        <p className="text-muted-foreground text-sm">{artifact.signoff.rationale}</p>
      )}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        <Metric label="Critical" value={`${m.critical}`} tone="text-destructive" />
        <Metric label="High" value={`${m.high}`} tone="text-orange-500" />
        <Metric label="Medium" value={`${m.medium}`} tone="text-warning" />
        <Metric label="Low" value={`${m.low}`} tone="text-sky-500" />
        <Metric label="SBOM" value={`${artifact.sbom.length}`} />
      </div>

      {artifact.compliance_frameworks.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-muted-foreground text-xs">Compliance:</span>
          {artifact.compliance_frameworks.map((f) => (
            <Badge key={f} variant="outline" className="text-[10px]">{f}</Badge>
          ))}
        </div>
      )}

      {artifact.summary && (
        <Section title="Summary">
          <Prose>{artifact.summary}</Prose>
        </Section>
      )}
      {artifact.remediation_plan && (
        <Section title="Remediation plan">
          <Prose>{artifact.remediation_plan}</Prose>
        </Section>
      )}
      {artifact.suppression_log.length > 0 && (
        <Section title="Suppression log">
          <ul className="space-y-1 text-sm">
            {artifact.suppression_log.map((sup, i) => (
              <li key={i} className="text-muted-foreground">
                <span className="font-mono text-xs">{sup.finding_id}</span> — {sup.reason}
              </li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{title}</h3>
      {children}
    </section>
  );
}

function Prose({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border bg-surface-1 p-4 text-sm leading-relaxed whitespace-pre-wrap">
      {children}
    </div>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-lg border bg-surface-1 p-3">
      <div className={cn("font-mono text-lg font-semibold", tone)}>{value}</div>
      <div className="text-muted-foreground text-[10px] uppercase tracking-wider">{label}</div>
    </div>
  );
}

function FindingsView({ artifact }: { artifact: SecurityArtifact | null }) {
  if (!artifact) {
    return (
      <div className="mx-auto max-w-xl px-4 py-12">
        <EmptyState icon={ListChecks} title="No findings yet" description="Run the scan to generate findings." variant="plain" />
      </div>
    );
  }
  if (artifact.findings.length === 0) {
    return (
      <div className="mx-auto max-w-xl px-4 py-12">
        <EmptyState icon={Check} title="No vulnerabilities found" description="The scan surfaced nothing worth flagging." variant="plain" />
      </div>
    );
  }
  const grouped = SEV_ORDER.map((sev) => ({
    sev,
    items: artifact.findings.filter((f) => f.severity === sev),
  })).filter((g) => g.items.length > 0);

  return (
    <div className="mx-auto max-w-3xl space-y-5 p-4 md:p-6">
      {grouped.map((g) => (
        <section key={g.sev} className="space-y-2">
          <div className="flex items-center gap-2">
            <span className={cn("size-2 rounded-full", SEV_META[g.sev].dot)} aria-hidden />
            <h3 className={cn("text-xs font-semibold uppercase tracking-wider", SEV_META[g.sev].text)}>
              {SEV_META[g.sev].label} ({g.items.length})
            </h3>
          </div>
          <ul className="space-y-2">
            {g.items.map((f) => (
              <FindingCard key={f.id} finding={f} />
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function FindingCard({ finding }: { finding: SecurityArtifact["findings"][number] }) {
  const [copied, setCopied] = React.useState(false);
  const copy = () => {
    if (!finding.autofix_patch) return;
    void navigator.clipboard.writeText(finding.autofix_patch).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };
  return (
    <li className="rounded-lg border bg-surface-1 p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="font-mono text-[11px] text-muted-foreground">{finding.id}</span>
            <Badge variant="outline" className="text-[10px] uppercase">{finding.category}</Badge>
            {finding.cve && (
              <Badge variant="outline" className="text-[10px]">{finding.cve}</Badge>
            )}
            <span className={cn("text-[10px] font-medium", REACH_TONE[finding.reachability] ?? "text-muted-foreground")}>
              {finding.reachability.replace(/_/g, " ")}
            </span>
            {finding.triage && finding.triage !== "unconfirmed" && (
              <Badge variant="outline" className="text-[10px]">{finding.triage.replace(/_/g, " ")}</Badge>
            )}
          </div>
          <p className="text-sm font-medium">{finding.title}</p>
          {(finding.file || finding.package) && (
            <p className="text-muted-foreground font-mono text-[11px]">
              {finding.package ? finding.package : `${finding.file}${finding.line ? `:${finding.line}` : ""}`}
            </p>
          )}
          {finding.description && <p className="text-sm">{finding.description}</p>}
          {finding.remediation && (
            <p className="text-muted-foreground text-xs">
              <span className="font-medium">Fix:</span> {finding.remediation}
            </p>
          )}
          {finding.compliance.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {finding.compliance.map((c) => (
                <Badge key={c} variant="secondary" className="text-[10px]">{c}</Badge>
              ))}
            </div>
          )}
        </div>
        {finding.autofix_patch && (
          <Button variant="outline" size="sm" onClick={copy} className="shrink-0">
            {copied ? <Check className="size-3.5" aria-hidden /> : <Copy className="size-3.5" aria-hidden />}
            {copied ? "Copied" : "Copy fix"}
          </Button>
        )}
      </div>
      {finding.autofix_patch && (
        <pre className="mt-2 max-h-48 overflow-auto rounded-md border bg-surface-2 p-2 font-mono text-[11px] leading-relaxed">
          {finding.autofix_patch}
        </pre>
      )}
    </li>
  );
}

function SbomView({ artifact }: { artifact: SecurityArtifact | null }) {
  if (!artifact || (artifact.sbom.length === 0 && artifact.supply_chain.length === 0)) {
    return (
      <div className="mx-auto max-w-xl px-4 py-12">
        <EmptyState icon={Boxes} title="No SBOM yet" description="Run the scan to inventory dependencies and supply-chain risk." variant="plain" />
      </div>
    );
  }
  return (
    <div className="mx-auto max-w-3xl space-y-6 p-4 md:p-6">
      {artifact.supply_chain.length > 0 && (
        <Section title="Supply-chain risk">
          <ul className="space-y-1.5">
            {artifact.supply_chain.map((s, i) => (
              <li key={i} className="flex items-center gap-2 text-sm">
                <Badge variant="outline" className="text-[10px] capitalize">{s.risk}</Badge>
                <span className="font-mono text-xs">{s.package}</span>
                {s.note && <span className="text-muted-foreground truncate">— {s.note}</span>}
              </li>
            ))}
          </ul>
        </Section>
      )}
      {artifact.sbom.length > 0 && (
        <Section title={`Components (${artifact.sbom.length})`}>
          <div className="overflow-hidden rounded-lg border">
            <table className="w-full text-sm">
              <thead className="bg-surface-2 text-muted-foreground text-[10px] uppercase tracking-wider">
                <tr>
                  <th className="px-3 py-2 text-left font-medium">Package</th>
                  <th className="px-3 py-2 text-left font-medium">Version</th>
                  <th className="px-3 py-2 text-left font-medium">License</th>
                  <th className="px-3 py-2 text-right font-medium">Vulns</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {artifact.sbom.map((c, i) => (
                  <tr key={`${c.name}-${i}`} className="hover:bg-accent/30">
                    <td className="px-3 py-1.5 font-mono text-xs">{c.name}</td>
                    <td className="px-3 py-1.5 font-mono text-xs text-muted-foreground">{c.version || "—"}</td>
                    <td className="px-3 py-1.5 text-xs text-muted-foreground">{c.license ?? "—"}</td>
                    <td className="px-3 py-1.5 text-right">
                      {c.vulnerabilities > 0 ? (
                        <Badge variant="outline" className="border-destructive/30 text-destructive text-[10px]">
                          {c.vulnerabilities}
                        </Badge>
                      ) : (
                        <span className="text-muted-foreground text-xs">0</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}
    </div>
  );
}
