"use client";

import * as React from "react";
import { ArrowDown, ArrowUp, GripVertical, Plus, Trash2 } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

export interface AcRow {
  given: string;
  when: string;
  then: string;
}

export interface AcEditorProps {
  value: readonly AcRow[];
  onChange: (next: AcRow[]) => void;
  readOnly?: boolean;
  className?: string;
}

const EMPTY_ROW: AcRow = { given: "", when: "", then: "" };

export function AcEditor({ value, onChange, readOnly, className }: AcEditorProps) {
  const [focused, setFocused] = React.useState<number | null>(null);

  const update = (idx: number, patch: Partial<AcRow>) => {
    const next = value.map((r, i) => (i === idx ? { ...r, ...patch } : r));
    onChange(next);
  };

  const addRow = () => onChange([...value, { ...EMPTY_ROW }]);

  const removeRow = (idx: number) => {
    onChange(value.filter((_, i) => i !== idx));
  };

  const move = (idx: number, delta: -1 | 1) => {
    const next = [...value];
    const target = idx + delta;
    if (target < 0 || target >= next.length) return;
    [next[idx], next[target]] = [next[target]!, next[idx]!];
    onChange(next);
  };

  return (
    <div className={cn("space-y-3", className)} aria-label="Acceptance criteria editor">
      {value.length === 0 ? (
        <p className="text-muted-foreground rounded-md border border-dashed p-4 text-center text-sm">
          No acceptance criteria yet.
        </p>
      ) : (
        <ol className="space-y-3">
          {value.map((row, i) => (
            <li
              key={i}
              className={cn(
                "group bg-card relative rounded-md border p-3",
                focused === i && "ring-ring ring-2",
              )}
              onFocus={() => setFocused(i)}
              onBlur={(e) => {
                if (!e.currentTarget.contains(e.relatedTarget)) setFocused(null);
              }}
            >
              <div className="text-muted-foreground absolute left-2 top-2 flex items-center gap-1">
                <GripVertical className="size-3.5" aria-hidden />
                <span className="font-mono text-[10px]">#{i + 1}</span>
              </div>
              <div className="mb-2 flex items-center justify-end gap-1">
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-6"
                  onClick={() => move(i, -1)}
                  disabled={readOnly || i === 0}
                  aria-label="Move up"
                >
                  <ArrowUp className="size-3.5" aria-hidden />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-6"
                  onClick={() => move(i, 1)}
                  disabled={readOnly || i === value.length - 1}
                  aria-label="Move down"
                >
                  <ArrowDown className="size-3.5" aria-hidden />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  className="text-destructive hover:text-destructive size-6"
                  onClick={() => removeRow(i)}
                  disabled={readOnly}
                  aria-label="Remove criterion"
                >
                  <Trash2 className="size-3.5" aria-hidden />
                </Button>
              </div>

              <div className="grid gap-3">
                <AcField
                  idx={i}
                  label="Given"
                  value={row.given}
                  readOnly={readOnly}
                  onChange={(v) => update(i, { given: v })}
                />
                <AcField
                  idx={i}
                  label="When"
                  value={row.when}
                  readOnly={readOnly}
                  onChange={(v) => update(i, { when: v })}
                />
                <AcField
                  idx={i}
                  label="Then"
                  value={row.then}
                  readOnly={readOnly}
                  onChange={(v) => update(i, { then: v })}
                />
              </div>
            </li>
          ))}
        </ol>
      )}

      {!readOnly && (
        <Button variant="outline" size="sm" onClick={addRow}>
          <Plus className="size-3.5" aria-hidden />
          Add criterion
        </Button>
      )}
    </div>
  );
}

function AcField({
  idx,
  label,
  value,
  onChange,
  readOnly,
}: {
  idx: number;
  label: string;
  value: string;
  onChange: (v: string) => void;
  readOnly?: boolean;
}) {
  const id = `ac-${idx}-${label.toLowerCase()}`;
  return (
    <div className="grid grid-cols-[60px_1fr] items-start gap-2">
      <Label
        htmlFor={id}
        className="text-muted-foreground pt-1.5 font-mono text-xs uppercase tracking-wider"
      >
        {label}
      </Label>
      <Textarea
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        readOnly={readOnly}
        rows={1}
        className="min-h-8 resize-none"
      />
    </div>
  );
}
