"use client";

import * as React from "react";
import { Check, ChevronRight, Clock, Lock, ShieldAlert } from "lucide-react";

import { cn } from "@/lib/utils";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { StatusBadge } from "@/components/ui/status-badge";
import type { DeployEnv, DeployEnvName } from "@/lib/schemas";

export interface EnvPromotionProps {
  envs: readonly DeployEnv[];
  /** Used as the magic word for the prod confirmation. */
  projectSlug: string;
  /** Called when an env's approve button fires. */
  onApprove?: (env: DeployEnv) => void | Promise<void>;
  /** Disable actions while a mutation is in flight. */
  busy?: boolean;
  /** Hide the approve buttons entirely — e.g. when viewing read-only. */
  readOnly?: boolean;
  className?: string;
}

const ENV_ORDER: DeployEnvName[] = ["dev", "staging", "prod"];

const ENV_LABEL: Record<DeployEnvName, string> = {
  dev: "Development",
  staging: "Staging",
  prod: "Production",
};

export function EnvPromotion({
  envs,
  projectSlug,
  onApprove,
  busy,
  readOnly,
  className,
}: EnvPromotionProps) {
  const ordered = React.useMemo(
    () =>
      [...envs].sort(
        (a, b) => ENV_ORDER.indexOf(a.name) - ENV_ORDER.indexOf(b.name),
      ),
    [envs],
  );

  const [confirmEnv, setConfirmEnv] = React.useState<DeployEnv | null>(null);

  const requestApprove = (env: DeployEnv) => {
    if (env.name === "prod") {
      setConfirmEnv(env);
      return;
    }
    void onApprove?.(env);
  };

  return (
    <>
      <ol
        className={cn(
          "grid grid-cols-1 gap-2 md:grid-cols-[1fr_auto_1fr_auto_1fr] md:items-stretch md:gap-0",
          className,
        )}
        aria-label="Environment promotion"
      >
        {ordered.map((env, i) => (
          <React.Fragment key={env.name}>
            {i > 0 && (
              <li
                aria-hidden
                className="text-muted-foreground hidden items-center justify-center md:flex"
              >
                <ChevronRight className="size-5" />
              </li>
            )}
            <li className="flex">
              <EnvCard
                env={env}
                onApprove={requestApprove}
                busy={busy}
                readOnly={readOnly}
              />
            </li>
          </React.Fragment>
        ))}
      </ol>

      <ProdConfirmDialog
        env={confirmEnv}
        projectSlug={projectSlug}
        onClose={() => setConfirmEnv(null)}
        onConfirm={(env) => {
          setConfirmEnv(null);
          void onApprove?.(env);
        }}
      />
    </>
  );
}

// ───────── env card ─────────

function EnvCard({
  env,
  onApprove,
  busy,
  readOnly,
}: {
  env: DeployEnv;
  onApprove: (env: DeployEnv) => void;
  busy?: boolean;
  readOnly?: boolean;
}) {
  const isProd = env.name === "prod";
  const canApprove =
    !readOnly &&
    !busy &&
    env.status === "awaiting_approval" &&
    env.requiresApproval;

  return (
    <section
      className={cn(
        "bg-card flex flex-1 flex-col gap-2 rounded-md border p-3",
        isProd && "border-destructive/30",
      )}
      aria-label={ENV_LABEL[env.name]}
    >
      <header className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="font-mono text-xs font-semibold uppercase tracking-wider">
            {env.name}
          </span>
          {isProd && <Lock className="text-destructive size-3" aria-hidden />}
        </div>
        <StatusBadge status={env.status} />
      </header>

      <dl className="text-muted-foreground space-y-1 text-[11px]">
        {env.commitSha && (
          <div className="flex items-baseline gap-2">
            <dt className="w-14 shrink-0 uppercase tracking-wider">commit</dt>
            <dd className="text-foreground truncate font-mono">
              {env.commitSha.slice(0, 7)}
            </dd>
          </div>
        )}
        {env.deployedAt && (
          <div className="flex items-baseline gap-2">
            <dt className="w-14 shrink-0 uppercase tracking-wider">deployed</dt>
            <dd className="text-foreground inline-flex items-center gap-1">
              <Clock className="size-3" aria-hidden />
              {new Date(env.deployedAt).toLocaleString(undefined, {
                dateStyle: "medium",
                timeStyle: "short",
              })}
            </dd>
          </div>
        )}
        {env.approvedBy && (
          <div className="flex items-baseline gap-2">
            <dt className="w-14 shrink-0 uppercase tracking-wider">by</dt>
            <dd className="text-foreground inline-flex items-center gap-1.5">
              <Avatar className="size-4">
                <AvatarFallback className="text-[8px]">
                  {env.approvedBy.initials}
                </AvatarFallback>
              </Avatar>
              {env.approvedBy.name}
            </dd>
          </div>
        )}
      </dl>

      {canApprove && (
        <Button
          size="sm"
          variant={isProd ? "destructive" : "default"}
          onClick={() => onApprove(env)}
          aria-busy={busy}
          className="mt-1"
        >
          {isProd ? <ShieldAlert aria-hidden /> : <Check aria-hidden />}
          Approve {ENV_LABEL[env.name]}
        </Button>
      )}
      {!canApprove && env.status === "awaiting_approval" && (
        <p className="text-muted-foreground text-xs italic">Awaiting upstream approval…</p>
      )}
    </section>
  );
}

// ───────── prod confirmation ─────────

function ProdConfirmDialog({
  env,
  projectSlug,
  onClose,
  onConfirm,
}: {
  env: DeployEnv | null;
  projectSlug: string;
  onClose: () => void;
  onConfirm: (env: DeployEnv) => void;
}) {
  const [typed, setTyped] = React.useState("");
  const open = !!env;

  React.useEffect(() => {
    if (!open) setTyped("");
  }, [open]);

  const matches = typed.trim() === projectSlug;

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ShieldAlert className="text-destructive size-5" aria-hidden />
            Approve production deploy
          </DialogTitle>
          <DialogDescription>
            This gate is <strong>non-overridable</strong>. Your CD will apply the
            pipeline PR to production after this approval. Rollback is a
            separate action if needed.
          </DialogDescription>
        </DialogHeader>

        <Alert variant="destructive">
          <ShieldAlert />
          <AlertTitle>Check before approving</AlertTitle>
          <AlertDescription className="space-y-1 text-xs">
            <ul className="list-inside list-disc">
              <li>All upstream envs have deployed cleanly.</li>
              <li>The generated IaC diff has been reviewed.</li>
              <li>A rollback plan is on file.</li>
            </ul>
          </AlertDescription>
        </Alert>

        <div className="space-y-2">
          <Label htmlFor="prod-confirm-slug">
            Type the project slug{" "}
            <code className="bg-muted rounded px-1 font-mono text-xs">
              {projectSlug}
            </code>{" "}
            to confirm.
          </Label>
          <Input
            id="prod-confirm-slug"
            autoFocus
            autoComplete="off"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder={projectSlug}
            aria-invalid={typed.length > 0 && !matches}
          />
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            disabled={!matches || !env}
            onClick={() => env && onConfirm(env)}
          >
            Approve prod deploy
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
