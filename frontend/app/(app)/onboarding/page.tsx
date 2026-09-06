"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import {
  ArrowLeft,
  ArrowRight,
  Boxes,
  Building2,
  CheckCircle2,
  Database,
  Globe,
  Globe2,
  LayoutDashboard,
  Loader2,
  Lock,
  Mail,
  Plus,
  Trash2,
  X,
} from "lucide-react";
import { z } from "zod";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Separator } from "@/components/ui/separator";

import { RequireRole } from "@/components/auth/require-role";
import { PwcMark } from "@/components/brand/pwc-mark";
import { useSession } from "@/hooks/use-session";
import { useActiveWorkspace } from "@/hooks/use-workspaces";
import { listConnectors } from "@/lib/api/connectors";
import { createProject } from "@/lib/api/projects";
import { qk } from "@/lib/api/query-keys";
import type { Connector, ConnectorKind, ProjectTemplate } from "@/lib/schemas";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";

// ───────── shared state + persistence ─────────

type Step = 1 | 2 | 3 | 4;

interface WizardState {
  step: Step;
  workspace: {
    name: string;
    region: "us" | "eu";
    retentionDays: 30 | 90 | 365;
  };
  identity: {
    mode: "sso" | "invite";
    ssoDomain?: string;
    ssoProvider?: "okta" | "entra";
    invites: string[];
  };
  tools: {
    work: ConnectorKind | null;
    scm: ConnectorKind | null;
    slack: boolean;
  };
  template: {
    projectName: string;
    template: ProjectTemplate;
  };
}

const STORAGE_KEY = "sdlc.onboarding";

const INITIAL: WizardState = {
  step: 1,
  workspace: { name: "", region: "us", retentionDays: 90 },
  identity: { mode: "invite", invites: [] },
  tools: { work: null, scm: null, slack: false },
  template: { projectName: "", template: "web_app" },
};

function loadState(): WizardState {
  if (typeof window === "undefined") return INITIAL;
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return INITIAL;
    const parsed = JSON.parse(raw) as Partial<WizardState>;
    return { ...INITIAL, ...parsed };
  } catch {
    return INITIAL;
  }
}

// ───────── page ─────────

export default function OnboardingPage() {
  return (
    <RequireRole
      role="admin"
      fallback={
        <div className="mx-auto max-w-md space-y-2 p-8 text-center">
          <h1 className="font-display text-xl font-bold">Admins only</h1>
          <p className="text-muted-foreground text-sm">
            Onboarding a {BUSINESS_UNIT_LABEL.toLowerCase()} is reserved for {BUSINESS_UNIT_LABEL.toLowerCase()} admins.
          </p>
        </div>
      }
    >
      <OnboardingWizard />
    </RequireRole>
  );
}

function OnboardingWizard() {
  const router = useRouter();
  const { user } = useSession({ required: true });
  const [state, setState] = React.useState<WizardState>(INITIAL);
  const [hydrated, setHydrated] = React.useState(false);

  // Load persisted state on mount
  React.useEffect(() => {
    setState(loadState());
    setHydrated(true);
  }, []);

  // Persist on every change (after hydration, so we don't clobber)
  React.useEffect(() => {
    if (!hydrated) return;
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch {
      // quota or disabled — non-fatal
    }
  }, [state, hydrated]);

  const goto = (step: Step) => setState((s) => ({ ...s, step }));

  const onDone = () => {
    try {
      sessionStorage.removeItem(STORAGE_KEY);
    } catch {
      // ignore
    }
  };

  if (!hydrated) return null;

  return (
    <div className="flex min-h-full flex-col">
      {/* Brand-gradient hero header — northstar hero/eyebrow/page-title treatment */}
      <div className="bg-brand-gradient relative overflow-hidden px-4 py-8 md:px-10 md:py-10">
        {/* Subtle grain overlay on hero */}
        <div
          className="pointer-events-none absolute inset-0 opacity-[0.035] mix-blend-overlay"
          aria-hidden
          style={{
            backgroundImage:
              "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='3'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")",
          }}
        />
        <div className="relative mx-auto max-w-5xl">
          {/* Eyebrow */}
          <p className="mb-3 flex items-center gap-2 font-mono text-[11px] font-semibold uppercase tracking-[0.14em] text-primary-foreground/80">
            <span className="inline-block h-px w-5 bg-primary-foreground/60" aria-hidden />
            {BUSINESS_UNIT_LABEL} setup
          </p>
          <h1 className="font-display text-3xl font-bold tracking-tight text-primary-foreground md:text-4xl">
            Welcome, {user.name.split(" ")[0]}
          </h1>
          <p className="mt-2 max-w-lg text-sm text-primary-foreground/80">
            Set up your {BUSINESS_UNIT_LABEL.toLowerCase()} in four steps — {BUSINESS_UNIT_LABEL.toLowerCase()} name,
            identity, tools, and your first project.
          </p>
          {/* Step progress indicator */}
          <div className="mt-5">
            <MobileRailHero current={state.step} />
          </div>
        </div>
      </div>

      {/* Wizard body */}
      <div className="mx-auto flex w-full max-w-5xl flex-1 gap-8 p-4 md:p-8">
        {/* Progress rail — desktop */}
        <aside className="hidden w-56 shrink-0 lg:block">
          <div className="sticky top-4 space-y-3">
            <div className="flex items-center gap-2 pb-1">
              <span className="bg-panel-elevated border-line-soft grid size-9 place-items-center overflow-hidden rounded-md border">
                <PwcMark size={28} />
              </span>
              <div className="flex flex-col leading-tight">
                <span className="font-display text-sm font-semibold">{BUSINESS_UNIT_LABEL} setup</span>
                <span className="text-muted-foreground font-mono text-[10px] uppercase tracking-wider">
                  4 steps
                </span>
              </div>
            </div>
            <StepRail current={state.step} />
          </div>
        </aside>

        {/* Form */}
        <main className="flex-1 space-y-4">
          <MobileRail current={state.step} className="lg:hidden" />

          {state.step === 1 && (
            <WorkspaceStep
              value={state.workspace}
              onChange={(v) => setState((s) => ({ ...s, workspace: v }))}
              onNext={() => goto(2)}
            />
          )}
          {state.step === 2 && (
            <IdentityStep
              value={state.identity}
              onChange={(v) => setState((s) => ({ ...s, identity: v }))}
              onBack={() => goto(1)}
              onNext={() => goto(3)}
            />
          )}
          {state.step === 3 && (
            <ToolsStep
              value={state.tools}
              onChange={(v) => setState((s) => ({ ...s, tools: v }))}
              onBack={() => goto(2)}
              onNext={() => goto(4)}
            />
          )}
          {state.step === 4 && (
            <TemplateStep
              value={state.template}
              workspaceName={state.workspace.name}
              onChange={(v) => setState((s) => ({ ...s, template: v }))}
              onBack={() => goto(3)}
              onComplete={(projectId) => {
                onDone();
                router.push(`/projects/${projectId}`);
              }}
            />
          )}
        </main>
      </div>
    </div>
  );
}

// ───────── step rail ─────────

const STEPS: Array<{ n: Step; label: string; blurb: string }> = [
  { n: 1, label: BUSINESS_UNIT_LABEL, blurb: "Name + region + retention" },
  { n: 2, label: "Identity", blurb: "SSO or invite teammates" },
  { n: 3, label: "Connect tools", blurb: "Jira + GitHub + Slack" },
  { n: 4, label: "First project", blurb: "Template + name" },
];

function StepRail({ current }: { current: Step }) {
  return (
    <ol className="space-y-1" aria-label="Onboarding progress">
      {STEPS.map((s) => {
        const isDone = s.n < current;
        const isActive = s.n === current;
        return (
          <li
            key={s.n}
            className={cn(
              "border-line-soft flex items-start gap-3 rounded-md border p-2 transition-colors",
              isActive && "border-primary bg-primary/5",
              isDone && "border-success/30 bg-success/5",
              !isActive && !isDone && "border-transparent",
            )}
            aria-current={isActive ? "step" : undefined}
          >
            <div
              className={cn(
                "mt-0.5 grid size-6 shrink-0 place-items-center rounded-full border font-mono text-[11px] font-semibold",
                isDone && "border-success bg-success text-success-foreground",
                isActive && "border-primary bg-primary text-primary-foreground",
                !isActive && !isDone && "border-border text-muted-foreground",
              )}
            >
              {isDone ? <CheckCircle2 className="size-3.5" aria-hidden /> : s.n}
            </div>
            <div className="flex min-w-0 flex-col leading-tight">
              <span className="font-display truncate text-sm font-semibold">{s.label}</span>
              <span className="text-muted-foreground truncate text-xs">{s.blurb}</span>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

/** Inline step progress for mobile — placed below the hero */
function MobileRailHero({ current }: { current: Step }) {
  return (
    <div className="flex items-center gap-2">
      {STEPS.map((s) => (
        <React.Fragment key={s.n}>
          <span
            className={cn(
              "inline-flex size-6 items-center justify-center rounded-full border font-mono text-[10px] font-semibold",
              s.n < current &&
                "border-success bg-success text-success-foreground",
              s.n === current &&
                "border-primary-foreground bg-primary-foreground text-primary",
              s.n > current &&
                "border-primary-foreground/40 text-primary-foreground/60",
            )}
            aria-current={s.n === current ? "step" : undefined}
          >
            {s.n < current ? "✓" : s.n}
          </span>
          {s.n !== STEPS.length && (
            <span
              aria-hidden
              className={cn(
                "h-px flex-1",
                s.n < current ? "bg-primary-foreground/60" : "bg-primary-foreground/20",
              )}
            />
          )}
        </React.Fragment>
      ))}
    </div>
  );
}

function MobileRail({ current, className }: { current: Step; className?: string }) {
  return (
    <div className={cn("flex items-center gap-2", className)}>
      {STEPS.map((s) => (
        <React.Fragment key={s.n}>
          <span
            className={cn(
              "inline-flex size-5 items-center justify-center rounded-full border font-mono text-[10px]",
              s.n < current && "border-success bg-success text-success-foreground",
              s.n === current && "border-primary bg-primary text-primary-foreground",
              s.n > current && "border-border text-muted-foreground",
            )}
            aria-current={s.n === current ? "step" : undefined}
          >
            {s.n < current ? "✓" : s.n}
          </span>
          {s.n !== STEPS.length && (
            <span
              aria-hidden
              className={cn("h-px flex-1", s.n < current ? "bg-success" : "bg-border")}
            />
          )}
        </React.Fragment>
      ))}
    </div>
  );
}

// ───────── Step 1: Workspace ─────────

const workspaceSchema = z.object({
  name: z.string().min(2, "At least 2 characters").max(80),
  region: z.enum(["us", "eu"]),
  retentionDays: z.union([z.literal(30), z.literal(90), z.literal(365)]),
});

function WorkspaceStep({
  value,
  onChange,
  onNext,
}: {
  value: WizardState["workspace"];
  onChange: (v: WizardState["workspace"]) => void;
  onNext: () => void;
}) {
  const form = useForm<z.infer<typeof workspaceSchema>>({
    resolver: zodResolver(workspaceSchema),
    defaultValues: value,
  });

  return (
    <Card className="border-line-soft bg-panel-elevated shadow-none">
      <CardHeader>
        <CardTitle className="font-display font-semibold">
          Name your {BUSINESS_UNIT_LABEL.toLowerCase()}
        </CardTitle>
        <CardDescription>
          Choose a region for data residency and a default retention window for audit
          events.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Form {...form}>
          <form
            onSubmit={form.handleSubmit((v) => {
              onChange(v);
              onNext();
            })}
            className="space-y-5"
          >
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{BUSINESS_UNIT_LABEL} name</FormLabel>
                  <FormControl>
                    <Input autoFocus placeholder="Acme Inc." {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="region"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Region</FormLabel>
                  <FormControl>
                    <RadioGroup
                      value={field.value}
                      onValueChange={field.onChange}
                      className="grid grid-cols-1 gap-2 sm:grid-cols-2"
                    >
                      <RegionOption
                        value="us"
                        selected={field.value === "us"}
                        icon={Globe}
                        label="United States"
                        blurb="us-east-1 · standard default"
                      />
                      <RegionOption
                        value="eu"
                        selected={field.value === "eu"}
                        icon={Globe2}
                        label="European Union"
                        blurb="eu-west-1 · GDPR residency"
                      />
                    </RadioGroup>
                  </FormControl>
                  <FormDescription>
                    Customer data never leaves the selected region.
                  </FormDescription>
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="retentionDays"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Audit log retention</FormLabel>
                  <FormControl>
                    <RadioGroup
                      value={String(field.value)}
                      onValueChange={(v) => field.onChange(Number(v))}
                      className="grid grid-cols-3 gap-2"
                    >
                      {[30, 90, 365].map((d) => (
                        <Label
                          key={d}
                          htmlFor={`ret-${d}`}
                          className={cn(
                            "border-line-soft flex cursor-pointer items-center justify-between gap-2 rounded-md border p-3",
                            field.value === d && "border-primary bg-primary/5",
                          )}
                        >
                          <div className="flex flex-col gap-0.5 leading-tight">
                            <span className="text-sm font-medium">
                              {d === 365 ? "1 year" : `${d} days`}
                            </span>
                            {d === 365 && (
                              <span className="font-mono text-muted-foreground text-xs">
                                Enterprise default
                              </span>
                            )}
                          </div>
                          <RadioGroupItem id={`ret-${d}`} value={String(d)} />
                        </Label>
                      ))}
                    </RadioGroup>
                  </FormControl>
                </FormItem>
              )}
            />

            <div className="flex justify-end">
              <Button type="submit">
                Continue
                <ArrowRight aria-hidden />
              </Button>
            </div>
          </form>
        </Form>
      </CardContent>
    </Card>
  );
}

function RegionOption({
  value,
  selected,
  icon: Icon,
  label,
  blurb,
}: {
  value: string;
  selected: boolean;
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  blurb: string;
}) {
  return (
    <Label
      htmlFor={`region-${value}`}
      className={cn(
        "border-line-soft flex cursor-pointer items-start gap-3 rounded-md border p-3",
        selected && "border-primary bg-primary/5",
      )}
    >
      <RadioGroupItem id={`region-${value}`} value={value} className="mt-0.5" />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Icon className="size-4" aria-hidden />
          {label}
        </div>
        <p className="font-mono text-muted-foreground text-xs font-normal">{blurb}</p>
      </div>
    </Label>
  );
}

// ───────── Step 2: Identity ─────────

function IdentityStep({
  value,
  onChange,
  onBack,
  onNext,
}: {
  value: WizardState["identity"];
  onChange: (v: WizardState["identity"]) => void;
  onBack: () => void;
  onNext: () => void;
}) {
  const [inviteDraft, setInviteDraft] = React.useState("");

  const addInvite = () => {
    const email = inviteDraft.trim();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      toast.error("Enter a valid email");
      return;
    }
    if (value.invites.includes(email)) return;
    onChange({ ...value, invites: [...value.invites, email] });
    setInviteDraft("");
  };

  const removeInvite = (email: string) =>
    onChange({ ...value, invites: value.invites.filter((e) => e !== email) });

  return (
    <Card className="border-line-soft bg-panel-elevated shadow-none">
      <CardHeader>
        <CardTitle className="font-display font-semibold">Identity &amp; access</CardTitle>
        <CardDescription>
          Enterprise SSO is the standard path; we can also seed a few teammates by
          email and turn SSO on later.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <RadioGroup
          value={value.mode}
          onValueChange={(v) => onChange({ ...value, mode: v as "sso" | "invite" })}
          className="grid grid-cols-1 gap-2 sm:grid-cols-2"
        >
          <Label
            htmlFor="mode-sso"
            className={cn(
              "border-line-soft flex cursor-pointer items-start gap-3 rounded-md border p-3",
              value.mode === "sso" && "border-primary bg-primary/5",
            )}
          >
            <RadioGroupItem id="mode-sso" value="sso" className="mt-0.5" />
            <div className="flex min-w-0 flex-1 flex-col gap-0.5">
              <div className="flex items-center gap-2 text-sm font-medium">
                <Lock className="size-4" aria-hidden />
                Configure SSO
              </div>
              <p className="text-muted-foreground text-xs font-normal">
                SAML / OIDC via Okta or Microsoft Entra.
              </p>
            </div>
          </Label>
          <Label
            htmlFor="mode-invite"
            className={cn(
              "border-line-soft flex cursor-pointer items-start gap-3 rounded-md border p-3",
              value.mode === "invite" && "border-primary bg-primary/5",
            )}
          >
            <RadioGroupItem id="mode-invite" value="invite" className="mt-0.5" />
            <div className="flex min-w-0 flex-1 flex-col gap-0.5">
              <div className="flex items-center gap-2 text-sm font-medium">
                <Mail className="size-4" aria-hidden />
                Invite teammates
              </div>
              <p className="text-muted-foreground text-xs font-normal">
                Add SSO later from Settings.
              </p>
            </div>
          </Label>
        </RadioGroup>

        {value.mode === "sso" ? (
          <div className="bg-muted/30 border-line-soft space-y-3 rounded-md border border-dashed p-4">
            <div className="grid gap-3 sm:grid-cols-[160px_1fr]">
              <Label htmlFor="sso-provider" className="pt-1.5">
                Provider
              </Label>
              <RadioGroup
                value={value.ssoProvider ?? "okta"}
                onValueChange={(v) =>
                  onChange({ ...value, ssoProvider: v as "okta" | "entra" })
                }
                className="grid grid-cols-2 gap-2"
              >
                {(["okta", "entra"] as const).map((p) => (
                  <Label
                    key={p}
                    htmlFor={`idp-${p}`}
                    className={cn(
                      "border-line-soft flex cursor-pointer items-center justify-between rounded-md border px-3 py-2 text-sm capitalize",
                      (value.ssoProvider ?? "okta") === p && "border-primary bg-primary/5",
                    )}
                  >
                    {p === "entra" ? "Microsoft Entra" : "Okta"}
                    <RadioGroupItem id={`idp-${p}`} value={p} />
                  </Label>
                ))}
              </RadioGroup>
            </div>
            <div className="grid gap-3 sm:grid-cols-[160px_1fr]">
              <Label htmlFor="sso-domain" className="pt-1.5">
                Email domain
              </Label>
              <Input
                id="sso-domain"
                placeholder="acme.com"
                value={value.ssoDomain ?? ""}
                onChange={(e) => onChange({ ...value, ssoDomain: e.target.value })}
              />
            </div>
            <p className="text-muted-foreground text-xs">
              After step 4 we&apos;ll send you a setup email with SAML metadata URL +
              ACS endpoint. You can complete SSO config from Settings any time.
            </p>
          </div>
        ) : (
          <div className="border-line-soft space-y-3 rounded-md border border-dashed p-4">
            <Label>Invite teammates</Label>
            <div className="flex gap-2">
              <Input
                placeholder="name@company.com"
                value={inviteDraft}
                onChange={(e) => setInviteDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addInvite();
                  }
                }}
              />
              <Button type="button" variant="outline" onClick={addInvite}>
                <Plus className="size-4" aria-hidden />
                Add
              </Button>
            </div>
            {value.invites.length > 0 && (
              <ul className="flex flex-wrap gap-1.5">
                {value.invites.map((email) => (
                  <li key={email}>
                    <Badge variant="secondary" className="gap-1 font-mono text-xs">
                      {email}
                      <button
                        type="button"
                        onClick={() => removeInvite(email)}
                        aria-label={`Remove ${email}`}
                        className="hover:text-destructive"
                      >
                        <X className="size-3" aria-hidden />
                      </button>
                    </Badge>
                  </li>
                ))}
              </ul>
            )}
            <p className="text-muted-foreground text-xs">
              Invitees receive an email to accept. All start as members; promote to
              admin from Team &amp; roles.
            </p>
          </div>
        )}

        <div className="flex justify-between">
          <Button variant="ghost" onClick={onBack}>
            <ArrowLeft aria-hidden /> Back
          </Button>
          <Button onClick={onNext}>
            Continue <ArrowRight aria-hidden />
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

// ───────── Step 3: Connect tools ─────────

const WORK_OPTIONS: ConnectorKind[] = ["jira", "azure_devops"];
// Azure DevOps covers source control too (one consolidated connection), so it's
// the SCM option alongside GitHub — Azure Repos is no longer a separate tile.
const SCM_OPTIONS: ConnectorKind[] = ["github", "azure_devops"];

const KIND_LABEL: Record<ConnectorKind, string> = {
  jira: "Jira Cloud",
  azure_devops: "Azure DevOps",
  github: "GitHub",
  azure_repos: "Azure Repos",
  azure_pipelines: "Azure Pipelines",
  github_actions: "GitHub Actions",
  slack: "Slack",
  ms_teams: "Microsoft Teams",
  sharepoint: "SharePoint",
  figma: "Figma",
  confluence: "Confluence",
  sonarqube: "SonarQube",
  sso_okta: "Okta SSO",
  sso_entra: "Microsoft Entra SSO",
};

function ToolsStep({
  value,
  onChange,
  onBack,
  onNext,
}: {
  value: WizardState["tools"];
  onChange: (v: WizardState["tools"]) => void;
  onBack: () => void;
  onNext: () => void;
}) {
  // List connectors so we know what's already installed
  const connectorsQ = useQuery({
    queryKey: qk.connectors.list(),
    queryFn: () => listConnectors(),
  });

  const installedKinds = new Set(
    (connectorsQ.data ?? []).filter((c) => c.installed).map((c) => c.kind),
  );

  // Continue only RECORDS the choice — it no longer installs anything.
  //
  // It used to fire installConnector() per kind, which returned an OAuth authorize URL
  // for the browser to follow. That flow is gone: it ran against one OAuth app the
  // platform registered and held a client secret for on every tenant's behalf. A
  // connector is connected by pasting a credential on the Integrations page, which is
  // per-tenant end to end, so there is nothing for this step to do but remember the
  // selection and say where to finish the job.
  const finish = () => {
    const pending: ConnectorKind[] = [];
    if (value.work && !installedKinds.has(value.work)) pending.push(value.work);
    if (value.scm && !installedKinds.has(value.scm)) pending.push(value.scm);
    if (value.slack && !installedKinds.has("slack")) pending.push("slack");
    if (pending.length > 0) {
      toast.info(
        `Add credentials for ${pending.length} connector${pending.length === 1 ? "" : "s"} in Integrations`,
        { description: "Your selection is saved — connect them whenever you're ready." },
      );
    }
    onNext();
  };

  const pickable = value.work !== null && value.scm !== null;

  return (
    <Card className="border-line-soft bg-panel-elevated shadow-none">
      <CardHeader>
        <CardTitle className="font-display font-semibold">Connect your tools</CardTitle>
        <CardDescription>
          Pick one work-management + one source-control connector. Slack is optional
          but recommended for approvals. You&apos;ll add each one&apos;s credentials on the
          Integrations page.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <ToolGroup
          label="Work management"
          options={WORK_OPTIONS}
          value={value.work}
          installed={installedKinds}
          onChange={(k) => onChange({ ...value, work: k })}
        />
        <Separator className="bg-line-soft" />
        <ToolGroup
          label="Source control"
          options={SCM_OPTIONS}
          value={value.scm}
          installed={installedKinds}
          onChange={(k) => onChange({ ...value, scm: k })}
        />
        <Separator className="bg-line-soft" />
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="bg-surface-2 border-line-soft text-muted-foreground grid size-10 place-items-center rounded-md border font-mono text-base font-semibold">
              S
            </div>
            <div className="flex flex-col">
              <span className="text-sm font-medium">Slack (optional)</span>
              <span className="text-muted-foreground text-xs">
                HITL notifications + button approvals
              </span>
            </div>
          </div>
          <Button
            variant={value.slack ? "secondary" : "outline"}
            size="sm"
            onClick={() => onChange({ ...value, slack: !value.slack })}
          >
            {value.slack ? "Selected" : "Add"}
          </Button>
        </div>

        <div className="flex justify-between">
          <Button variant="ghost" onClick={onBack}>
            <ArrowLeft aria-hidden /> Back
          </Button>
          <Button onClick={finish} disabled={!pickable}>
            Continue <ArrowRight aria-hidden />
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function ToolGroup({
  label,
  options,
  value,
  installed,
  onChange,
}: {
  label: string;
  options: readonly ConnectorKind[];
  value: ConnectorKind | null;
  installed: Set<Connector["kind"]>;
  onChange: (k: ConnectorKind) => void;
}) {
  return (
    <fieldset className="space-y-2">
      <legend className="font-display text-sm font-semibold">{label}</legend>
      <div className="grid gap-2 sm:grid-cols-2">
        {options.map((k) => {
          const isInstalled = installed.has(k);
          const selected = value === k;
          return (
            <button
              key={k}
              type="button"
              onClick={() => onChange(k)}
              className={cn(
                "border-line-soft flex items-center gap-3 rounded-md border p-3 text-left transition-colors",
                "hover:bg-accent/40",
                selected && "border-primary bg-primary/5",
              )}
              aria-pressed={selected}
            >
              <div className="bg-surface-2 border-line-soft text-muted-foreground grid size-10 place-items-center rounded-md border font-mono text-base font-semibold">
                {KIND_LABEL[k].charAt(0)}
              </div>
              <div className="flex min-w-0 flex-1 flex-col">
                <span className="text-sm font-medium">{KIND_LABEL[k]}</span>
                <span className="font-mono text-muted-foreground text-[11px]">
                  {isInstalled ? "Already installed — will reuse" : "Connect via OAuth"}
                </span>
              </div>
              {isInstalled && <CheckCircle2 className="text-success size-4" aria-hidden />}
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}

// ───────── Step 4: Template + first project ─────────

const templateSchema = z.object({
  projectName: z.string().min(2, "At least 2 characters").max(60),
  template: z.enum(["web_app", "microservice", "data_pipeline", "blank"]),
});

const TEMPLATE_OPTIONS: Array<{
  value: ProjectTemplate;
  label: string;
  description: string;
  icon: React.ComponentType<{ className?: string }>;
}> = [
  {
    value: "web_app",
    label: "Web app",
    description: "Frontend + API + DB, React/TS defaults.",
    icon: LayoutDashboard,
  },
  {
    value: "microservice",
    label: "Microservice",
    description: "Stateless service + health checks + image.",
    icon: Boxes,
  },
  {
    value: "data_pipeline",
    label: "Data pipeline",
    description: "Ingestion + transform + schema migrations.",
    icon: Database,
  },
  {
    value: "blank",
    label: "Blank",
    description: "No scaffold — bring your own.",
    icon: Building2,
  },
];

function TemplateStep({
  value,
  workspaceName,
  onChange,
  onBack,
  onComplete,
}: {
  value: WizardState["template"];
  workspaceName: string;
  onChange: (v: WizardState["template"]) => void;
  onBack: () => void;
  onComplete: (projectId: string) => void;
}) {
  const queryClient = useQueryClient();
  // This wizard's earlier "Business Unit setup" step only collects a name
  // locally — it doesn't yet call createWorkspace() to persist a new
  // Business Unit (pre-existing gap, unrelated to project-creation approval;
  // not fixed here). Falls back to the admin's current active Business Unit
  // so the required workspaceId is always real.
  const { active: activeWorkspace } = useActiveWorkspace();
  const form = useForm<z.infer<typeof templateSchema>>({
    resolver: zodResolver(templateSchema),
    defaultValues: value,
  });

  const createMutation = useMutation({
    mutationFn: createProject,
    onSuccess: (p) => {
      toast.success(`Welcome to ${workspaceName || `your ${BUSINESS_UNIT_LABEL.toLowerCase()}`}!`, {
        description: `"${p.name}" is ready.`,
      });
      queryClient.invalidateQueries({ queryKey: qk.projects.all() });
      onComplete(p.id);
    },
    onError: (err) =>
      toast.error("Couldn't create project", {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  return (
    <Card className="border-line-soft bg-panel-elevated shadow-none">
      <CardHeader>
        <CardTitle className="font-display font-semibold">Create your first project</CardTitle>
        <CardDescription>
          Pick a template to seed sensible defaults. You can change these any time from
          project settings.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Form {...form}>
          <form
            onSubmit={form.handleSubmit((v) => {
              onChange(v);
              if (!activeWorkspace) {
                toast.error(`No ${BUSINESS_UNIT_LABEL.toLowerCase()} to create this project in yet.`);
                return;
              }
              // Onboarding creates a Track 1 greenfield project; the track can
              // be changed later from project settings (PRD §6).
              createMutation.mutate({
                name: v.projectName,
                template: v.template,
                track: "greenfield",
                workspaceId: activeWorkspace.id,
              });
            })}
            className="space-y-5"
          >
            <FormField
              control={form.control}
              name="projectName"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Project name</FormLabel>
                  <FormControl>
                    <Input autoFocus placeholder="Checkout service" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="template"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Template</FormLabel>
                  <FormControl>
                    <RadioGroup
                      value={field.value}
                      onValueChange={field.onChange}
                      className="grid grid-cols-1 gap-2 sm:grid-cols-2"
                    >
                      {TEMPLATE_OPTIONS.map((t) => (
                        <Label
                          key={t.value}
                          htmlFor={`onb-tpl-${t.value}`}
                          className={cn(
                            "border-line-soft flex cursor-pointer items-start gap-3 rounded-md border p-3",
                            field.value === t.value && "border-primary bg-primary/5",
                          )}
                        >
                          <RadioGroupItem
                            id={`onb-tpl-${t.value}`}
                            value={t.value}
                            className="mt-0.5"
                          />
                          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                            <div className="flex items-center gap-2 text-sm font-medium">
                              <t.icon className="size-4" aria-hidden />
                              {t.label}
                            </div>
                            <p className="text-muted-foreground text-xs font-normal">
                              {t.description}
                            </p>
                          </div>
                        </Label>
                      ))}
                    </RadioGroup>
                  </FormControl>
                </FormItem>
              )}
            />

            <Separator className="bg-line-soft" />

            <div className="flex justify-between">
              <Button
                type="button"
                variant="ghost"
                onClick={onBack}
                disabled={createMutation.isPending}
              >
                <ArrowLeft aria-hidden /> Back
              </Button>
              <div className="flex gap-2">
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => {
                    try {
                      sessionStorage.removeItem(STORAGE_KEY);
                    } catch {
                      // ignore
                    }
                    toast.message("Onboarding progress cleared");
                  }}
                  disabled={createMutation.isPending}
                >
                  <Trash2 aria-hidden className="size-3.5" />
                  Reset
                </Button>
                <Button
                  type="submit"
                  disabled={createMutation.isPending}
                  aria-busy={createMutation.isPending}
                >
                  {createMutation.isPending && (
                    <Loader2 className="size-4 animate-spin" aria-hidden />
                  )}
                  Finish setup
                  <ArrowRight aria-hidden />
                </Button>
              </div>
            </div>
          </form>
        </Form>
      </CardContent>
    </Card>
  );
}
