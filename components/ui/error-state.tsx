import * as React from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

export interface ErrorStateProps {
  title?: string;
  description?: React.ReactNode;
  /** Optional technical detail — hidden by default, toggled by "Show details" */
  detail?: string;
  onRetry?: () => void;
  retryLabel?: string;
  className?: string;
  variant?: "plain" | "card";
}

export function ErrorState({
  title = "Something went wrong",
  description = "We couldn't load this. Please try again, and if it keeps failing, contact support.",
  detail,
  onRetry,
  retryLabel = "Retry",
  className,
  variant = "card",
}: ErrorStateProps) {
  const [showDetail, setShowDetail] = React.useState(false);

  return (
    <div
      role="alert"
      aria-live="assertive"
      className={cn(
        "flex flex-col items-center justify-center gap-3 px-6 py-10 text-center",
        variant === "card" && "border-destructive/30 bg-destructive/5 rounded-lg border",
        className,
      )}
    >
      <div className="bg-destructive/10 text-destructive grid size-10 place-items-center rounded-full">
        <AlertTriangle className="size-5" aria-hidden />
      </div>
      <div className="space-y-1">
        <h3 className="text-base font-semibold">{title}</h3>
        <p className="text-muted-foreground mx-auto max-w-md text-sm">{description}</p>
      </div>
      <div className="flex flex-wrap items-center justify-center gap-2">
        {onRetry && (
          <Button variant="outline" size="sm" onClick={onRetry}>
            <RefreshCw className="size-3.5" />
            {retryLabel}
          </Button>
        )}
        {detail && (
          <Button variant="ghost" size="sm" onClick={() => setShowDetail((v) => !v)}>
            {showDetail ? "Hide details" : "Show details"}
          </Button>
        )}
      </div>
      {showDetail && detail && (
        <pre className="bg-muted text-muted-foreground mx-auto mt-2 max-w-full overflow-auto rounded-md p-3 text-left font-mono text-[11px]">
          {detail}
        </pre>
      )}
    </div>
  );
}
