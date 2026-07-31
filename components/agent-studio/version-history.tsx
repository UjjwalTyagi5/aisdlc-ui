"use client";

import * as React from "react";
import { formatDistanceToNow } from "date-fns";
import { Eye, History, Loader2, Upload } from "lucide-react";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { diffLines, hasLineChanges, type DiffLine } from "@/lib/diff";
import type { AgentProfileVersion } from "@/lib/schemas/agent-profiles";

const FIELD_LABELS: { key: keyof AgentProfileVersion; label: string }[] = [
  { key: "prompt_prepend", label: "Behavior instructions" },
  { key: "prompt_append", label: "Additional output rules" },
  { key: "output_contract_extra", label: "Output contract extras" },
];

function relTime(iso: string | null): string {
  if (!iso) return "unknown time";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "unknown time";
  return formatDistanceToNow(d, { addSuffix: true });
}

export interface VersionHistoryProps {
  versions: AgentProfileVersion[];
  /** The currently active version — diff baseline for the View dialog. */
  activeVersion: AgentProfileVersion | null;
  canManage: boolean;
  /** Called when a row's Publish/Roll-back action is pressed. */
  onRequestPublish: (version: AgentProfileVersion) => void;
  /** id of the version currently being published (busy state). */
  publishingId: string | null;
}

export function VersionHistory({
  versions,
  activeVersion,
  canManage,
  onRequestPublish,
  publishingId,
}: VersionHistoryProps) {
  const [viewing, setViewing] = React.useState<AgentProfileVersion | null>(null);

  return (
    <section aria-labelledby="history-heading" className="space-y-3">
      <div className="flex items-center gap-2">
        <History className="text-muted-foreground size-3.5" aria-hidden />
        <h3
          id="history-heading"
          className="text-xs font-semibold uppercase tracking-wider"
        >
          Version history
        </h3>
      </div>

      {versions.length === 0 ? (
        <p className="text-muted-foreground border-line-soft rounded-lg border border-dashed px-3 py-6 text-center text-xs">
          No versions yet. Saving a draft creates version 1.
        </p>
      ) : (
        <ul className="divide-line-soft border-line-soft divide-y rounded-lg border">
          {versions.map((v) => {
            const isActive = v.is_active;
            const busy = publishingId === v.id;
            return (
              <li
                key={v.id}
                className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-3 py-2.5"
              >
                <span className="font-mono text-xs font-semibold tabular-nums">
                  v{v.version}
                </span>
                {isActive && (
                  <Badge variant="success" className="text-[10px]">
                    Active
                  </Badge>
                )}
                <span className="text-muted-foreground text-xs">
                  {relTime(v.created_at)}
                </span>
                {v.created_by && (
                  <span
                    className="text-muted-foreground/80 max-w-[12rem] truncate text-[11px]"
                    title={v.created_by}
                  >
                    · Created by {v.created_by}
                  </span>
                )}
                {v.published_by && v.published_by !== v.created_by && (
                  <span
                    className="text-muted-foreground/80 max-w-[12rem] truncate text-[11px]"
                    title={v.published_by}
                  >
                    · Approved by {v.published_by}
                  </span>
                )}
                <div className="ml-auto flex items-center gap-1.5">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setViewing(v)}
                    aria-label={`View version ${v.version}`}
                  >
                    <Eye className="size-3.5" aria-hidden />
                    View
                  </Button>
                  {!isActive && canManage && (
                    <Button
                      variant="outline"
                      size="sm"
                      className="border-line-soft"
                      disabled={busy}
                      aria-busy={busy}
                      onClick={() => onRequestPublish(v)}
                    >
                      {busy ? (
                        <Loader2 className="size-3.5 animate-spin" aria-hidden />
                      ) : (
                        <Upload className="size-3.5" aria-hidden />
                      )}
                      {activeVersion && v.version < activeVersion.version
                        ? "Roll back"
                        : "Publish"}
                    </Button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}

      <VersionDiffDialog
        version={viewing}
        activeVersion={activeVersion}
        onClose={() => setViewing(null)}
      />
    </section>
  );
}

// ───────── View + diff dialog ─────────

function VersionDiffDialog({
  version,
  activeVersion,
  onClose,
}: {
  version: AgentProfileVersion | null;
  activeVersion: AgentProfileVersion | null;
  onClose: () => void;
}) {
  const open = Boolean(version);
  const isActive = version && activeVersion && version.id === activeVersion.id;

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            Version {version ? `v${version.version}` : ""}
            {isActive && (
              <Badge variant="success" className="text-[10px]">
                Active
              </Badge>
            )}
          </DialogTitle>
          <DialogDescription>
            {isActive
              ? "This is the active version — nothing to compare."
              : "Highlighted lines show how this version differs from the currently active one."}
          </DialogDescription>
        </DialogHeader>

        {version && (
          <div className="space-y-4">
            {FIELD_LABELS.map(({ key, label }) => {
              const target = String(version[key] ?? "");
              const base = String(activeVersion?.[key] ?? "");
              return (
                <FieldDiff
                  key={key}
                  label={label}
                  base={base}
                  target={target}
                  showDiff={!isActive}
                />
              );
            })}
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" className="border-line-soft" onClick={onClose}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function FieldDiff({
  label,
  base,
  target,
  showDiff,
}: {
  label: string;
  base: string;
  target: string;
  showDiff: boolean;
}) {
  const empty = target.trim().length === 0;
  const changed = hasLineChanges(base, target);
  const lines: DiffLine[] = showDiff
    ? diffLines(base, target)
    : target.split("\n").map((text) => ({ type: "context", text }));

  const glyph = (type: DiffLine["type"]) =>
    type === "add" ? "+" : type === "remove" ? "−" : " ";

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-semibold uppercase tracking-wider">{label}</p>
        {showDiff && (
          <span className="text-muted-foreground text-[10px]">
            {changed ? "changed" : "unchanged"}
          </span>
        )}
      </div>
      {empty && !changed ? (
        <p className="text-muted-foreground text-xs italic">Empty.</p>
      ) : (
        <pre className="border-line-soft overflow-x-auto rounded-md border text-[11px] leading-relaxed">
          <code className="block">
            {lines.map((line, i) => (
              <span
                key={i}
                className={cn(
                  "flex gap-2 px-2.5 py-px",
                  line.type === "add" && "bg-success/10 text-success",
                  line.type === "remove" && "bg-destructive/10 text-destructive",
                  line.type === "context" && "text-muted-foreground",
                )}
              >
                <span className="shrink-0 select-none" aria-hidden>
                  {glyph(line.type)}
                </span>
                <span className="whitespace-pre-wrap break-words">
                  {line.text || " "}
                </span>
              </span>
            ))}
          </code>
        </pre>
      )}
    </div>
  );
}
