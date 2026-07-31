"use client";

import * as React from "react";
import { Bot, History, RefreshCw, ShieldQuestion, XCircle } from "lucide-react";

import { cn } from "@/lib/utils";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import type { TestCase } from "@/lib/schemas";

export interface FailingTestTriageProps {
  test: TestCase;
  onAskAgent?: () => void;
  onMarkFlaky?: () => void;
  onRerun?: () => void;
  /** Set while any action is in flight. */
  busy?: boolean;
  className?: string;
}

export function FailingTestTriage({
  test,
  onAskAgent,
  onMarkFlaky,
  onRerun,
  busy,
  className,
}: FailingTestTriageProps) {
  if (test.status !== "failing" && test.status !== "flaky") return null;
  return (
    <Alert variant={test.status === "failing" ? "destructive" : "warning"} className={className}>
      {test.status === "failing" ? <XCircle /> : <ShieldQuestion />}
      <AlertTitle className="flex items-center gap-2">
        {test.status === "failing" ? "Test failed" : "Flaky test"}
        <span className="text-muted-foreground font-mono text-[10px] uppercase tracking-wider">
          {test.path}
        </span>
      </AlertTitle>
      <AlertDescription className="space-y-3">
        {test.failure?.message && (
          <p className="font-mono text-xs">{test.failure.message}</p>
        )}
        {test.failure?.stack && (
          <pre
            className={cn(
              "bg-background/60 max-h-52 overflow-auto rounded-md border p-2",
              "font-mono text-[11px] leading-snug",
            )}
          >
            {test.failure.stack}
          </pre>
        )}
        {test.failure?.lastPassingVersion && (
          <p className="text-xs">
            <History className="mr-1 inline size-3" aria-hidden />
            Last passing:{" "}
            <span className="font-mono">{test.failure.lastPassingVersion}</span>
            {test.failure.lastPassingAt && (
              <span className="text-muted-foreground">
                {" · "}
                {new Date(test.failure.lastPassingAt).toLocaleString(undefined, {
                  dateStyle: "medium",
                })}
              </span>
            )}
          </p>
        )}
        <div className="flex flex-wrap gap-2 pt-1">
          {onAskAgent && (
            <Button size="sm" onClick={onAskAgent} disabled={busy}>
              <Bot className="size-3.5" aria-hidden />
              Ask agent to fix
            </Button>
          )}
          {onRerun && (
            <Button variant="outline" size="sm" onClick={onRerun} disabled={busy} aria-busy={busy}>
              <RefreshCw
                className={cn("size-3.5", busy && "animate-spin")}
                aria-hidden
              />
              Re-run
            </Button>
          )}
          {onMarkFlaky && test.status === "failing" && (
            <Button variant="ghost" size="sm" onClick={onMarkFlaky} disabled={busy}>
              Mark as flaky
            </Button>
          )}
        </div>
      </AlertDescription>
    </Alert>
  );
}
