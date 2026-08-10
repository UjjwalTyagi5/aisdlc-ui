"use client";

import * as React from "react";
import { Check, MessageSquare, Pencil, Plus, Trash2, X } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { OrchestratorSession, SessionStatus } from "@/lib/orchestrator/types";

/** A quiet dot per run state — the rail is a list, not a dashboard. */
const STATUS_DOT: Record<SessionStatus, string> = {
  idle: "bg-muted-foreground/40",
  running: "bg-info animate-pulse",
  paused: "bg-warning",
  complete: "bg-success",
  failed: "bg-destructive",
};

const STATUS_TITLE: Record<SessionStatus, string> = {
  idle: "Not started",
  running: "Running",
  paused: "Paused at a gate",
  complete: "Complete",
  failed: "Failed",
};

export interface SessionRailProps {
  sessions: OrchestratorSession[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  /** Resolve a project id to its display name for the sub-label. */
  projectName: (projectId: string) => string;
}

export function SessionRail({
  sessions,
  activeId,
  onSelect,
  onCreate,
  onRename,
  onDelete,
  projectName,
}: SessionRailProps) {
  const [editingId, setEditingId] = React.useState<string | null>(null);
  const [draft, setDraft] = React.useState("");

  const beginEdit = (s: OrchestratorSession) => {
    setEditingId(s.id);
    setDraft(s.title);
  };

  const commit = () => {
    if (editingId) onRename(editingId, draft);
    setEditingId(null);
  };

  return (
    <aside className="border-line-soft bg-surface-1 flex h-full w-[248px] shrink-0 flex-col border-r">
      <div className="border-line-soft flex items-center justify-between gap-2 border-b px-3 py-2.5">
        <span className="text-muted-foreground font-mono text-[10px] font-semibold tracking-[0.14em] uppercase">
          Sessions
        </span>
        <Button size="sm" variant="ghost" className="h-7 gap-1.5 px-2 text-[12px]" onClick={onCreate}>
          <Plus className="size-3.5" aria-hidden />
          New
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {sessions.length === 0 ? (
          <p className="text-muted-foreground px-2 py-6 text-center text-[12px] leading-relaxed">
            No sessions yet. Start one to run a project&apos;s agent roster.
          </p>
        ) : (
          <ul className="space-y-1">
            {sessions.map((s) => {
              const active = s.id === activeId;
              const editing = s.id === editingId;

              return (
                <li key={s.id}>
                  {editing ? (
                    <div className="flex items-center gap-1 px-1">
                      <Input
                        value={draft}
                        onChange={(e) => setDraft(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") commit();
                          if (e.key === "Escape") setEditingId(null);
                        }}
                        aria-label="Session name"
                        className="h-7 text-[12.5px]"
                        autoFocus
                      />
                      <Button size="icon" variant="ghost" className="size-7" onClick={commit} aria-label="Save name">
                        <Check className="size-3.5" aria-hidden />
                      </Button>
                      <Button
                        size="icon"
                        variant="ghost"
                        className="size-7"
                        onClick={() => setEditingId(null)}
                        aria-label="Cancel rename"
                      >
                        <X className="size-3.5" aria-hidden />
                      </Button>
                    </div>
                  ) : (
                    <div
                      className={cn(
                        "group flex items-center gap-2 rounded-lg px-2 py-1.5 transition-colors",
                        active ? "bg-accent" : "hover:bg-accent/60",
                      )}
                    >
                      <button
                        type="button"
                        onClick={() => onSelect(s.id)}
                        aria-current={active ? "true" : undefined}
                        className="flex min-w-0 flex-1 items-center gap-2 text-left"
                      >
                        <span
                          className={cn("size-1.5 shrink-0 rounded-full", STATUS_DOT[s.status])}
                          title={STATUS_TITLE[s.status]}
                          aria-hidden
                        />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-[12.5px] font-medium">{s.title}</span>
                          <span className="text-muted-foreground block truncate text-[11px]">
                            {projectName(s.projectId)}
                          </span>
                        </span>
                      </button>

                      <span className="flex shrink-0 items-center opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                        <Button
                          size="icon"
                          variant="ghost"
                          className="size-6"
                          onClick={() => beginEdit(s)}
                          aria-label={`Rename ${s.title}`}
                        >
                          <Pencil className="size-3" aria-hidden />
                        </Button>
                        <Button
                          size="icon"
                          variant="ghost"
                          className="hover:text-destructive size-6"
                          onClick={() => onDelete(s.id)}
                          aria-label={`Delete ${s.title}`}
                        >
                          <Trash2 className="size-3" aria-hidden />
                        </Button>
                      </span>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <div className="border-line-soft text-muted-foreground flex items-start gap-2 border-t px-3 py-2.5 text-[11px] leading-relaxed">
        <MessageSquare className="mt-px size-3.5 shrink-0" aria-hidden />
        <span>Sessions are stored in this browser only.</span>
      </div>
    </aside>
  );
}
