"use client";

import * as React from "react";
import { CheckCircle2, Clock, Inbox, UserRound, XCircle } from "lucide-react";

import { cn } from "@/lib/utils";
import { OPEN_REQUEST_STATUSES } from "@/lib/schemas/governance-approval";
import type { ApprovalGate, GovernanceApproval } from "@/lib/schemas";

export interface RequestCounts {
  total: number;
  pending: number;
  approved: number;
  rejected: number;
  mine: number;
}

/**
 * The five counts, from one pass over each list.
 *
 * `pending` counts every OPEN status — submitted, pending review and escalated
 * — not just `pending_review`. A summary that excluded escalated requests would
 * report the queue as shorter than it is, and escalated is precisely the state
 * someone needs to see in a total.
 *
 * GATES AND DOCUMENTS COUNT TOO, and leaving them out is what made these tiles read
 * "Total requests 0 · Pending 0" directly above an Inbox listing a document waiting
 * for a decision. The tiles summarise the page, and the page shows both lanes:
 * governance requests raised by a person, and approvals derived from run and artifact
 * state. A summary that contradicts the list under it teaches people to ignore both.
 *
 * THEY CANNOT REACH `approved` OR `rejected`, and that is a property of the source
 * rather than an omission. `GET /approvals` returns only what is still waiting — a
 * decided gate stops being derived at all, which is exactly what makes the
 * first-approver rule work. So a decided document leaves `total` as well as
 * `pending`; there is no history lane for it to move into. The alternative, keeping a
 * decided copy around to count, is the stored queue this deliberately is not.
 */
export function countRequests(
  requests: GovernanceApproval[],
  viewerIdentityId: string | null,
  awaiting: ApprovalGate[] = [],
): RequestCounts {
  let pending = 0;
  let approved = 0;
  let rejected = 0;
  let mine = 0;
  for (const r of requests) {
    if (OPEN_REQUEST_STATUSES.includes(r.status)) pending++;
    else if (r.status === "approved") approved++;
    else if (r.status === "rejected") rejected++;
    if (viewerIdentityId && r.requestedById === viewerIdentityId) mine++;
  }
  for (const g of awaiting) {
    pending++;
    // MATCHED ON THE ID, NOT THE LABEL. `requestedBy` is a rendered email; comparing
    // it to an identity id would never match and "Raised by me" would silently stay 0
    // for the person who uploaded the document.
    if (viewerIdentityId && g.requestedById === viewerIdentityId) mine++;
  }
  return { total: requests.length + awaiting.length, pending, approved, rejected, mine };
}

interface CardSpec {
  key: keyof RequestCounts;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  /** Tint applied to the figure only — the card itself stays neutral. */
  tone?: string;
}

const CARDS: CardSpec[] = [
  { key: "total", label: "Total requests", icon: Inbox },
  { key: "pending", label: "Pending", icon: Clock, tone: "text-warning" },
  { key: "approved", label: "Approved", icon: CheckCircle2, tone: "text-success" },
  { key: "rejected", label: "Rejected", icon: XCircle, tone: "text-destructive" },
  { key: "mine", label: "Raised by me", icon: UserRound },
];

/**
 * Five metrics across the top of Requests & Approvals.
 *
 * Colour lands on the NUMBER, not the card. Five tinted panels in a row would
 * read as five alerts; the figure is the thing that changes, so it is the thing
 * that carries the state. Zero stays muted in every card — a red 0 rejected is
 * an alarm about nothing.
 */
export function RequestSummaryCards({
  counts,
  className,
}: {
  counts: RequestCounts;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5",
        className,
      )}
    >
      {CARDS.map(({ key, label, icon: Icon, tone }) => {
        const value = counts[key];
        return (
          <div
            key={key}
            className="border-line-soft bg-panel-elevated rounded-xl border px-4 py-3"
          >
            <div className="text-muted-foreground flex items-center justify-between gap-2">
              <span className="font-mono text-[10px] tracking-[0.12em] uppercase">{label}</span>
              <Icon className="size-3.5 shrink-0" aria-hidden />
            </div>
            <p
              className={cn(
                "font-display mt-1 text-[26px] leading-none font-bold tabular-nums",
                value > 0 && tone,
              )}
            >
              {value}
            </p>
          </div>
        );
      })}
    </div>
  );
}
