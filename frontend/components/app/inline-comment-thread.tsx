"use client";

import * as React from "react";
import { Bot, Check, CornerDownRight } from "lucide-react";

import { cn } from "@/lib/utils";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { UserRef } from "@/lib/schemas";

export interface InlineComment {
  id: string;
  author: UserRef | "agent";
  body: string;
  createdAt: string;
  resolved?: boolean;
  severity?: "blocking" | "suggestion" | "nit";
}

export interface InlineCommentThreadProps {
  comments: readonly InlineComment[];
  currentUser: UserRef;
  onReply?: (body: string) => void | Promise<void>;
  onResolve?: (id: string) => void | Promise<void>;
  className?: string;
}

const SEVERITY_TONE: Record<NonNullable<InlineComment["severity"]>, string> = {
  blocking: "border-destructive/40 bg-destructive/5 text-destructive",
  suggestion: "border-info/30 bg-info/5 text-info",
  nit: "border-border bg-muted text-muted-foreground",
};

const SEVERITY_LABEL: Record<NonNullable<InlineComment["severity"]>, string> = {
  blocking: "Blocking",
  suggestion: "Suggestion",
  nit: "Nit",
};

export function InlineCommentThread({
  comments,
  currentUser,
  onReply,
  onResolve,
  className,
}: InlineCommentThreadProps) {
  const [draft, setDraft] = React.useState("");

  const submit = async () => {
    const body = draft.trim();
    if (!body || !onReply) return;
    await onReply(body);
    setDraft("");
  };

  return (
    <section className={cn("space-y-3", className)} aria-label="Comment thread">
      {comments.length === 0 ? (
        <p className="text-muted-foreground text-sm">No comments yet. Start the thread below.</p>
      ) : (
        <ol className="space-y-3">
          {comments.map((c) => (
            <CommentRow
              key={c.id}
              comment={c}
              onResolve={onResolve ? () => onResolve(c.id) : undefined}
            />
          ))}
        </ol>
      )}

      {onReply && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
          className="flex gap-2"
        >
          <Avatar className="size-7">
            <AvatarFallback>{currentUser.initials}</AvatarFallback>
          </Avatar>
          <div className="flex min-w-0 flex-1 flex-col gap-2">
            <Textarea
              placeholder="Reply — @mention teammates, /agent to loop in the AI."
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              rows={2}
              aria-label="Write a reply"
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                  e.preventDefault();
                  void submit();
                }
              }}
            />
            <div className="flex justify-end">
              <Button type="submit" size="sm" disabled={!draft.trim()}>
                Send reply
              </Button>
            </div>
          </div>
        </form>
      )}
    </section>
  );
}

function CommentRow({
  comment,
  onResolve,
}: {
  comment: InlineComment;
  onResolve?: () => void;
}) {
  const isAgent = comment.author === "agent";
  const initials = isAgent ? "AI" : (comment.author as UserRef).initials;
  const name = isAgent ? "Agent" : (comment.author as UserRef).name;
  return (
    <li className={cn("flex gap-2", comment.resolved && "opacity-60")}>
      <Avatar className="size-7">
        <AvatarFallback>
          {isAgent ? <Bot className="size-3.5" aria-hidden /> : initials}
        </AvatarFallback>
      </Avatar>
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex items-center gap-2 text-xs">
          <span className="font-medium">{name}</span>
          <span className="text-muted-foreground">
            {new Date(comment.createdAt).toLocaleString(undefined, {
              dateStyle: "medium",
              timeStyle: "short",
            })}
          </span>
          {comment.severity && (
            <span
              className={cn(
                "rounded-full border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider",
                SEVERITY_TONE[comment.severity],
              )}
            >
              {SEVERITY_LABEL[comment.severity]}
            </span>
          )}
          {comment.resolved && (
            <span className="text-muted-foreground inline-flex items-center gap-1 text-[10px] uppercase tracking-wider">
              <Check className="size-3" aria-hidden /> Resolved
            </span>
          )}
        </div>
        <p className="whitespace-pre-wrap text-sm">{comment.body}</p>
        {onResolve && !comment.resolved && (
          <Button
            variant="ghost"
            size="sm"
            className="text-muted-foreground h-7 px-1 text-xs"
            onClick={onResolve}
          >
            <CornerDownRight className="size-3" aria-hidden />
            Resolve
          </Button>
        )}
      </div>
    </li>
  );
}
