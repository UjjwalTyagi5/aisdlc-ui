"use client";

import * as React from "react";
import {
  ChevronDown,
  ChevronRight,
  ExternalLink,
  GitPullRequest,
  RotateCcw,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { AdrViewer } from "@/components/app/adr-viewer";
import { EnvPromotion } from "@/components/app/env-promotion";
import type { DeployEnv, DeployPlanBody } from "@/lib/schemas";

export interface DeployPlanProps {
  body: DeployPlanBody;
  projectSlug: string;
  onApproveEnv?: (env: DeployEnv) => void | Promise<void>;
  busy?: boolean;
  readOnly?: boolean;
  className?: string;
}

export function DeployPlan({
  body,
  projectSlug,
  onApproveEnv,
  busy,
  readOnly,
  className,
}: DeployPlanProps) {
  const [rollbackOpen, setRollbackOpen] = React.useState(false);

  return (
    <div className={cn("space-y-4", className)}>
      {/* Linked PR header */}
      {body.prUrl && (
        <a
          href={body.prUrl}
          target="_blank"
          rel="noreferrer noopener"
          className="bg-card hover:border-foreground/20 flex items-center gap-3 rounded-md border p-3 transition-colors"
        >
          <GitPullRequest className="text-muted-foreground size-4" aria-hidden />
          <div className="flex min-w-0 flex-col">
            <span className="text-xs font-semibold uppercase tracking-wider">
              Dry-run PR
            </span>
            <span className="text-muted-foreground truncate font-mono text-xs">
              {body.prUrl.replace(/^https?:\/\//, "")}
            </span>
          </div>
          <ExternalLink className="text-muted-foreground ml-auto size-3.5" aria-hidden />
        </a>
      )}

      {/* Environment promotion */}
      <EnvPromotion
        envs={body.envs}
        projectSlug={projectSlug}
        onApprove={onApproveEnv}
        busy={busy}
        readOnly={readOnly}
      />

      {/* Rollback */}
      {body.rollback && (
        <section className="bg-card rounded-md border">
          <button
            type="button"
            onClick={() => setRollbackOpen((o) => !o)}
            className="hover:bg-accent flex w-full items-center gap-2 rounded-md px-3 py-2 text-left"
            aria-expanded={rollbackOpen}
          >
            {rollbackOpen ? (
              <ChevronDown className="text-muted-foreground size-4" aria-hidden />
            ) : (
              <ChevronRight className="text-muted-foreground size-4" aria-hidden />
            )}
            <RotateCcw className="text-muted-foreground size-3.5" aria-hidden />
            <span className="text-sm font-medium">Rollback plan</span>
            <span className="text-muted-foreground ml-auto text-xs">
              click to {rollbackOpen ? "collapse" : "expand"}
            </span>
          </button>
          {rollbackOpen && (
            <div className="border-t p-3">
              <AdrViewer markdown={body.rollback.markdown} className="border-0 p-0" />
            </div>
          )}
        </section>
      )}
    </div>
  );
}
