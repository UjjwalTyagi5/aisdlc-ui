"use client";

import * as React from "react";
import {
  CheckCircle2,
  CircleDashed,
  FlaskConical,
  Layers,
  Shuffle,
  TestTubes,
  XCircle,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";
import type { TestCase, TestCaseStatus, TestSuite } from "@/lib/schemas";

const SUITE_ICON: Record<TestSuite, LucideIcon> = {
  unit: FlaskConical,
  integration: Layers,
  e2e: TestTubes,
};

const SUITE_LABEL: Record<TestSuite, string> = {
  unit: "Unit",
  integration: "Integration",
  e2e: "E2E",
};

const STATUS_ICON: Record<TestCaseStatus, LucideIcon> = {
  passing: CheckCircle2,
  failing: XCircle,
  flaky: Shuffle,
  skipped: CircleDashed,
};

const STATUS_TONE: Record<TestCaseStatus, string> = {
  passing: "text-success",
  failing: "text-destructive",
  flaky: "text-warning",
  skipped: "text-muted-foreground",
};

export interface TestSuiteListProps {
  tests: readonly TestCase[];
  selectedId?: string;
  onSelect?: (t: TestCase) => void;
  className?: string;
}

export function TestSuiteList({
  tests,
  selectedId,
  onSelect,
  className,
}: TestSuiteListProps) {
  const groups = React.useMemo(() => {
    const g: Record<TestSuite, TestCase[]> = { unit: [], integration: [], e2e: [] };
    for (const t of tests) g[t.suite].push(t);
    return g;
  }, [tests]);

  return (
    <div className={cn("flex flex-col gap-3", className)}>
      {(Object.entries(groups) as Array<[TestSuite, TestCase[]]>).map(([suite, items]) => {
        const Icon = SUITE_ICON[suite];
        const failing = items.filter((i) => i.status === "failing").length;
        const flaky = items.filter((i) => i.status === "flaky").length;
        return (
          <section key={suite} aria-labelledby={`tests-${suite}-heading`}>
            <h3
              id={`tests-${suite}-heading`}
              className="text-muted-foreground mb-1 flex items-center justify-between text-xs font-semibold uppercase tracking-wider"
            >
              <span className="inline-flex items-center gap-1.5">
                <Icon className="size-3" aria-hidden />
                {SUITE_LABEL[suite]} <span className="opacity-60">· {items.length}</span>
              </span>
              {(failing > 0 || flaky > 0) && (
                <span className="font-mono">
                  {failing > 0 && (
                    <span className="text-destructive">{failing} failing</span>
                  )}
                  {failing > 0 && flaky > 0 && "  ·  "}
                  {flaky > 0 && <span className="text-warning">{flaky} flaky</span>}
                </span>
              )}
            </h3>
            {items.length === 0 ? (
              <p className="text-muted-foreground px-1 text-xs italic">None yet</p>
            ) : (
              <ul className="space-y-0.5">
                {items.map((t) => {
                  const StatusIcon = STATUS_ICON[t.status];
                  const active = selectedId === t.id;
                  return (
                    <li key={t.id}>
                      <button
                        type="button"
                        onClick={() => onSelect?.(t)}
                        className={cn(
                          "flex w-full items-center gap-2 rounded-md border px-2 py-1.5 text-left transition-colors",
                          "hover:bg-accent",
                          "focus-visible:ring-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-1",
                          active
                            ? "bg-accent border-foreground/20"
                            : "border-transparent",
                        )}
                      >
                        <StatusIcon
                          className={cn("size-3.5 shrink-0", STATUS_TONE[t.status])}
                          aria-hidden
                        />
                        <div className="flex min-w-0 flex-1 flex-col">
                          <span className="truncate text-sm">{t.name}</span>
                          <span className="text-muted-foreground truncate font-mono text-[10px]">
                            {t.path}
                          </span>
                        </div>
                        <MiniSparkline history={t.history} />
                        {t.durationMs !== undefined && t.status !== "skipped" && (
                          <span className="text-muted-foreground w-10 text-right font-mono text-[10px]">
                            {t.durationMs < 1000
                              ? `${t.durationMs}ms`
                              : `${(t.durationMs / 1000).toFixed(1)}s`}
                          </span>
                        )}
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        );
      })}
    </div>
  );
}

function MiniSparkline({ history }: { history: readonly boolean[] }) {
  if (history.length === 0) return <span className="w-16" aria-hidden />;
  return (
    <div
      className="flex items-end gap-[1px]"
      role="img"
      aria-label={`Last ${history.length} runs`}
    >
      {history.map((ok, i) => (
        <span
          key={i}
          className={cn(
            "w-1.5 rounded-[1px]",
            ok ? "bg-success/70" : "bg-destructive/80",
          )}
          style={{ height: ok ? 10 : 14 }}
        />
      ))}
    </div>
  );
}
