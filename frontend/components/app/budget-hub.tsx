"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Check, ChevronRight, Loader2, Pencil, X } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { LoadingState } from "@/components/ui/loading-state";
import { useSession } from "@/hooks/use-session";
import { hasPermission } from "@/lib/auth/permissions";
import { getBudgets, setBudget, type BudgetRow as Row } from "@/lib/api/cost";
import { qk } from "@/lib/api/query-keys";
import { BUSINESS_UNIT_LABEL, BUSINESS_UNIT_LABEL_PLURAL } from "@/lib/scope";

function fmtUsd(n: number, max = 2): string {
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: max });
}

export function BudgetHub() {
  const session = useSession();
  const canManage = hasPermission(session, "workspace:manage");
  const qc = useQueryClient();

  const q = useQuery({ queryKey: qk.cost.budgets(), queryFn: getBudgets });
  const m = useMutation({
    mutationFn: setBudget,
    onSuccess: () => {
      toast.success("Budget updated");
      qc.invalidateQueries({ queryKey: qk.cost.budgets() });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Couldn't update budget"),
  });

  const [orgOpen, setOrgOpen] = React.useState(true);
  const [openWs, setOpenWs] = React.useState<Set<string>>(new Set());
  const toggleWs = (id: string) =>
    setOpenWs((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  if (q.isLoading) return <LoadingState variant="list" rows={4} />;
  if (!q.data) return null;
  // Projects take no default budget: creating one requires an explicit budget, and
  // a unit that sets none simply has no cap. The API used to return an unused
  // `defaultProjectBudgetUsd`; it was removed along with the backend constant.
  const { org, workspaces, projects } = q.data;

  const projByWs = new Map<string, Row[]>();
  for (const p of projects) {
    const key = p.parentId ?? "";
    const arr = projByWs.get(key) ?? [];
    arr.push(p);
    projByWs.set(key, arr);
  }

  const save = (row: Row, value: number | null) =>
    m.mutate({ scope: row.scope, id: row.id, monthlyBudgetUsd: value });

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-display text-[20px] font-bold tracking-[-0.02em]">Budgets</h2>
        <p className="text-muted-foreground font-mono text-[11px]">
          Total caps · org ⊇ {BUSINESS_UNIT_LABEL.toLowerCase()} ⊇ project · every project
          sets its own; a {BUSINESS_UNIT_LABEL.toLowerCase()} with no cap bounds nothing
          {canManage ? "" : " · view only"}
        </p>
      </div>

      <section className="border-line-soft bg-panel-elevated overflow-hidden rounded-xl border">
        <ul className="divide-line-soft divide-y">
          <BudgetNode
            row={org}
            level={0}
            expandable={workspaces.length > 0}
            open={orgOpen}
            onToggle={() => setOrgOpen((v) => !v)}
            childLabel={BUSINESS_UNIT_LABEL_PLURAL.toLowerCase()}
            canManage={canManage}
            saving={m.isPending}
            onSave={save}
          />
          {orgOpen &&
            workspaces.map((ws) => {
              const kids = projByWs.get(ws.id) ?? [];
              const isOpen = openWs.has(ws.id);
              return (
                <React.Fragment key={ws.id}>
                  <BudgetNode
                    row={ws}
                    level={1}
                    expandable={kids.length > 0}
                    open={isOpen}
                    onToggle={() => toggleWs(ws.id)}
                    childLabel="projects"
                    canManage={canManage}
                    saving={m.isPending}
                    onSave={save}
                  />
                  {isOpen &&
                    kids.map((p) => (
                      <BudgetNode
                        key={p.id}
                        row={p}
                        level={2}
                        canManage={canManage}
                        saving={m.isPending}
                        onSave={save}
                      />
                    ))}
                </React.Fragment>
              );
            })}
        </ul>
      </section>
    </div>
  );
}

function BudgetNode({
  row,
  level,
  expandable = false,
  open = false,
  onToggle,
  childLabel,
  canManage,
  saving,
  onSave,
}: {
  row: Row;
  level: 0 | 1 | 2;
  expandable?: boolean;
  open?: boolean;
  onToggle?: () => void;
  childLabel?: string;
  canManage: boolean;
  saving: boolean;
  onSave: (row: Row, value: number | null) => void;
}) {
  const [editing, setEditing] = React.useState(false);
  const [value, setValue] = React.useState(
    typeof row.monthlyBudgetUsd === "number" ? String(row.monthlyBudgetUsd) : "",
  );

  React.useEffect(() => {
    setValue(typeof row.monthlyBudgetUsd === "number" ? String(row.monthlyBudgetUsd) : "");
  }, [row.monthlyBudgetUsd]);

  const budget = row.monthlyBudgetUsd ?? null;
  const pct = budget && budget > 0 ? Math.min(100, (row.monthlySpendUsd / budget) * 100) : 0;
  const over = budget !== null && budget > 0 && row.monthlySpendUsd >= budget;
  const warn = budget !== null && budget > 0 && !over && row.monthlySpendUsd >= 0.8 * budget;
  const overAllocated =
    budget !== null && budget > 0 && typeof row.allocatedUsd === "number" && row.allocatedUsd > budget;
  const barTone = over ? "bg-destructive" : warn ? "bg-warning" : "bg-brand-bright";
  const nameSize = level === 0 ? "text-[14px]" : level === 1 ? "text-[13px]" : "text-[12.5px]";

  const commit = () => {
    const trimmed = value.trim();
    const parsed = trimmed === "" ? 0 : Number(trimmed);
    if (Number.isNaN(parsed) || parsed < 0) {
      toast.error("Enter a valid non-negative amount");
      return;
    }
    onSave(row, parsed === 0 ? null : parsed);
    setEditing(false);
  };

  return (
    <li
      className={cn(
        "grid grid-cols-[1fr_auto] items-center gap-4 py-3 pr-5",
        level === 0 && "bg-surface-1/40",
      )}
      style={{ paddingLeft: 20 + level * 22 }}
    >
      <div className="flex min-w-0 items-start gap-2">
        {expandable ? (
          <button
            type="button"
            onClick={onToggle}
            aria-label={open ? "Collapse" : "Expand"}
            className="text-muted-foreground hover:text-foreground mt-0.5 shrink-0"
          >
            <ChevronRight className={cn("size-3.5 transition-transform", open && "rotate-90")} />
          </button>
        ) : (
          <span className="w-3.5 shrink-0" aria-hidden />
        )}
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className={cn("truncate font-semibold", nameSize)}>{row.name}</span>
            {over && <span className="text-destructive font-mono text-[10px] font-semibold">OVER</span>}
            {warn && <span className="text-warning font-mono text-[10px] font-semibold">80%</span>}
            {expandable && childLabel && (
              <span className="text-muted-foreground font-mono text-[10px]">· {childLabel}</span>
            )}
          </div>
          <div className="text-muted-foreground mt-1 flex items-center gap-2 font-mono text-[11px]">
            <span>{fmtUsd(row.monthlySpendUsd)} used</span>
            {budget !== null && budget > 0 && (
              <>
                <span>/ {fmtUsd(budget)}</span>
                <span className="bg-surface-1 border-line-soft inline-block h-1.5 w-24 overflow-hidden rounded-full border align-middle">
                  <span className={cn("block h-full rounded-full", barTone)} style={{ width: `${pct}%` }} />
                </span>
              </>
            )}
          </div>
          {typeof row.allocatedUsd === "number" && row.allocatedUsd > 0 && (
            <div className="text-muted-foreground mt-0.5 font-mono text-[10.5px]">
              {fmtUsd(row.allocatedUsd)} allocated to {childLabel ?? "children"}
              {budget !== null && budget > 0 && (
                <>
                  {" · "}
                  <span className={overAllocated ? "text-destructive" : ""}>
                    {fmtUsd(Math.max(0, budget - row.allocatedUsd))} free
                  </span>
                </>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="flex items-center justify-end gap-2">
        {editing ? (
          <>
            <span className="text-muted-foreground font-mono text-[12px]">$</span>
            <Input
              type="number"
              min={0}
              step={1}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="0 = default"
              className="border-line-soft h-8 w-28 font-mono text-[12px]"
              autoFocus
            />
            <Button
              size="icon"
              className="from-brand-gradient-from to-brand-gradient-to size-8 bg-gradient-to-br text-white"
              disabled={saving}
              onClick={commit}
              aria-label="Save budget"
            >
              {saving ? <Loader2 className="size-3.5 animate-spin" /> : <Check className="size-3.5" />}
            </Button>
            <Button size="icon" variant="ghost" className="size-8" onClick={() => setEditing(false)} aria-label="Cancel">
              <X className="size-3.5" />
            </Button>
          </>
        ) : (
          <>
            <span className="font-mono text-[12.5px] font-semibold tabular-nums">
              {budget !== null && budget > 0 ? fmtUsd(budget) : "—"}
            </span>
            {canManage && (
              <Button
                size="icon"
                variant="ghost"
                className="text-muted-foreground hover:text-foreground size-8"
                onClick={() => setEditing(true)}
                aria-label={`Edit ${row.name} budget`}
              >
                <Pencil className="size-3.5" />
              </Button>
            )}
          </>
        )}
      </div>
    </li>
  );
}
