"use client";

import * as React from "react";
import { AlertTriangle, Ban, ShieldAlert, Wallet } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

export type GuardrailKind = "pii" | "prompt_injection" | "policy" | "budget";

const CONFIG: Record<
  GuardrailKind,
  {
    variant: "warning" | "destructive" | "info";
    icon: React.ComponentType<{ className?: string }>;
    title: string;
  }
> = {
  pii: { variant: "warning", icon: ShieldAlert, title: "PII detected in output" },
  prompt_injection: {
    variant: "destructive",
    icon: AlertTriangle,
    title: "Possible prompt injection",
  },
  policy: { variant: "destructive", icon: Ban, title: "Blocked by policy" },
  budget: { variant: "warning", icon: Wallet, title: "Budget near cap" },
};

export interface GuardrailBannerProps {
  kind: GuardrailKind;
  message: string;
  blocked?: boolean;
  onReview?: () => void;
  onDismiss?: () => void;
  className?: string;
}

export function GuardrailBanner({
  kind,
  message,
  blocked,
  onReview,
  onDismiss,
  className,
}: GuardrailBannerProps) {
  const { variant, icon: Icon, title } = CONFIG[kind];
  return (
    <Alert variant={variant} className={className}>
      <Icon />
      <AlertTitle>
        {title}
        {blocked && (
          <span className="text-destructive ml-2 font-mono text-[10px] uppercase">
            Blocked
          </span>
        )}
      </AlertTitle>
      <AlertDescription className="flex flex-col gap-2">
        <span>{message}</span>
        {(onReview || onDismiss) && (
          <div className="flex gap-2">
            {onReview && (
              <Button variant="outline" size="sm" onClick={onReview}>
                Review
              </Button>
            )}
            {onDismiss && (
              <Button variant="ghost" size="sm" onClick={onDismiss}>
                Dismiss
              </Button>
            )}
          </div>
        )}
      </AlertDescription>
    </Alert>
  );
}
