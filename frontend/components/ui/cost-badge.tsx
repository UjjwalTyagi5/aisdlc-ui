import * as React from "react";
import { Coins } from "lucide-react";

import { cn } from "@/lib/utils";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

export interface CostBadgeProps {
  /** Total LLM cost in USD */
  usd: number;
  /** Total tokens (input + output) */
  tokens?: number;
  /** Show a tooltip breakdown */
  inputTokens?: number;
  outputTokens?: number;
  className?: string;
  /** Visual density — "inline" for tables, "card" for detail surfaces */
  density?: "inline" | "card";
}

function formatTokens(n: number) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function formatUsd(n: number) {
  return n >= 1
    ? `$${n.toFixed(2)}`
    : `$${n.toFixed(n < 0.01 ? 4 : 3)}`;
}

export function CostBadge({
  usd,
  tokens,
  inputTokens,
  outputTokens,
  className,
  density = "inline",
}: CostBadgeProps) {
  const body = (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border font-mono text-xs",
        density === "inline" ? "px-1.5 py-0.5" : "px-2.5 py-1",
        "bg-muted text-foreground",
        className,
      )}
    >
      <Coins className="size-3 opacity-70" aria-hidden />
      <span>{formatUsd(usd)}</span>
      {tokens !== undefined && (
        <span className="text-muted-foreground">· {formatTokens(tokens)} tok</span>
      )}
    </span>
  );

  if (inputTokens === undefined && outputTokens === undefined) {
    return body;
  }

  return (
    <Tooltip>
      <TooltipTrigger asChild>{body}</TooltipTrigger>
      <TooltipContent className="font-mono text-[11px]">
        {inputTokens !== undefined && <div>in: {formatTokens(inputTokens)}</div>}
        {outputTokens !== undefined && <div>out: {formatTokens(outputTokens)}</div>}
        <div>usd: {formatUsd(usd)}</div>
      </TooltipContent>
    </Tooltip>
  );
}
