"use client";

import * as React from "react";
import Link from "next/link";
import { motion, useReducedMotion } from "motion/react";
import {
  ArrowRight,
  ArrowUpRight,
  BookText,
  Check,
  ClipboardList,
  Code2,
  Compass,
  Database,
  DraftingCompass,
  FileSearch,
  FlaskConical,
  GitCompareArrows,
  KeyRound,
  Map,
  Plug,
  Rocket,
  ScrollText,
  ShieldCheck,
  Sparkles,
  type LucideIcon,
} from "lucide-react";

import { PwcMark } from "@/components/brand/pwc-mark";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { TRACK_META, TRACK_ORDER } from "@/lib/tracks";

import { LoginDialog } from "./login-dialog";

const NAV_LINKS = [
  { label: "Capabilities", href: "#capabilities" },
  { label: "Agents", href: "#agents" },
  { label: "Tracks", href: "#tracks" },
  { label: "Enterprise", href: "#enterprise" },
];

const HIGHLIGHTS: { icon: LucideIcon; title: string; text: string }[] = [
  {
    icon: ShieldCheck,
    title: "Human approval at every gate",
    text: "Approve or reject Requirements, Design, Development, Code Review, Security, Testing, and Deployment before the pipeline advances — durable gates that survive restarts.",
  },
  {
    icon: KeyRound,
    title: "Multi-tenant RBAC & SSO",
    text: "Tenant isolation enforced at the database with row-level security. Default and custom roles, OIDC single sign-on.",
  },
  {
    icon: ScrollText,
    title: "Audit & cost governance",
    text: "An immutable trail of every action, with per-run token, cost, latency, and automated quality-score observability.",
  },
  {
    icon: Plug,
    title: "Plugs into your stack",
    text: "Azure DevOps, Jira, GitHub, and Slack connectors. Wire in only what you need — no rip-and-replace.",
  },
];

const AGENTS: { icon: LucideIcon; phase: string; title: string; text: string }[] = [
  {
    icon: ClipboardList,
    phase: "01",
    title: "Requirements Agent",
    text: "Turns intent, documents, and conversations into structured requirements and work items in Azure DevOps or Jira.",
  },
  {
    icon: DraftingCompass,
    phase: "02",
    title: "Design Agent",
    text: "Produces HLD/LLD, C4 architecture diagrams, API contracts, database schema, and ADRs you can review.",
  },
  {
    icon: Code2,
    phase: "03",
    title: "Development Agent",
    text: "Generates code and scaffolds inside a sandboxed runtime with command allowlists and secret redaction.",
  },
  {
    icon: FileSearch,
    phase: "04",
    title: "Code Review Agent",
    text: "Reviews code changes against requirements, design, and quality standards — producing findings and a merge recommendation.",
  },
  {
    icon: ShieldCheck,
    phase: "05",
    title: "Security Agent",
    text: "Dependency scanning (SCA), OWASP SAST, and secret detection with risk scoring and remediation planning.",
  },
  {
    icon: FlaskConical,
    phase: "06",
    title: "Testing Agent",
    text: "Auto-generates test plans and edge cases mapped directly to requirements and the code under test.",
  },
  {
    icon: Rocket,
    phase: "07",
    title: "Deployment Agent",
    text: "Builds release-readiness reports and risk-gate validations before anything ships to production.",
  },
  {
    icon: BookText,
    phase: "08",
    title: "Documentation Agent",
    text: "Compiles every upstream artifact into consistent docs — BRD, API reference, runbooks, changelog, and a run summary.",
  },
  {
    icon: Compass,
    phase: "Track 3 · 4",
    title: "Discovery & Assessment Agent",
    text: "Builds the as-is inventory and assessment that every later agent plans against — for modernization and RPA/infra migrations.",
  },
  {
    icon: Map,
    phase: "Track 3",
    title: "Strategy Agent",
    text: "Turns the assessment into a risk-sequenced execution plan with per-module equivalence criteria, for code modernization.",
  },
  {
    icon: GitCompareArrows,
    phase: "Track 4",
    title: "Migration Mapping Agent",
    text: "Maps each legacy bot or workload to its target platform item-by-item, escalating ambiguous mappings for sign-off.",
  },
  {
    icon: FlaskConical,
    phase: "Track 4",
    title: "Validation Agent",
    text: "Runs parallel-parity validation between old and new systems and accepts the result as cutover-ready.",
  },
  {
    icon: Database,
    phase: "Track 5",
    title: "Data Engineering Agent",
    text: "Builds and registers the data pipeline — ingestion, transform, and schema migrations — for data engineering projects.",
  },
];

const TRACK_ICONS: Record<string, LucideIcon> = {
  greenfield: Sparkles,
  enhancement: ArrowUpRight,
  modernization: Map,
  rpa_infra: GitCompareArrows,
  data_engineering: Database,
};

const CAPABILITIES = [
  "Durable orchestration — runs survive restarts and resume exactly where they paused.",
  "Tenant data isolated at the database with Postgres row-level security.",
  "Every agent call logged: tokens, cost, latency, and an automated quality score.",
  "Compose custom roles from individual permissions — no code change required.",
  "Single sign-on via OIDC, with SCIM-ready user provisioning.",
  "Bring your own LLM key; the platform runs inside your cloud.",
];

export function Landing({ authed, consoleHref }: { authed: boolean; consoleHref: string }) {
  const [loginOpen, setLoginOpen] = React.useState(false);
  const [scrolled, setScrolled] = React.useState(false);
  const reduce = useReducedMotion();

  React.useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 12);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const primaryCta = (size: "sm" | "default" | "lg" = "default") =>
    authed ? (
      <Button asChild size={size} className="gap-2">
        <Link href={consoleHref}>
          Open console
          <ArrowRight className="size-4" aria-hidden />
        </Link>
      </Button>
    ) : (
      <Button size={size} className="gap-2" onClick={() => setLoginOpen(true)}>
        Sign in
        <ArrowRight className="size-4" aria-hidden />
      </Button>
    );

  return (
    <div className="bg-background text-foreground relative min-h-dvh scroll-smooth">
      {/* Ambient atmosphere */}
      <div className="bg-mesh pointer-events-none fixed inset-0 z-0" aria-hidden />
      {/* Light-mode warm tint so the page reads as a surface, not flat white */}
      <div
        className="pointer-events-none fixed inset-0 z-0 bg-[radial-gradient(1100px_600px_at_80%_-5%,oklch(0.62_0.18_38/0.10),transparent_60%),radial-gradient(800px_520px_at_-5%_10%,oklch(0.62_0.18_38/0.06),transparent_55%)] dark:hidden"
        aria-hidden
      />
      <div className="grain pointer-events-none fixed inset-0 z-0" aria-hidden />
      <FloatingParticles reduce={Boolean(reduce)} />
      <FloatingOrbs reduce={Boolean(reduce)} />

      {/* Nav */}
      <header
        className={cn(
          "fixed inset-x-0 top-0 z-40 transition-colors duration-300",
          scrolled
            ? "border-line-soft bg-panel-elevated/70 supports-[backdrop-filter]:bg-panel-elevated/50 border-b backdrop-blur-md"
            : "border-b border-transparent",
        )}
      >
        <div className="mx-auto flex h-16 w-full items-center gap-6 px-6 md:px-10 lg:px-16">
          <Link href="/" className="flex items-center gap-3" aria-label="SDLC Platform">
            <PwcMark size={38} />
            <div className="hidden flex-col leading-tight sm:flex">
              <span className="font-display text-sm font-bold tracking-tight">SDLC Platform</span>
              <span className="text-brand-bright font-mono text-[10px] font-semibold tracking-[0.14em] uppercase">
                Powered by PwC
              </span>
            </div>
          </Link>

          <nav className="ml-4 hidden items-center gap-1 md:flex" aria-label="Sections">
            {NAV_LINKS.map((l) => (
              <a
                key={l.href}
                href={l.href}
                className="text-muted-foreground hover:text-foreground hover:bg-secondary/60 rounded-md px-3 py-1.5 text-sm font-medium transition-colors"
              >
                {l.label}
              </a>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-2">
            <ThemeToggle />
            {primaryCta("sm")}
          </div>
        </div>
      </header>

      <main className="relative z-10">
        {/* Hero */}
        <section className="mx-auto flex w-full flex-col items-center px-6 pt-40 pb-24 text-center md:px-10 lg:px-16">
          <motion.span
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6 }}
            className="border-line-soft bg-panel-elevated/60 text-muted-foreground inline-flex items-center gap-2 rounded-full border px-4 py-1.5 text-xs font-medium backdrop-blur"
          >
            <Sparkles className="text-brand-bright size-3.5" aria-hidden />
            Agentic SDLC — governed, auditable, human-approved
          </motion.span>

          <motion.h1
            initial={{ opacity: 0, y: 24 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, delay: 0.08 }}
            className="font-display mt-6 max-w-4xl text-5xl leading-[1.04] font-bold tracking-tight text-balance sm:text-6xl lg:text-7xl"
          >
            Ship software through{" "}
            <span className="bg-brand-gradient bg-clip-text text-transparent">
              coordinated AI agents
            </span>
            .
          </motion.h1>

          <motion.p
            initial={{ opacity: 0, y: 24 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, delay: 0.16 }}
            className="text-muted-foreground mt-6 max-w-2xl text-lg text-pretty"
          >
            Requirements to documentation, across eight specialized agents — with a human approval gate
            at every phase, tenant isolation, and an end-to-end audit trail. Built for enterprise
            SDLC teams.
          </motion.p>

          <motion.div
            initial={{ opacity: 0, y: 24 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, delay: 0.24 }}
            className="mt-10 flex flex-col items-center gap-3 sm:flex-row"
          >
            {primaryCta("lg")}
            <Button asChild variant="outline" size="lg" className="gap-2">
              <a href="#agents">
                Explore the agents
                <ArrowUpRight className="size-4" aria-hidden />
              </a>
            </Button>
          </motion.div>
        </section>

        {/* Highlights / Capabilities */}
        <section id="capabilities" className="mx-auto w-full px-6 py-20 md:px-10 lg:px-16">
          <SectionEyebrow label="Why teams choose it" />
          <Reveal>
            <h2 className="font-display mt-3 max-w-2xl text-3xl font-bold tracking-tight sm:text-4xl">
              Enterprise guardrails, built in — not bolted on.
            </h2>
          </Reveal>
          <div className="mt-10 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
            {HIGHLIGHTS.map((h, i) => (
              <Reveal key={h.title} delay={i * 0.08}>
                <div className="border-line-soft bg-panel-elevated/40 hover:border-brand-bright/40 h-full rounded-2xl border p-6 transition-colors">
                  <span className="bg-brand-gradient grid size-11 place-items-center rounded-xl text-white shadow-sm">
                    <h.icon className="size-5" aria-hidden />
                  </span>
                  <h3 className="mt-4 text-base font-semibold">{h.title}</h3>
                  <p className="text-muted-foreground mt-1.5 text-sm">{h.text}</p>
                </div>
              </Reveal>
            ))}
          </div>
        </section>

        {/* Agents */}
        <section id="agents" className="mx-auto w-full px-6 py-20 md:px-10 lg:px-16">
          <SectionEyebrow label="The pipeline" />
          <Reveal>
            <h2 className="font-display mt-3 max-w-2xl text-3xl font-bold tracking-tight sm:text-4xl">
              Thirteen agents, one control plane.
            </h2>
          </Reveal>
          <Reveal delay={0.05}>
            <p className="text-muted-foreground mt-3 max-w-2xl text-sm">
              Eight agents run the core hand-off from Requirements to Documentation — with a human in
              the loop at every gate. Five more specialize for modernization, RPA/infra migration, and
              data engineering, picked up automatically by the delivery track you choose.
            </p>
          </Reveal>
          <div className="mt-10 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {AGENTS.map((a, i) => (
              <Reveal key={a.title} delay={i * 0.06}>
                <div className="group border-line-soft bg-panel-elevated/40 hover:border-brand-bright/40 h-full rounded-2xl border p-6 transition-colors hover:shadow-lg">
                  <div className="flex items-center justify-between">
                    <span className="bg-brand-gradient grid size-11 place-items-center rounded-xl text-white shadow-sm">
                      <a.icon className="size-5" aria-hidden />
                    </span>
                    <span className="text-muted-foreground/60 font-mono text-xs font-semibold">
                      {a.phase}
                    </span>
                  </div>
                  <h3 className="mt-4 text-base font-semibold">{a.title}</h3>
                  <p className="text-muted-foreground mt-1.5 text-sm">{a.text}</p>
                </div>
              </Reveal>
            ))}
          </div>
        </section>

        {/* Delivery tracks */}
        <section id="tracks" className="mx-auto w-full px-6 py-20 md:px-10 lg:px-16">
          <SectionEyebrow label="Five ways in" />
          <Reveal>
            <h2 className="font-display mt-3 max-w-2xl text-3xl font-bold tracking-tight sm:text-4xl">
              One platform, five delivery tracks.
            </h2>
          </Reveal>
          <Reveal delay={0.05}>
            <p className="text-muted-foreground mt-3 max-w-2xl text-sm">
              A project&apos;s track selects its agent roster, entry point, and gates — from a
              blank-slate build to migrating a legacy system nobody fully remembers.
            </p>
          </Reveal>
          <div className="mt-10 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {TRACK_ORDER.map((t, i) => {
              const meta = TRACK_META[t];
              const Icon = TRACK_ICONS[t] ?? Sparkles;
              return (
                <Reveal key={t} delay={i * 0.06}>
                  <div className="group border-line-soft bg-panel-elevated/40 hover:border-brand-bright/40 h-full rounded-2xl border p-6 transition-colors hover:shadow-lg">
                    <div className="flex items-center justify-between">
                      <span className="bg-brand-gradient grid size-11 place-items-center rounded-xl text-white shadow-sm">
                        <Icon className="size-5" aria-hidden />
                      </span>
                      <span className="text-muted-foreground/60 font-mono text-xs font-semibold">
                        Track {meta.number}
                      </span>
                    </div>
                    <h3 className="mt-4 text-base font-semibold">{meta.label}</h3>
                    <p className="text-muted-foreground mt-1.5 text-sm">{meta.summary}</p>
                  </div>
                </Reveal>
              );
            })}
          </div>
        </section>

        {/* Enterprise capabilities band */}
        <section id="enterprise" className="mx-auto w-full px-6 py-20 md:px-10 lg:px-16">
          <div className="border-line-soft from-panel-elevated/70 to-background overflow-hidden rounded-3xl border bg-gradient-to-br p-8 sm:p-12">
            <div className="grid gap-10 lg:grid-cols-[0.9fr_1.1fr]">
              <div>
                <SectionEyebrow label="Enterprise-ready" />
                <Reveal>
                  <h2 className="font-display mt-3 text-3xl font-bold tracking-tight sm:text-4xl">
                    Designed for regulated, high-scale delivery.
                  </h2>
                </Reveal>
                <Reveal delay={0.05}>
                  <p className="text-muted-foreground mt-3 text-sm">
                    Governance, isolation, and observability are first-class — so platform teams can
                    pilot fast and scale with confidence.
                  </p>
                </Reveal>
                <Reveal delay={0.1}>
                  <div className="mt-6">{primaryCta()}</div>
                </Reveal>
              </div>
              <ul className="grid gap-3">
                {CAPABILITIES.map((c, i) => (
                  <Reveal key={c} delay={i * 0.06}>
                    <li className="flex items-start gap-3">
                      <span className="bg-brand-bright/15 text-brand-bright mt-0.5 grid size-6 shrink-0 place-items-center rounded-full">
                        <Check className="size-3.5" aria-hidden />
                      </span>
                      <span className="text-sm">{c}</span>
                    </li>
                  </Reveal>
                ))}
              </ul>
            </div>
          </div>
        </section>

        {/* CTA */}
        <section className="mx-auto w-full px-6 py-24 text-center md:px-10 lg:px-16">
          <Reveal>
            <h2 className="font-display text-4xl font-bold tracking-tight text-balance sm:text-5xl">
              Modernize your SDLC with{" "}
              <span className="bg-brand-gradient bg-clip-text text-transparent">
                agentic delivery
              </span>
            </h2>
          </Reveal>
          <Reveal delay={0.06}>
            <p className="text-muted-foreground mx-auto mt-4 max-w-xl text-base">
              Coordinated agents, governed end-to-end — powered by PwC.
            </p>
          </Reveal>
          <Reveal delay={0.12}>
            <div className="mt-8 flex justify-center">{primaryCta("lg")}</div>
          </Reveal>
        </section>
      </main>

      {/* Footer */}
      <footer className="border-line-soft relative z-10 border-t">
        <div className="text-muted-foreground mx-auto flex w-full flex-col items-center justify-between gap-4 px-6 py-8 text-xs sm:flex-row md:px-10 lg:px-16">
          <div className="flex items-center gap-3">
            <PwcMark size={30} />
            <span>SDLC Platform — Powered by PwC</span>
          </div>
          <span>© {new Date().getFullYear()} PwC. All rights reserved.</span>
        </div>
      </footer>

      <LoginDialog open={loginOpen} onOpenChange={setLoginOpen} redirectTo={consoleHref} />
    </div>
  );
}

function SectionEyebrow({ label }: { label: string }) {
  return (
    <div className="text-brand-bright flex items-center gap-2 font-mono text-[11px] font-semibold tracking-[0.14em] uppercase">
      <span className="bg-brand-bright inline-block h-px w-5" aria-hidden />
      {label}
    </div>
  );
}

function Reveal({
  children,
  delay = 0,
  className,
}: {
  children: React.ReactNode;
  delay?: number;
  className?: string;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 24 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-80px" }}
      transition={{ duration: 0.6, delay, ease: [0.2, 0.7, 0.2, 1] }}
      className={className}
    >
      {children}
    </motion.div>
  );
}

function FloatingParticles({ reduce }: { reduce: boolean }) {
  // Mount-gate so the randomized field is generated client-side only — avoids
  // an SSR/hydration mismatch from Math.random() during the server render.
  const [mounted, setMounted] = React.useState(false);
  React.useEffect(() => setMounted(true), []);

  const particles = React.useMemo(
    () =>
      Array.from({ length: 64 }, (_, i) => ({
        id: i,
        left: Math.random() * 100,
        top: Math.random() * 100,
        size: Math.random() * 3 + 2,
        drift: Math.random() * 60 - 30,
        rise: Math.random() * 80 + 40,
        duration: Math.random() * 12 + 12,
        delay: Math.random() * 8,
      })),
    [],
  );

  if (reduce || !mounted) return null;

  return (
    <div className="pointer-events-none fixed inset-0 z-0 overflow-hidden" aria-hidden>
      {particles.map((p) => (
        <motion.span
          key={p.id}
          className="bg-brand-bright absolute rounded-full"
          style={{
            left: `${p.left}%`,
            top: `${p.top}%`,
            width: p.size,
            height: p.size,
          }}
          initial={{ opacity: 0 }}
          animate={{
            x: [0, p.drift, 0],
            y: [0, -p.rise, 0],
            opacity: [0, 0.7, 0.3, 0.7, 0],
            scale: [1, 1.5, 1],
          }}
          transition={{
            duration: p.duration,
            delay: p.delay,
            repeat: Infinity,
            ease: "easeInOut",
          }}
        />
      ))}
    </div>
  );
}

function FloatingOrbs({ reduce }: { reduce: boolean }) {
  if (reduce) return null;
  return (
    <div className="pointer-events-none fixed inset-0 z-0 overflow-hidden" aria-hidden>
      <motion.div
        className="bg-brand-gradient absolute -top-32 -left-24 size-[28rem] rounded-full opacity-[0.08] blur-3xl"
        animate={{ scale: [1, 1.15, 1], x: [0, 40, 0], y: [0, 30, 0] }}
        transition={{ duration: 22, repeat: Infinity, ease: "easeInOut" }}
      />
      <motion.div
        className="bg-brand-gradient absolute -right-24 bottom-0 size-[24rem] rounded-full opacity-[0.06] blur-3xl"
        animate={{ scale: [1.1, 1, 1.1], x: [0, -30, 0], y: [0, -20, 0] }}
        transition={{ duration: 26, repeat: Infinity, ease: "easeInOut" }}
      />
    </div>
  );
}
