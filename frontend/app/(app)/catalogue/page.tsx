"use client";

import * as React from "react";
import Link from "next/link";
import {
  ArrowRight,
  BookOpen,
  Boxes,
  Clock,
  Compass,
  Gauge,
  Layers,
  Route,
  Search,
  ShieldCheck,
  Sparkles,
  Star,
  Users,
  Workflow,
  X,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { PHASE_LABEL } from "@/lib/agents";
import { CAPABILITY_CLASS_META, CAPABILITY_CLASS_ORDER } from "@/lib/capability-class";
import { ROLE_META } from "@/lib/roles";
import { TRACK_META, TRACK_ORDER } from "@/lib/tracks";
import type { DeliveryTrack, Phase } from "@/lib/schemas/enums";
import type { PlatformRole } from "@/lib/roles";
import {
  AGENT_INVARIANTS,
  AUTONOMY_LADDER,
  BUSINESS_OUTCOMES,
  CATALOGUE_AGENTS,
  GETTING_STARTED,
  GOVERNANCE_CONTROLS,
  MASTER_JOURNEYS,
  PERSONA_VIEWS,
  PLATFORM_CAPABILITIES,
  PLATFORM_FRAMING,
  PLATFORM_POSITIONING,
  PLATFORM_PROPOSITION,
  RELEASE_LIFECYCLE,
  RISK_TIERS,
  TRACK_DETAIL,
  agentsInTrack,
  agentsOwnedBy,
  tracksForAgent,
} from "@/lib/catalogue";
import { AgentReadinessSection } from "@/components/catalogue/agent-readiness-section";
import {
  CapabilityClassChip,
  CatalogueSection,
  Chip,
  Disclosure,
  LabelledList,
  MetricTile,
  NoResults,
} from "@/components/catalogue/catalogue-primitives";
import { AgentCard } from "@/components/catalogue/agent-card";
import { PersonaAgentList, WorkflowMap } from "@/components/catalogue/workflow-map";

/**
 * Agent Catalogue — the platform's discovery portal.
 *
 * DELIBERATELY UNGATED. Every other Govern screen carries a permission or a
 * scope requirement; this one carries none, because its job is to answer "what
 * can this platform do" for anyone who can sign in. It reads no project, unit
 * or spend data — only the documented model — so there is nothing here to
 * scope. That is what makes it safe to leave open, and it is the same reasoning
 * behind `/my-access` being the one ungated RBAC surface.
 *
 * Everything rendered is derived from `lib/catalogue.ts`, which in turn
 * composes the platform's existing single sources of truth. No agent, track,
 * persona or capability is written here.
 */

const FAVOURITES_KEY = "sdlc.catalogue.favourites";
const RECENT_KEY = "sdlc.catalogue.recent";
const MAX_RECENT = 5;

/** Small localStorage-backed string set — favourites and recently viewed. */
function useStoredList(key: string, cap?: number) {
  const [items, setItems] = React.useState<string[]>([]);

  // Read after mount, never during render: the server has no localStorage, and
  // seeding state from it directly would hydrate-mismatch on every load.
  React.useEffect(() => {
    try {
      const raw = window.localStorage.getItem(key);
      if (raw) setItems(JSON.parse(raw) as string[]);
    } catch {
      // A blocked or corrupt store is not worth breaking the page over.
    }
  }, [key]);

  const persist = React.useCallback(
    (next: string[]) => {
      setItems(next);
      try {
        window.localStorage.setItem(key, JSON.stringify(next));
      } catch {
        /* ignore */
      }
    },
    [key],
  );

  const toggle = React.useCallback(
    (id: string) => persist(items.includes(id) ? items.filter((x) => x !== id) : [...items, id]),
    [items, persist],
  );

  const push = React.useCallback(
    (id: string) => persist([id, ...items.filter((x) => x !== id)].slice(0, cap ?? items.length + 1)),
    [items, persist, cap],
  );

  return { items, toggle, push };
}

export default function AgentCataloguePage() {
  const [query, setQuery] = React.useState("");
  const [track, setTrack] = React.useState<DeliveryTrack | null>(null);
  const [phase, setPhase] = React.useState<Phase | null>(null);
  const [persona, setPersona] = React.useState<PlatformRole | null>(null);
  const [mapTrack, setMapTrack] = React.useState<DeliveryTrack | null>(null);
  const [favouritesOnly, setFavouritesOnly] = React.useState(false);

  const favourites = useStoredList(FAVOURITES_KEY);
  const recent = useStoredList(RECENT_KEY, MAX_RECENT);

  const hasFilter =
    query.trim() !== "" || track !== null || phase !== null || persona !== null || favouritesOnly;

  const clearAll = () => {
    setQuery("");
    setTrack(null);
    setPhase(null);
    setPersona(null);
    setFavouritesOnly(false);
  };

  const agents = React.useMemo(() => {
    const q = query.trim().toLowerCase();
    return CATALOGUE_AGENTS.filter((a) => {
      if (favouritesOnly && !favourites.items.includes(a.phase)) return false;
      if (track && !tracksForAgent(a.phase).includes(track)) return false;
      if (phase && a.phase !== phase) return false;
      if (persona && a.ownerRole !== persona) return false;
      if (!q) return true;
      // Search across everything a person might remember an agent by — its
      // name, what it does, why it matters, its tags, and the artifacts it
      // produces. Matching on the name alone makes search useless for the
      // common case of "which agent gives me an SBOM".
      const haystack = [
        a.name,
        a.purpose,
        a.businessValue,
        ROLE_META[a.ownerRole].label,
        ...a.tags,
        ...a.profiles.flatMap((p) => [...p.inputs, ...p.outputs, p.mode ?? "", p.approvalFlow]),
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(q);
    });
  }, [query, track, phase, persona, favouritesOnly, favourites.items]);

  const totalTrackSlots = TRACK_ORDER.reduce((n, t) => n + agentsInTrack(t).length, 0);

  const openAgent = (p: Phase) => {
    recent.push(p);
    document.getElementById(`agent-${p}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  return (
    <div className="w-full space-y-14 p-4 pb-20 md:px-10 md:py-8">
      {/* ── Hero ──────────────────────────────────────────────────────────── */}
      <header className="relative overflow-hidden rounded-3xl border border-line-soft bg-panel-elevated px-6 py-10 md:px-10 md:py-14">
        <div
          className="from-brand-gradient-from/[0.09] pointer-events-none absolute inset-0 bg-gradient-to-br to-transparent"
          aria-hidden
        />
        <div className="relative max-w-3xl">
          <div className="text-brand-bright mb-3 flex items-center gap-2 font-mono text-[11px] tracking-[0.16em] uppercase">
            <Sparkles className="size-3.5" aria-hidden />
            About
          </div>
          <h1 className="font-display text-[38px] leading-[1.05] font-bold tracking-[-0.035em] md:text-[46px]">
            Every agent, track and control the platform actually has.
          </h1>
          <p className="text-muted-foreground mt-4 text-[15px] leading-relaxed">
            {PLATFORM_PROPOSITION}
          </p>
          <p className="text-muted-foreground/80 mt-2 text-[13px] italic">{PLATFORM_POSITIONING}</p>

          <div className="mt-7 flex flex-wrap gap-2">
            <Button asChild className="from-brand-gradient-from to-brand-gradient-to bg-gradient-to-br font-semibold text-white">
              <a href="#agents">
                <Boxes className="size-4" aria-hidden />
                Browse agents
              </a>
            </Button>
            <Button asChild variant="outline" className="border-line-soft">
              <a href="#tracks">
                <Route className="size-4" aria-hidden />
                Delivery tracks
              </a>
            </Button>
            <Button asChild variant="outline" className="border-line-soft">
              <a href="#learn">
                <BookOpen className="size-4" aria-hidden />
                Get started
              </a>
            </Button>
          </div>
        </div>

        <div className="relative mt-9 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <MetricTile icon={Boxes} value={String(CATALOGUE_AGENTS.length)} label="Agents" sub="One control plane" />
          <MetricTile icon={Route} value={String(TRACK_ORDER.length)} label="Delivery tracks" sub="Templates, not products" />
          <MetricTile icon={Layers} value={String(totalTrackSlots)} label="Agent stages" sub="Across all tracks" />
          <MetricTile icon={Users} value={String(PERSONA_VIEWS.length)} label="Roles" sub="Governance and delivery" />
        </div>
      </header>

      {/* ── Platform overview ─────────────────────────────────────────────── */}
      <CatalogueSection
        id="overview"
        eyebrow="Platform overview"
        title="What this platform is"
        lead="Four facts frame every journey through it."
      >
        <div className="grid gap-3 md:grid-cols-2">
          {PLATFORM_FRAMING.map((f) => (
            <div key={f.title} className="border-line-soft bg-panel-elevated rounded-2xl border p-5">
              <h3 className="font-display text-[14px] font-bold tracking-[-0.01em]">{f.title}</h3>
              <p className="text-muted-foreground mt-1.5 text-[12.5px] leading-relaxed">{f.body}</p>
            </div>
          ))}
        </div>

        <div className="mt-3 grid gap-3 md:grid-cols-3">
          {PLATFORM_CAPABILITIES.map((c) => (
            <div key={c.title} className="border-line-soft rounded-2xl border border-dashed p-5">
              <h3 className="font-display text-[13.5px] font-bold">{c.title}</h3>
              <p className="text-muted-foreground mt-1.5 text-[12.5px] leading-relaxed">{c.body}</p>
            </div>
          ))}
        </div>

        <div className="border-line-soft bg-panel-elevated mt-3 overflow-hidden rounded-2xl border">
          <div className="border-line-soft flex items-center gap-2 border-b px-5 py-3">
            <Gauge className="text-brand-bright size-3.5" aria-hidden />
            <h3 className="font-display text-[13.5px] font-bold">Business outcomes</h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[46rem] text-left">
              <thead>
                <tr className="text-muted-foreground border-line-soft border-b font-mono text-[10px] tracking-[0.12em] uppercase">
                  <th scope="col" className="px-5 py-2 font-medium">Outcome</th>
                  <th scope="col" className="px-5 py-2 font-medium">How the platform contributes</th>
                  <th scope="col" className="px-5 py-2 font-medium">Evidence / KPI</th>
                </tr>
              </thead>
              <tbody>
                {BUSINESS_OUTCOMES.map((o) => (
                  <tr key={o.outcome} className="border-line-soft border-b last:border-b-0">
                    <th scope="row" className="px-5 py-3 text-[12.5px] font-semibold whitespace-nowrap">
                      {o.outcome}
                    </th>
                    <td className="text-muted-foreground px-5 py-3 text-[12.5px]">{o.contribution}</td>
                    <td className="text-muted-foreground px-5 py-3 font-mono text-[11.5px]">{o.evidence}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </CatalogueSection>

      {/* ── Agents ────────────────────────────────────────────────────────── */}
      <CatalogueSection
        id="agents"
        eyebrow="Agent catalogue"
        title={`${CATALOGUE_AGENTS.length} agents, one control plane`}
        lead="Eight run the forward pipeline. Tracks 3–5 add Discovery & Assessment, Strategy, Migration Mapping, Validation and Data Engineering. An agent appearing in several tracks is one agent with several modes."
        actions={
          <Button
            variant="outline"
            size="sm"
            onClick={() => setFavouritesOnly((v) => !v)}
            aria-pressed={favouritesOnly}
            className={cn("border-line-soft h-8 font-mono text-[11px]", favouritesOnly && "border-brand-bright/40 text-brand-bright")}
          >
            <Star className={cn("size-3.5", favouritesOnly && "fill-current")} aria-hidden />
            Favourites {favourites.items.length > 0 && `(${favourites.items.length})`}
          </Button>
        }
      >
        {/* Filter bar */}
        <div className="border-line-soft bg-panel-elevated mb-4 space-y-3 rounded-2xl border p-4">
          <div className="relative">
            <Search className="text-muted-foreground absolute top-1/2 left-3 size-4 -translate-y-1/2" aria-hidden />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search agents, artifacts, tools — “SBOM”, “Gherkin”, “cutover”…"
              aria-label="Search the catalogue"
              className="border-line-soft bg-surface-1 h-10 pl-9"
            />
            {query && (
              <button
                onClick={() => setQuery("")}
                aria-label="Clear search"
                className="text-muted-foreground hover:text-foreground absolute top-1/2 right-3 -translate-y-1/2"
              >
                <X className="size-4" aria-hidden />
              </button>
            )}
          </div>

          <FilterRow label="Track">
            <FilterChip active={track === null} onClick={() => setTrack(null)}>All</FilterChip>
            {TRACK_ORDER.map((t) => (
              <FilterChip key={t} active={track === t} onClick={() => setTrack(track === t ? null : t)}>
                T{TRACK_META[t].number} {TRACK_META[t].shortLabel}
              </FilterChip>
            ))}
          </FilterRow>

          <FilterRow label="SDLC stage">
            <FilterChip active={phase === null} onClick={() => setPhase(null)}>All</FilterChip>
            {CATALOGUE_AGENTS.map((a) => (
              <FilterChip
                key={a.phase}
                active={phase === a.phase}
                onClick={() => setPhase(phase === a.phase ? null : a.phase)}
              >
                {PHASE_LABEL[a.phase]}
              </FilterChip>
            ))}
          </FilterRow>

          <FilterRow label="Owned by">
            <FilterChip active={persona === null} onClick={() => setPersona(null)}>All</FilterChip>
            {Array.from(new Set(CATALOGUE_AGENTS.map((a) => a.ownerRole))).map((r) => (
              <FilterChip key={r} active={persona === r} onClick={() => setPersona(persona === r ? null : r)}>
                {ROLE_META[r].label}
              </FilterChip>
            ))}
          </FilterRow>

          <div className="flex items-center justify-between gap-3 pt-1">
            <p className="text-muted-foreground font-mono text-[11px]">
              {agents.length} of {CATALOGUE_AGENTS.length} agents
            </p>
            {hasFilter && (
              <button
                onClick={clearAll}
                className="text-brand-bright font-mono text-[11px] underline underline-offset-2"
              >
                Clear filters
              </button>
            )}
          </div>
        </div>

        {recent.items.length > 0 && (
          <div className="mb-4 flex flex-wrap items-center gap-2">
            <span className="text-muted-foreground flex items-center gap-1.5 font-mono text-[10px] tracking-[0.12em] uppercase">
              <Clock className="size-3" aria-hidden />
              Recently viewed
            </span>
            {recent.items.map((p) => (
              <button key={p} onClick={() => openAgent(p as Phase)}>
                <Chip tone="neutral">{PHASE_LABEL[p as Phase]}</Chip>
              </button>
            ))}
          </div>
        )}

        {agents.length === 0 ? (
          <NoResults onClear={clearAll} />
        ) : (
          <div className="grid items-stretch gap-4 md:grid-cols-2 xl:grid-cols-3">
            {agents.map((a, i) => (
              <div key={a.phase} id={`agent-${a.phase}`}>
                <AgentCard
                  agent={a}
                  trackFilter={track}
                  isFavourite={favourites.items.includes(a.phase)}
                  onToggleFavourite={() => favourites.toggle(a.phase)}
                  onOpen={() => recent.push(a.phase)}
                  style={{
                    animationName: "rise",
                    animationDuration: "0.5s",
                    animationTimingFunction: "cubic-bezier(0.2,0.7,0.2,1)",
                    animationFillMode: "both",
                    animationDelay: `${Math.min(i, 8) * 30}ms`,
                  }}
                />
              </div>
            ))}
          </div>
        )}

        <div className="border-line-soft mt-4 rounded-2xl border border-dashed p-5">
          <h3 className="font-display mb-2 text-[13.5px] font-bold">True of every agent, in every track</h3>
          <ul className="grid gap-2 md:grid-cols-2">
            {AGENT_INVARIANTS.map((inv) => (
              <li key={inv} className="text-muted-foreground flex gap-2 text-[12.5px] leading-relaxed">
                <span className="bg-brand-bright/40 mt-[7px] size-1 shrink-0 rounded-full" aria-hidden />
                {inv}
              </li>
            ))}
          </ul>
        </div>
      </CatalogueSection>

      {/* ── Readiness ─────────────────────────────────────────────────────────
          Sits directly under the roster, because "what does it do" and "can I
          run it" are the same question asked twice, and the second one used to
          have no answer anywhere on this page. */}
      <CatalogueSection
        id="readiness"
        eyebrow="Your access"
        title="What each agent needs, and what you hold"
        lead="An agent is only as available as the connectors it reads and writes. This is your scope's standing against each one — not the organization's."
      >
        <AgentReadinessSection />
      </CatalogueSection>

      {/* ── Delivery tracks ───────────────────────────────────────────────── */}
      <CatalogueSection
        id="tracks"
        eyebrow="Delivery track explorer"
        title="Five tracks, one control plane"
        lead="A track is a configurable delivery template — not a separate product. It selects a context model, recommended agents and skills, required artifacts, evaluation suites, risk rules, an approval matrix and metrics."
      >
        <div className="grid gap-4 lg:grid-cols-2">
          {TRACK_ORDER.map((t) => {
            const meta = TRACK_META[t];
            const detail = TRACK_DETAIL[t];
            const roster = agentsInTrack(t);
            const owners = Array.from(new Set(roster.map((a) => a.ownerRole)));
            return (
              <article key={t} className="border-line-soft bg-panel-elevated flex flex-col gap-4 rounded-2xl border p-5">
                <div>
                  <div className="mb-1.5 flex items-center gap-2">
                    <Chip tone="brand">Track {meta.number}</Chip>
                    <Chip>{roster.length} agents</Chip>
                  </div>
                  <h3 className="font-display text-[16px] font-bold tracking-[-0.01em]">{meta.label}</h3>
                  <p className="text-muted-foreground mt-1.5 text-[12.5px] leading-relaxed">{meta.summary}</p>
                </div>

                <LabelledList label="Starts from" items={detail.entryContext} />

                <div>
                  <p className="text-muted-foreground mb-1.5 font-mono text-[10px] tracking-[0.12em] uppercase">
                    Objectives
                  </p>
                  <ul className="space-y-1">
                    {detail.objectives.map((o) => (
                      <li key={o} className="text-[12.5px] leading-relaxed">{o}</li>
                    ))}
                  </ul>
                </div>

                <div>
                  <p className="text-muted-foreground mb-1.5 font-mono text-[10px] tracking-[0.12em] uppercase">
                    Agents, in order
                  </p>
                  <ol className="flex flex-wrap items-center gap-1">
                    {roster.map((a, i) => (
                      <li key={a.phase} className="flex items-center gap-1">
                        <Chip>{a.name}</Chip>
                        {i < roster.length - 1 && (
                          <span className="text-muted-foreground/30 text-[10px]" aria-hidden>→</span>
                        )}
                      </li>
                    ))}
                  </ol>
                </div>

                <div>
                  <p className="text-muted-foreground mb-1.5 font-mono text-[10px] tracking-[0.12em] uppercase">
                    Personas involved
                  </p>
                  <div className="flex flex-wrap gap-1">
                    {owners.map((r) => (
                      <Chip key={r}>{ROLE_META[r].label}</Chip>
                    ))}
                  </div>
                </div>

                <div className="mt-auto space-y-2">
                  <Disclosure summary="Deliverables" count={detail.deliverables.length}>
                    <LabelledList label="Produces" items={detail.deliverables} />
                  </Disclosure>
                  <Disclosure summary="Governance checkpoints" count={detail.checkpoints.length}>
                    <LabelledList label="Gates" items={detail.checkpoints} />
                  </Disclosure>
                </div>
              </article>
            );
          })}
        </div>
      </CatalogueSection>

      {/* ── Capability explorer ───────────────────────────────────────────── */}
      <CatalogueSection
        id="capabilities"
        eyebrow="Capability explorer"
        title="Every action is one of three classes"
        lead="There is no fourth class and no sub-tiering of approvals. Only Consequential actions and Sign-offs ever require a human."
      >
        <div className="grid gap-3 md:grid-cols-3">
          {CAPABILITY_CLASS_ORDER.map((c) => {
            const meta = CAPABILITY_CLASS_META[c];
            const agents = CATALOGUE_AGENTS.filter((a) => a.classes.includes(c));
            return (
              <div key={c} className="border-line-soft bg-panel-elevated flex flex-col gap-3 rounded-2xl border p-5">
                <CapabilityClassChip value={c} className="w-fit" />
                <p className="text-[13px] leading-relaxed font-medium">{meta.meaning}</p>
                <p className="text-muted-foreground text-[12.5px]">{meta.uiBehaviour}</p>
                <div className="mt-auto pt-2">
                  <p className="text-muted-foreground mb-1.5 font-mono text-[10px] tracking-[0.12em] uppercase">
                    {agents.length} agents use this class
                  </p>
                  <div className="flex flex-wrap gap-1">
                    {agents.map((a) => (
                      <Chip key={a.phase}>{a.name}</Chip>
                    ))}
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        <div className="mt-3 grid gap-3 lg:grid-cols-2">
          <div className="border-line-soft bg-panel-elevated rounded-2xl border p-5">
            <h3 className="font-display mb-1 text-[13.5px] font-bold">Autonomy ladder</h3>
            <p className="text-muted-foreground mb-3 text-[12px]">
              The platform&apos;s default today is conversational, human-directed use — L1 to L3. L4 and
              L5 are controlled future capability with eligibility criteria still open.
            </p>
            <ul className="space-y-2">
              {AUTONOMY_LADDER.map((l) => (
                <li key={l.level} className="flex items-start gap-3">
                  <Chip tone={l.available ? "brand" : "neutral"} className={cn(!l.available && "opacity-60")}>
                    {l.level}
                  </Chip>
                  <div className={cn("min-w-0", !l.available && "opacity-60")}>
                    <p className="text-[12.5px] font-semibold">
                      {l.mode}
                      {!l.available && (
                        <span className="text-muted-foreground ml-2 font-mono text-[10px] font-normal">
                          not enabled today
                        </span>
                      )}
                    </p>
                    <p className="text-muted-foreground text-[12px]">{l.useCase}</p>
                  </div>
                </li>
              ))}
            </ul>
          </div>

          <div className="border-line-soft bg-panel-elevated rounded-2xl border p-5">
            <h3 className="font-display mb-1 text-[13.5px] font-bold">Risk tiers</h3>
            <p className="text-muted-foreground mb-3 text-[12px]">
              The tier sets how much control an action attracts, independent of which agent runs it.
            </p>
            <ul className="space-y-2">
              {RISK_TIERS.map((r) => (
                <li key={r.tier} className="flex items-start gap-3">
                  <Chip>{r.tier}</Chip>
                  <p className="text-muted-foreground text-[12.5px] leading-relaxed">{r.definition}</p>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </CatalogueSection>

      {/* ── Workflow mapping ──────────────────────────────────────────────── */}
      <CatalogueSection
        id="workflows"
        eyebrow="Workflow mapping"
        title="How the stages actually run"
        lead="Agents behave as ordered pipeline stages, but progression is user-driven rather than automatic — the rail shows the order, not an automation."
      >
        <WorkflowMap selected={mapTrack} onSelect={setMapTrack} />
      </CatalogueSection>

      {/* ── Personas ──────────────────────────────────────────────────────── */}
      <CatalogueSection
        id="personas"
        eyebrow="Persona-based discovery"
        title="Who owns what"
        lead="Governance and delivery are two tiers that never cross within one scope. The Project Admin is the fallback approver on every agent, so work never stalls."
      >
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {PERSONA_VIEWS.map((p) => {
            const meta = ROLE_META[p.role];
            const owned = agentsOwnedBy(p.role);
            return (
              <div key={p.role} className="border-line-soft bg-panel-elevated flex flex-col gap-2.5 rounded-2xl border p-5">
                <div className="flex items-start justify-between gap-2">
                  <h3 className="font-display text-[14px] font-bold tracking-[-0.01em]">{meta.label}</h3>
                  <Chip tone={meta.governanceOnly ? "brand" : "neutral"}>
                    {meta.governanceOnly ? "Governance" : "Delivery"}
                  </Chip>
                </div>
                <p className="text-muted-foreground text-[12.5px] leading-relaxed">{meta.oneLiner}</p>
                {p.successCondition && (
                  <p className="text-[12px] leading-relaxed italic">
                    <span className="text-muted-foreground not-italic">Succeeds when: </span>
                    {p.successCondition}
                  </p>
                )}
                <div className="mt-auto pt-1">
                  <p className="text-muted-foreground mb-1.5 font-mono text-[10px] tracking-[0.12em] uppercase">
                    Owns these agent gates
                  </p>
                  <PersonaAgentList phases={owned.map((a) => a.phase)} />
                </div>
              </div>
            );
          })}
        </div>
      </CatalogueSection>

      {/* ── Governance ────────────────────────────────────────────────────── */}
      <CatalogueSection
        id="governance"
        eyebrow="Governance & compliance"
        title="The controls wrapped around every agent"
        lead="Consequential actions and formal sign-offs are governed at capability level, with evidence and immutable audit."
      >
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {GOVERNANCE_CONTROLS.map((g) => {
            const body = (
              <>
                <div className="mb-1.5 flex items-center gap-2">
                  <ShieldCheck className="text-brand-bright size-3.5 shrink-0" aria-hidden />
                  <h3 className="font-display text-[13.5px] font-bold">{g.title}</h3>
                </div>
                <p className="text-muted-foreground text-[12.5px] leading-relaxed">{g.body}</p>
                {g.href && (
                  <span className="text-brand-bright mt-2 flex items-center gap-1 font-mono text-[11px]">
                    Open <ArrowRight className="size-3" aria-hidden />
                  </span>
                )}
              </>
            );
            return g.href ? (
              <Link
                key={g.title}
                href={g.href}
                className="border-line-soft bg-panel-elevated hover:border-border rounded-2xl border p-5 transition-colors"
              >
                {body}
              </Link>
            ) : (
              <div key={g.title} className="border-line-soft bg-panel-elevated rounded-2xl border p-5">
                {body}
              </div>
            );
          })}
        </div>

        <div className="border-line-soft bg-panel-elevated mt-3 rounded-2xl border p-5">
          <h3 className="font-display mb-1 text-[13.5px] font-bold">Agent release lifecycle</h3>
          <p className="text-muted-foreground mb-4 text-[12px]">
            Every agent and every skill or template configuration moves through the same six states,
            regardless of track.
          </p>
          <ol className="-mx-1 flex gap-2 overflow-x-auto px-1 pb-1">
            {RELEASE_LIFECYCLE.map((s, i) => (
              <li key={s.state} className="border-line-soft bg-surface-1 w-[168px] shrink-0 rounded-xl border p-3">
                <span className="text-muted-foreground font-mono text-[10px]">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <p className="mt-1 text-[12.5px] font-semibold">{s.state}</p>
                <p className="text-muted-foreground mt-1 text-[11.5px] leading-relaxed">{s.meaning}</p>
              </li>
            ))}
          </ol>
        </div>
      </CatalogueSection>

      {/* ── Learning centre ───────────────────────────────────────────────── */}
      <CatalogueSection
        id="learn"
        eyebrow="Learning centre"
        title="How to get started"
        lead="Four master journeys carry every piece of work through the platform."
      >
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          {MASTER_JOURNEYS.map((j, i) => (
            <div key={j.title} className="border-line-soft bg-panel-elevated rounded-2xl border p-5">
              <div className="text-brand-bright mb-2 flex items-center gap-2">
                <Compass className="size-3.5" aria-hidden />
                <span className="font-mono text-[10px] tracking-[0.12em] uppercase">
                  Journey {i + 1}
                </span>
              </div>
              <h3 className="font-display text-[13.5px] font-bold">{j.title}</h3>
              <p className="text-muted-foreground mt-1.5 text-[12.5px] leading-relaxed">{j.body}</p>
            </div>
          ))}
        </div>

        <ol className="mt-3 space-y-2">
          {GETTING_STARTED.map((s, i) => {
            const inner = (
              <>
                <span className="border-line-soft bg-surface-2 text-muted-foreground grid size-7 shrink-0 place-items-center rounded-full border font-mono text-[11px]">
                  {i + 1}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-[13px] font-semibold">{s.step}</span>
                  <span className="text-muted-foreground block text-[12.5px] leading-relaxed">
                    {s.body}
                  </span>
                </span>
                {s.href && <ArrowRight className="text-muted-foreground mt-1 size-3.5 shrink-0" aria-hidden />}
              </>
            );
            return (
              <li key={s.step}>
                {s.href ? (
                  <Link
                    href={s.href}
                    className="border-line-soft bg-panel-elevated hover:border-border flex items-start gap-3 rounded-xl border p-4 transition-colors"
                  >
                    {inner}
                  </Link>
                ) : (
                  <div className="border-line-soft bg-panel-elevated flex items-start gap-3 rounded-xl border p-4">
                    {inner}
                  </div>
                )}
              </li>
            );
          })}
        </ol>
      </CatalogueSection>

      <p className="text-muted-foreground border-line-soft border-t pt-6 text-center font-mono text-[11px]">
        <Workflow className="mr-1.5 inline size-3" aria-hidden />
        Every entry on this page is derived from the platform&apos;s product documentation and its
        own agent, track and role registries. Nothing here is illustrative.
      </p>
    </div>
  );
}

// ─── Filter bar pieces ────────────────────────────────────────────────────────

function FilterRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-muted-foreground w-[5.5rem] shrink-0 font-mono text-[10px] tracking-[0.12em] uppercase">
        {label}
      </span>
      {children}
    </div>
  );
}

function FilterChip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "rounded-full border px-2.5 py-1 font-mono text-[11px] transition-colors",
        active
          ? "border-brand-bright/40 bg-brand-bright/10 text-brand-bright"
          : "border-line-soft text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}
