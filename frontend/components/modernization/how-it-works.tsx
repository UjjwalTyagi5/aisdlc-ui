import * as React from "react";

import { Button } from "@/components/ui/button";

/**
 * What a Track 3 page shows until a version is opened: the journey, step by step, with
 * the two actions that start it — pulling the legacy code and running the agent.
 */
export type HowItWorksStep = {
  title: string;
  body: string;
  action?: { label: string; onClick: () => void; disabled?: boolean };
  /** A line of live status under the step, e.g. what is pulled right now. */
  status?: React.ReactNode;
};

export function HowItWorks({ title, steps }: { title: string; steps: HowItWorksStep[] }) {
  return (
    <section aria-label={title} className="mx-auto max-w-2xl space-y-5 py-4">
      <h2 className="font-display text-lg font-semibold tracking-tight">{title}</h2>
      <ol className="space-y-4">
        {steps.map((step, i) => (
          <li key={step.title} className="flex gap-4 rounded-lg border p-4">
            <span
              aria-hidden
              className="bg-primary/10 text-primary flex size-7 shrink-0 items-center justify-center rounded-full text-sm font-semibold"
            >
              {i + 1}
            </span>
            <div className="min-w-0 flex-1 space-y-2">
              <p className="text-sm font-medium">{step.title}</p>
              <p className="text-muted-foreground text-sm">{step.body}</p>
              {step.status && <div>{step.status}</div>}
              {step.action && (
                <Button size="sm" variant="outline" onClick={step.action.onClick} disabled={step.action.disabled}>
                  {step.action.label}
                </Button>
              )}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
