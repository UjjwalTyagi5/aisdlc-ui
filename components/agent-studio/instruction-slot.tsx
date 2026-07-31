"use client";

import * as React from "react";
import { AlertCircle } from "lucide-react";

import { cn } from "@/lib/utils";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { LintViolation } from "@/lib/schemas/agent-profiles";

export interface InstructionSlotProps {
  id: string;
  label: string;
  helper: string;
  value: string;
  cap: number;
  disabled?: boolean;
  readOnly?: boolean;
  onChange: (value: string) => void;
  /** Lint violations returned by the server for THIS field only. */
  violations?: LintViolation[];
  rows?: number;
}

/**
 * One bounded instruction field: labelled textarea with a live character
 * counter (turns destructive past the cap) and inline server-side lint
 * violations. The cap is a soft client mirror — the server is authoritative,
 * so we never truncate input, only warn.
 */
export function InstructionSlot({
  id,
  label,
  helper,
  value,
  cap,
  disabled,
  readOnly,
  onChange,
  violations,
  rows = 4,
}: InstructionSlotProps) {
  const over = value.length > cap;
  const helperId = `${id}-helper`;
  const errorId = `${id}-error`;
  const hasErrors = Boolean(violations && violations.length > 0);

  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between gap-3">
        <Label htmlFor={id} className="text-sm font-medium">
          {label}
        </Label>
        <span
          className={cn(
            "font-mono text-[11px] tabular-nums",
            over ? "text-destructive font-semibold" : "text-muted-foreground",
          )}
          aria-live="polite"
        >
          {value.length.toLocaleString()} / {cap.toLocaleString()}
        </span>
      </div>

      <Textarea
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        readOnly={readOnly}
        rows={rows}
        aria-describedby={hasErrors ? `${helperId} ${errorId}` : helperId}
        aria-invalid={over || hasErrors}
        className={cn(
          "resize-y",
          (over || hasErrors) &&
            "border-destructive focus-visible:ring-destructive/40",
          readOnly && "bg-muted/40",
        )}
      />

      <p id={helperId} className="text-muted-foreground text-xs">
        {helper}
      </p>

      {hasErrors && (
        <ul id={errorId} className="space-y-1">
          {violations!.map((v, idx) => (
            <li
              key={`${v.code}-${idx}`}
              className="text-destructive flex items-start gap-1.5 text-xs"
            >
              <AlertCircle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
              <span>{v.message}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
