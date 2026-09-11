"use client";

import { ArrowRight, FolderGit2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { MigrationIntentBrief } from "@/lib/schemas/modernization";

/**
 * The migration-intent brief, as the Requirements agent recorded it (Track 3).
 *
 * Laid out in the order the brief is taken — why, from → to, scope, constraints,
 * success — because that is the order a reviewer baselines it in, and every later
 * agent reads a part of it: Discovery the target stack and the repository, Strategy
 * the constraints, Testing the success criteria.
 */
export function MigrationBriefCard({
  brief,
  updatedAt,
}: {
  brief: MigrationIntentBrief;
  updatedAt?: string | null;
}) {
  const repo = brief.legacy_repository;
  // The brief's own time. `updatedAt` is its RUN's last change, which moves whenever
  // anything else writes to that run (Discovery, in the same Orchestrator chat).
  const recordedAt = brief.recorded_at ?? updatedAt;
  return (
    <article className="space-y-6" aria-label="Migration-intent brief">
      <header className="space-y-1">
        <p className="text-muted-foreground font-mono text-[10.5px] tracking-[0.14em] uppercase">
          Migration-intent brief
        </p>
        <h2 className="font-display text-xl font-semibold tracking-tight">
          {brief.system_name || "Unnamed system"}
        </h2>
        {recordedAt && (
          <p className="text-muted-foreground font-mono text-[11px]">
            Recorded {new Date(recordedAt).toLocaleString()}
          </p>
        )}
      </header>

      <section aria-labelledby="brief-from-to" className="rounded-lg border p-4">
        <h3 id="brief-from-to" className="sr-only">From and to</h3>
        <div className="grid items-start gap-3 sm:grid-cols-[1fr_auto_1fr]">
          <StackSide label="Today" stack={brief.current_state.stack} note={brief.current_state.description} />
          <ArrowRight className="text-muted-foreground mt-6 hidden size-5 sm:block" aria-hidden />
          <StackSide label="Target" stack={brief.target_state.stack} note={brief.target_state.description} />
        </div>
      </section>

      <BriefList title="Why this modernization is happening" items={brief.business_drivers} />
      <div className="grid gap-6 md:grid-cols-2">
        <BriefList title="In scope" items={brief.in_scope} />
        <BriefList title="Out of scope" items={brief.out_of_scope} empty="Nothing excluded yet." />
      </div>
      <BriefList title="Constraints" items={brief.constraints} />
      <BriefList title="Success criteria" items={brief.success_criteria} />

      <section className="space-y-2">
        <SectionTitle>Legacy repository</SectionTitle>
        {repo && (repo.url || repo.name) ? (
          <p className="flex items-center gap-2 text-sm">
            <FolderGit2 className="text-muted-foreground size-4" aria-hidden />
            <span>{[repo.provider, repo.project, repo.name].filter(Boolean).join(" / ") || repo.url}</span>
            {repo.url && <code className="text-muted-foreground truncate text-xs">{repo.url}</code>}
          </p>
        ) : (
          <p className="text-muted-foreground text-sm italic">
            Not named yet — Discovery & Assessment will ask which repository to clone.
          </p>
        )}
      </section>

      {brief.stakeholders.length > 0 && (
        <section className="space-y-2">
          <SectionTitle>Stakeholders</SectionTitle>
          <ul className="flex flex-wrap gap-2">
            {brief.stakeholders.map((s) => (
              <li key={`${s.name}-${s.role}`}>
                <Badge variant="outline" className="font-normal">
                  {s.name}
                  {s.role && <span className="text-muted-foreground ml-1">· {s.role}</span>}
                </Badge>
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="grid gap-6 md:grid-cols-3">
        <BriefList title="Assumptions" items={brief.assumptions} empty="None recorded." />
        <BriefList title="Risks" items={brief.risks} empty="None recorded." />
        <BriefList title="Open questions" items={brief.open_questions} empty="None." />
      </div>
    </article>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="text-muted-foreground text-xs font-semibold tracking-wider uppercase">{children}</h3>
  );
}

function StackSide({ label, stack, note }: { label: string; stack: string; note: string }) {
  return (
    <div className="space-y-1">
      <p className="text-muted-foreground font-mono text-[10.5px] tracking-[0.12em] uppercase">{label}</p>
      <p className="text-sm font-medium">{stack || "Not stated"}</p>
      {note && <p className="text-muted-foreground text-xs">{note}</p>}
    </div>
  );
}

function BriefList({ title, items, empty = "Not answered yet." }: {
  title: string;
  items: readonly string[];
  empty?: string;
}) {
  const clean = items.filter((i) => i.trim());
  return (
    <section className="space-y-2">
      <SectionTitle>{title}</SectionTitle>
      {clean.length ? (
        <ul className="list-disc space-y-1 pl-5 text-sm">
          {clean.map((item, i) => (
            <li key={i}>{item}</li>
          ))}
        </ul>
      ) : (
        <p className="text-muted-foreground text-sm italic">{empty}</p>
      )}
    </section>
  );
}
