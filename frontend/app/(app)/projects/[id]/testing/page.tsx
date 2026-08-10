"use client";

import * as React from "react";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Boxes,
  CheckCircle2,
  Download,
  FileCode2,
  FlaskConical,
  GitBranch,
  GitPullRequest,
  Loader2,
  MessageSquare,
  Play,
  ScrollText,
  Settings2,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";
import { AgentChatDrawer } from "@/components/app/agent-chat-drawer";
import { ModelSelector } from "@/components/app/model-selector";
import { TestTargetDialog, type TestTarget } from "@/components/app/test-target-dialog";
import { RequireRole } from "@/components/auth/require-role";
import { useAgentChat } from "@/hooks/use-agent-chat";
import { useSession } from "@/hooks/use-session";
import { getProject } from "@/lib/api/projects";
import { getUnitResult, openTestsPr, type UnitResult } from "@/lib/api/testing";
import { qk } from "@/lib/api/query-keys";
import type { ProjectId } from "@/lib/schemas";

// ── Test types + their per-type run-config fields ──────────────────────────────
type FieldType = "text" | "password" | "url" | "number" | "select";
interface CfgField {
  key: string;
  label: string;
  type: FieldType;
  placeholder?: string;
  options?: string[];
  required?: boolean;
  default?: string;
  half?: boolean;
}
interface TType {
  id: string;
  label: string;
  blurb: string;
  fields: CfgField[];
}

const TEST_TYPES: TType[] = [
  { id: "unit", label: "Unit", blurb: "Function/class-level tests, generated and executed.",
    fields: [{ key: "coverage_threshold", label: "Coverage threshold %", type: "number", default: "80", half: true },
             { key: "test_scope", label: "Scope", type: "select", options: ["changed", "full"], default: "changed", half: true }] },
  { id: "functional", label: "Functional / UI", blurb: "Drives the running app end-to-end with Selenium.",
    fields: [{ key: "target_url", label: "Target URL (running app)", type: "url", required: true, placeholder: "https://app-dev.internal" },
             { key: "username", label: "Test user", type: "text", half: true },
             { key: "password", label: "Password", type: "password", half: true },
             { key: "browser", label: "Browser", type: "select", options: ["Chromium", "Firefox"], default: "Chromium", half: true },
             { key: "test_scope", label: "Scope", type: "select", options: ["changed", "full"], default: "changed", half: true }] },
  { id: "api", label: "API", blurb: "Validates endpoints (auth, error codes, pagination).",
    fields: [{ key: "base_url", label: "Base API URL", type: "url", required: true, placeholder: "https://api-dev.internal" },
             { key: "auth", label: "Auth", type: "select", options: ["none", "api_key", "bearer", "basic"], default: "none", half: true },
             { key: "auth_token", label: "Token / key", type: "password", half: true },
             { key: "api_timeout_s", label: "Request timeout (s)", type: "number", default: "30", half: true }] },
  { id: "contract", label: "Contract", blurb: "Validates implementation against the OpenAPI contract.",
    fields: [{ key: "base_url", label: "Base API URL", type: "url", placeholder: "https://api-dev.internal" },
             { key: "openapi_url", label: "OpenAPI/Swagger URL", type: "url", placeholder: "…/swagger.json (else from Design)" }] },
  { id: "integration", label: "Integration", blurb: "Module-to-module + DB interactions with real deps.",
    fields: [{ key: "db_connection", label: "DB connection string", type: "password", placeholder: "Server=…;Database=…" },
             { key: "service_url", label: "Dependent service URL", type: "url" },
             { key: "env_vars", label: "Env vars (KEY=val, comma-sep)", type: "text", placeholder: "REDIS_URL=…, FEATURE_X=1" }] },
  { id: "smoke", label: "Smoke", blurb: "Critical-path stability checks.",
    fields: [{ key: "target_url", label: "Base/Target URL", type: "url", placeholder: "https://app-dev.internal" }] },
  { id: "accessibility", label: "Accessibility", blurb: "WCAG checks against the running app.",
    fields: [{ key: "target_url", label: "Target URL", type: "url", required: true },
             { key: "wcag_level", label: "WCAG level", type: "select", options: ["A", "AA", "AAA"], default: "AA", half: true }] },
  { id: "smoke_perf", label: "Performance", blurb: "Load profile against the running app (script + run where supported).",
    fields: [{ key: "target_url", label: "Target URL", type: "url", required: true },
             { key: "vus", label: "Virtual users", type: "number", default: "10", half: true },
             { key: "duration_s", label: "Duration (s)", type: "number", default: "30", half: true },
             { key: "p95_ms", label: "p95 threshold (ms)", type: "number", default: "500", half: true }] },
  { id: "mutation", label: "Mutation", blurb: "Mutation score on existing tests.",
    fields: [{ key: "min_score", label: "Min mutation score %", type: "number", default: "60", half: true },
             { key: "time_budget_min", label: "Time budget (min)", type: "number", default: "10", half: true }] },
  { id: "property_based", label: "Property-based", blurb: "Property/invariant tests with generated inputs.",
    fields: [{ key: "examples", label: "Examples", type: "number", default: "100", half: true },
             { key: "seed", label: "Seed", type: "number", half: true }] },
  { id: "negative_edge", label: "Negative / Edge", blurb: "Negative paths + boundary conditions.",
    fields: [{ key: "test_scope", label: "Scope", type: "select", options: ["changed", "full"], default: "changed", half: true }] },
  { id: "security_static", label: "Security (static)", blurb: "SAST over the code (also covered by the Security agent).", fields: [] },
  { id: "dependency_scan", label: "Dependencies", blurb: "Dependency vuln scan (also covered by the Security agent).",
    fields: [{ key: "severity", label: "Min severity", type: "select", options: ["low", "medium", "high", "critical"], default: "medium", half: true }] },
];

type Tab = "qa" | "output";

export default function TestingPage() {
  const params = useParams<{ id: string }>();
  const id = params.id as ProjectId;
  useSession({ required: true });

  const projectQ = useQuery({ queryKey: qk.projects.detail(id), queryFn: () => getProject(id) });

  const [target, setTarget] = React.useState<TestTarget | null>(null);
  const [pickerOpen, setPickerOpen] = React.useState(false);
  const [chatOpen, setChatOpen] = React.useState(false);
  const [panelOpen, setPanelOpen] = React.useState(true);
  const [selectedType, setSelectedType] = React.useState<string>("unit");
  const [cfg, setCfg] = React.useState<Record<string, Record<string, string>>>({});
  const [tab, setTab] = React.useState<Tab>("output");
  const [ranSession, setRanSession] = React.useState<string | null>(null);
  const [ranType, setRanType] = React.useState<string | null>(null);
  const [agentModel, setAgentModel] = React.useState<string>();

  const queryClient = useQueryClient();
  const chat = useAgentChat({
    agent: "testing",
    projectId: id,
    sessionKey: id,
    onArtifact: () => {
      queryClient.invalidateQueries({ queryKey: qk.artifacts.forProject(id) });
      queryClient.invalidateQueries({ queryKey: qk.runs.forProject(id) });
    },
    context: {
      project_id: id,
      page: "Testing",
      artifactTitle: target ? `${target.repo}@${target.branch}` : undefined,
    },
  });

  // When a run finishes, surface the reports for that session.
  const prevBusy = React.useRef(chat.busy);
  React.useEffect(() => {
    if (prevBusy.current && !chat.busy && chat.messages.some((m) => m.role === "agent")) {
      setRanSession(chat.sessionId ?? null); // enables the QA Report tab + Download
    }
    prevBusy.current = chat.busy;
  }, [chat.busy, chat.sessionId, chat.messages]);

  // After a unit run, fetch its coverage + generated files (for the results bar + gated PR).
  const unitQ = useQuery({
    queryKey: qk.testing.unitResult(id, chat.sessionId ?? ""),
    queryFn: () => getUnitResult(id, chat.sessionId ?? ""),
    enabled: !!ranSession && !!chat.sessionId && ranType === "unit",
    refetchInterval: chat.busy ? 4000 : false,
  });
  const prMut = useMutation({
    mutationFn: () => openTestsPr(id, chat.sessionId ?? ""),
    onSuccess: () => { toast.success("Tests PR opened"); void unitQ.refetch(); },
    onError: (e) => toast.error("Couldn't open the tests PR", { description: e instanceof Error ? e.message : undefined }),
  });

  const tt = TEST_TYPES.find((t) => t.id === selectedType)!;
  const typeCfg = cfg[selectedType] ?? {};
  const setField = (k: string, v: string) =>
    setCfg((c) => ({ ...c, [selectedType]: { ...(c[selectedType] ?? {}), [k]: v } }));

  const missingRequired = tt.fields.filter((f) => f.required && !(typeCfg[f.key] ?? f.default)).map((f) => f.label);
  const canRun = !!target && missingRequired.length === 0 && !chat.busy;

  const runTests = () => {
    if (!target) { setPickerOpen(true); return; }
    // Assemble test_config from defaults + entered values.
    const test_config: Record<string, unknown> = {};
    for (const f of tt.fields) {
      const v = typeCfg[f.key] ?? f.default;
      if (v !== undefined && v !== "") test_config[f.key] = f.type === "number" ? Number(v) : v;
    }
    if (selectedType === "api" || selectedType === "contract") test_config["api_scope"] = "both";
    const agentParams = {
      selected_test_types: [selectedType],
      clone_target: { project: target.ado_project, repo: target.repo, branch: target.branch },
      test_config,
      // Clicking Run is an explicit intent to execute the full flow (generate →
      // run → coverage) — bypass the conversational staged-approval gates.
      execute_now: true,
      ...(test_config["target_url"] ? { target_url: test_config["target_url"] } : {}),
      ...(agentModel ? { model: agentModel } : {}),
    };
    // A run is an action, not a chat: show progress + result in the Output pane
    // (the chat drawer stays for free-form questions).
    setTab("output");
    setRanType(selectedType);
    void chat.send(
      `Run ${tt.label} tests on ${target.repo} @ ${target.branch}.`,
      agentParams,
    );
  };

  if (projectQ.isLoading) {
    return <div className="w-full p-4 md:px-10 md:py-8"><LoadingState variant="card" /></div>;
  }
  if (projectQ.isError || !projectQ.data) {
    return (
      <div className="w-full p-4 md:px-10 md:py-8">
        <ErrorState title="Project not found"
          description={projectQ.error instanceof Error ? projectQ.error.message : "Unknown error."}
          onRetry={() => projectQ.refetch()} />
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Header */}
      <div className="border-b px-4 py-3 md:px-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-xl font-semibold tracking-tight">Testing</h1>
            <p className="text-muted-foreground text-xs">
              {target ? (
                <span className="inline-flex items-center gap-1">
                  <GitBranch className="size-3" aria-hidden />
                  <span className="font-mono">{target.ado_project} / {target.repo}</span>
                  <span className="opacity-50">@</span>
                  <span className="font-mono">{target.branch}</span>
                </span>
              ) : (
                <span>Generate &amp; run tests of any type against a branch.</span>
              )}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <ModelSelector
              aria-label="Testing agent model"
              value={agentModel}
              onValueChange={setAgentModel}
            />
            <Button variant="outline" size="sm" onClick={() => setPickerOpen(true)}>
              <GitBranch className="size-4" aria-hidden />
              Select target
            </Button>
            <Button variant="outline" size="sm" onClick={() => setPanelOpen((o) => !o)}>
              <Settings2 className="size-4" aria-hidden />
              New test run
            </Button>
            <Button variant="outline" size="sm" onClick={() => setChatOpen(true)}>
              <MessageSquare className="size-4" aria-hidden />
              Chat
            </Button>
          </div>
        </div>
      </div>

      <div className={cn("grid min-h-0 flex-1 overflow-hidden",
        panelOpen ? "md:grid-cols-[360px_1fr]" : "grid-cols-1")}>
        {/* Left config rail */}
        {panelOpen && (
          <aside className="min-h-0 overflow-auto border-b md:border-b-0 md:border-r">
            <div className="space-y-4 p-3">
              <div>
                <p className="text-muted-foreground mb-2 text-xs font-semibold uppercase tracking-wider">Test type</p>
                <div className="grid grid-cols-2 gap-1.5">
                  {TEST_TYPES.map((t) => (
                    <button key={t.id} type="button" onClick={() => setSelectedType(t.id)}
                      className={cn("rounded-lg border px-2.5 py-2 text-left text-xs transition-colors",
                        selectedType === t.id
                          ? "border-brand-bright/50 bg-brand-bright/10 text-brand-bright"
                          : "border-line-soft bg-surface-1 text-muted-foreground hover:bg-surface-2")}>
                      <span className="font-medium">{t.label}</span>
                    </button>
                  ))}
                </div>
              </div>

              <div className="space-y-2">
                <p className="text-muted-foreground text-xs font-semibold uppercase tracking-wider">
                  Run configuration — {tt.label}
                </p>
                <p className="text-muted-foreground text-[11px]">{tt.blurb}</p>
                <div className="grid grid-cols-2 gap-2">
                  {tt.fields.length === 0 && (
                    <p className="text-muted-foreground col-span-2 text-[11px]">No extra inputs needed.</p>
                  )}
                  {tt.fields.map((f) => (
                    <div key={f.key} className={cn(f.half ? "col-span-1" : "col-span-2", "space-y-1")}>
                      <Label className="text-[10px] uppercase tracking-wide text-muted-foreground">
                        {f.label}{f.required && <span className="text-destructive"> *</span>}
                      </Label>
                      {f.type === "select" ? (
                        <select
                          value={typeCfg[f.key] ?? f.default ?? ""}
                          onChange={(e) => setField(f.key, e.target.value)}
                          className="border-line-soft bg-surface-1 h-9 w-full rounded-md border px-2 text-sm">
                          {f.options!.map((o) => <option key={o} value={o}>{o}</option>)}
                        </select>
                      ) : (
                        <Input
                          type={f.type === "number" ? "number" : f.type === "password" ? "password" : "text"}
                          value={typeCfg[f.key] ?? f.default ?? ""}
                          onChange={(e) => setField(f.key, e.target.value)}
                          placeholder={f.placeholder}
                          className="h-9 text-sm" />
                      )}
                    </div>
                  ))}
                </div>
                {!target && (
                  <p className="text-warning text-[11px]">Select a target branch first.</p>
                )}
                {target && missingRequired.length > 0 && (
                  <p className="text-warning text-[11px]">Required: {missingRequired.join(", ")}.</p>
                )}
                <RequireRole capability="run:trigger" fallback={<Button disabled className="w-full">Run</Button>}>
                  <Button onClick={runTests} disabled={!canRun} className="w-full">
                    <Play className="size-4" aria-hidden />
                    Run {tt.label} tests
                  </Button>
                </RequireRole>
                <p className="text-muted-foreground text-[10px]">
                  Secrets are sent over the authenticated channel for this run only and redacted in logs.
                </p>
              </div>
            </div>
          </aside>
        )}

        {/* Main results pane */}
        <main className="flex min-h-0 flex-col overflow-hidden">
          <div className="flex items-center gap-1 border-b px-2 py-1.5">
            <TabBtn active={tab === "output"} onClick={() => setTab("output")} icon={ScrollText}>Output</TabBtn>
            <TabBtn active={tab === "qa"} onClick={() => setTab("qa")} icon={Boxes}>QA Report</TabBtn>
            {ranSession && (
              <a className="ml-auto" href={`/api/testing/${id}/download/${ranSession}/testing_agent_reports.zip`}>
                <Button variant="outline" size="sm"><Download className="size-4" aria-hidden />Download reports</Button>
              </a>
            )}
          </div>
          {ranType === "unit" && unitQ.data?.available && (
            <UnitResultBar data={unitQ.data} busy={chat.busy} pending={prMut.isPending} onOpenPr={() => prMut.mutate()} />
          )}
          <div className="min-h-0 flex-1 overflow-auto">
            {tab === "output" ? (
              <OutputView messages={chat.messages} busy={chat.busy} onOpenChat={() => setChatOpen(true)} hasTarget={!!target}
                onSelectTarget={() => setPickerOpen(true)} />
            ) : ranSession ? (
              <iframe title="QA report" src={`/api/testing/${id}/qa/${ranSession}`} className="h-full w-full bg-white" />
            ) : (
              <div className="mx-auto max-w-xl px-4 py-12">
                <EmptyState icon={Boxes} title="No QA report yet"
                  description="Run a test type to generate the report, coverage, and downloadable test files." variant="plain" />
              </div>
            )}
          </div>
        </main>
      </div>

      <TestTargetDialog open={pickerOpen} onOpenChange={setPickerOpen} projectId={id} onSelected={setTarget} />

      <AgentChatDrawer
        open={chatOpen}
        onOpenChange={setChatOpen}
        context={{ page: "Testing", artifactTitle: target ? `${target.repo}@${target.branch}` : undefined }}
        messages={chat.messages}
        onSend={(text) =>
          chat.send(text, {
            ...(target ? { clone_target: { project: target.ado_project, repo: target.repo, branch: target.branch } } : {}),
            ...(agentModel ? { model: agentModel } : {}),
          })
        }
        busy={chat.busy}
        sessions={chat.sessions}
        activeSessionId={chat.sessionId}
        onSelectSession={chat.selectSession}
        onNewChat={chat.newChat}
        attachments={chat.attachments}
        onAttachFiles={chat.attachFiles}
        onRemoveAttachment={chat.removeAttachment}
        disabledReason={target ? undefined : "Select a target branch first (Select target)."}
        starterSuggestions={[
          "What test types make sense for the changes on this branch?",
          "Generate unit tests for the changed files.",
          "Explain the failures from the last run.",
        ]}
      />
    </div>
  );
}

function TabBtn({ active, onClick, icon: Icon, children }: {
  active: boolean; onClick: () => void; icon: React.ComponentType<{ className?: string }>; children: React.ReactNode;
}) {
  return (
    <button type="button" onClick={onClick}
      className={cn("flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
        active ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:bg-accent/50")}>
      <Icon className="size-4" aria-hidden />
      {children}
    </button>
  );
}

function UnitResultBar({ data, busy, pending, onOpenPr }: {
  data: UnitResult; busy: boolean; pending: boolean; onOpenPr: () => void;
}) {
  const cov = data.coverage;
  const res = data.results;
  const files = data.generated_files ?? [];
  const pct = typeof cov?.coverage_pct === "number" ? cov.coverage_pct : null;
  const covCls = pct === null ? "" : pct >= 80 ? "text-success" : pct >= 50 ? "text-warning" : "text-destructive";
  return (
    <div className="border-b bg-surface-1/60 px-3 py-2">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs">
        <span className="text-brand-bright inline-flex items-center gap-1 font-semibold uppercase tracking-wider">
          <FlaskConical className="size-3.5" aria-hidden />Unit results
        </span>
        {pct !== null && (
          <span className="inline-flex items-center gap-1">
            <span className="text-muted-foreground">Coverage</span>
            <span className={cn("font-semibold tabular-nums", covCls)}>{pct.toFixed(1)}%</span>
          </span>
        )}
        {res && (
          <span className="inline-flex items-center gap-1.5">
            <span className="text-success">{res.passed} passed</span>
            {res.failed > 0 && <span className="text-destructive">{res.failed} failed</span>}
            <span className="text-muted-foreground">/ {res.total} tests</span>
          </span>
        )}
        <span className="text-muted-foreground inline-flex items-center gap-1">
          <FileCode2 className="size-3.5" aria-hidden />{files.length} generated file(s)
        </span>
        <div className="ml-auto">
          {data.pr_url ? (
            <a href={data.pr_url} target="_blank" rel="noreferrer">
              <Button variant="outline" size="sm" className="h-7 gap-1.5 text-xs">
                <CheckCircle2 className="text-success size-3.5" aria-hidden />View tests PR
              </Button>
            </a>
          ) : files.length > 0 ? (
            <RequireRole capability="run:trigger" fallback={null}>
              <Button size="sm" className="from-brand-gradient-from to-brand-gradient-to h-7 gap-1.5 bg-gradient-to-br text-xs font-semibold text-white"
                onClick={onOpenPr} disabled={busy || pending}>
                {pending ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : <GitPullRequest className="size-3.5" aria-hidden />}
                {pending ? "Opening PR…" : "Open tests PR"}
              </Button>
            </RequireRole>
          ) : null}
        </div>
      </div>
      {files.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {files.slice(0, 8).map((f) => (
            <span key={f.path} className="border-line-soft bg-surface-2 text-muted-foreground rounded border px-1.5 py-0.5 font-mono text-[10px]">
              {f.path}
            </span>
          ))}
          {files.length > 8 && <span className="text-muted-foreground text-[10px]">+{files.length - 8} more</span>}
        </div>
      )}
    </div>
  );
}

function OutputView({ messages, busy, onOpenChat, hasTarget, onSelectTarget }: {
  messages: ReturnType<typeof useAgentChat>["messages"];
  busy: boolean; onOpenChat: () => void; hasTarget: boolean; onSelectTarget: () => void;
}) {
  const agentMsgs = messages.filter((m) => m.role === "agent" && m.content);
  if (messages.length === 0 && !busy) {
    return (
      <div className="mx-auto max-w-xl px-4 py-12">
        <EmptyState icon={FlaskConical}
          title={hasTarget ? "Pick a test type and run it" : "Import code to test"}
          description={hasTarget
            ? "Choose a test type on the left, fill its run configuration, and click Run. Progress and results appear here; use Chat for follow-up questions."
            : "Select a branch from Azure DevOps, then choose a test type to generate and run."}
          action={<Button onClick={hasTarget ? onOpenChat : onSelectTarget}>
            {hasTarget ? "Open chat" : "Select target"}</Button>} />
      </div>
    );
  }
  return (
    <div className="mx-auto max-w-3xl space-y-3 p-4 md:p-6">
      {busy && <RunningBanner />}
      {agentMsgs.map((m) => (
        <div key={m.id} className="rounded-lg border bg-surface-1 p-3 text-sm whitespace-pre-wrap leading-relaxed">
          {m.content}
        </div>
      ))}
      {!busy && (
        <Button variant="outline" size="sm" onClick={onOpenChat}>
          <MessageSquare className="size-4" aria-hidden />Ask a follow-up in chat
        </Button>
      )}
    </div>
  );
}

function RunningBanner() {
  const [elapsed, setElapsed] = React.useState(0);
  React.useEffect(() => {
    const start = Date.now();
    const id = setInterval(() => setElapsed(Math.floor((Date.now() - start) / 1000)), 1000);
    return () => clearInterval(id);
  }, []);
  return (
    <div className="border-brand-bright/30 bg-brand-bright/5 flex items-center gap-3 rounded-lg border p-3">
      <span className="inline-flex items-center gap-1">
        {[0, 1, 2].map((i) => (
          <span key={i} className="bg-brand-bright/70 size-1.5 animate-bounce rounded-full"
            style={{ animationDelay: `${i * 160}ms` }} />
        ))}
      </span>
      <span className="text-sm">
        Running the test agent… <span className="tabular-nums text-muted-foreground">{elapsed}s</span>
        <span className="text-muted-foreground"> · cloning, generating &amp; executing tests can take a few minutes</span>
      </span>
    </div>
  );
}
