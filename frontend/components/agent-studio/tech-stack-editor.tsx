"use client";

import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Loader2, X } from "lucide-react";

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
import { Textarea } from "@/components/ui/textarea";
import { getLintViolations } from "@/lib/api/agent-profiles";
import { createTechStack, updateTechStack } from "@/lib/api/tech-stacks";
import { qk } from "@/lib/api/query-keys";
import type { TechStack, TechStackCatalog } from "@/lib/schemas/tech-stacks";

export type TechStackEditorMode =
  | { kind: "create"; scope: "workspace" | "project"; scopeId: string; where: string }
  | { kind: "edit"; stack: TechStack };

/** One category's items: chips, typed in (Enter or comma), with the catalogue's suggestions. */
function ChipInput({ id, label, items, suggestions, onChange, error }: {
  id: string;
  label: string;
  items: string[];
  suggestions: string[];
  onChange: (next: string[]) => void;
  error?: string;
}) {
  const [draft, setDraft] = React.useState("");
  const add = (raw: string) => {
    const value = raw.replace(/,$/, "").trim();
    setDraft("");
    if (!value || items.some((i) => i.toLowerCase() === value.toLowerCase())) return;
    onChange([...items, value]);
  };
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <div className="flex min-h-9 flex-wrap items-center gap-1.5 rounded-md border px-2 py-1.5">
        {items.map((item) => (
          <span key={item} className="bg-muted inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs">
            {item}
            <button
              type="button"
              aria-label={`Remove ${item}`}
              onClick={() => onChange(items.filter((i) => i !== item))}
              className="text-muted-foreground hover:text-foreground"
            >
              <X className="size-3" aria-hidden />
            </button>
          </span>
        ))}
        <input
          id={id}
          list={`${id}-suggestions`}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              add(draft);
            } else if (e.key === "Backspace" && !draft && items.length) {
              onChange(items.slice(0, -1));
            }
          }}
          onBlur={() => add(draft)}
          placeholder={items.length ? "" : "Type and press Enter"}
          className="min-w-[9rem] flex-1 bg-transparent py-0.5 text-sm outline-none"
        />
        <datalist id={`${id}-suggestions`}>
          {suggestions.filter((s) => !items.includes(s)).map((s) => <option key={s} value={s} />)}
        </datalist>
      </div>
      {error && <p className="text-destructive text-xs">{error}</p>}
    </div>
  );
}

/**
 * Create or edit a tech stack. The backend owns every rule (limits, unique names, who may
 * write): its `violations` come back per field and are shown on that field.
 */
export function TechStackEditor({ open, onOpenChange, mode, catalog }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mode: TechStackEditorMode;
  catalog: TechStackCatalog | undefined;
}) {
  const queryClient = useQueryClient();
  const initial = mode.kind === "edit" ? mode.stack : null;
  const [name, setName] = React.useState(initial?.name ?? "");
  const [description, setDescription] = React.useState(initial?.description ?? "");
  const [notes, setNotes] = React.useState(initial?.notes ?? "");
  const [categories, setCategories] = React.useState<Record<string, string[]>>(initial?.categories ?? {});
  const [errors, setErrors] = React.useState<Record<string, string>>({});

  const save = useMutation({
    mutationFn: () => {
      const fields = { name, description, notes, categories };
      return mode.kind === "edit"
        ? updateTechStack(mode.stack.id, fields)
        : createTechStack({ ...fields, scope: mode.scope, scope_id: mode.scopeId });
    },
    onSuccess: async (stack) => {
      toast.success(mode.kind === "edit" ? `Saved ${stack.name}` : `Created ${stack.name}`);
      await queryClient.invalidateQueries({ queryKey: qk.techStacks.all });
      onOpenChange(false);
    },
    onError: (err) => {
      const violations = getLintViolations(err);
      if (violations) {
        setErrors(Object.fromEntries(violations.map((v) => [v.field, v.message])));
        return;
      }
      toast.error(err instanceof Error ? err.message : "The tech stack could not be saved.");
    },
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{mode.kind === "edit" ? `Edit ${mode.stack.name}` : "New tech stack"}</DialogTitle>
          <DialogDescription>
            {mode.kind === "edit"
              ? "Changes apply to the next thing an agent generates."
              : `For ${mode.where}. Agents follow it on every project that uses it.`}
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            setErrors({});
            save.mutate();
          }}
        >
          <div className="space-y-1.5">
            <Label htmlFor="ts-name">Name</Label>
            <Input id="ts-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Java + Spring Boot" />
            {errors["name"] && <p className="text-destructive text-xs">{errors["name"]}</p>}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ts-description">Description</Label>
            <Input
              id="ts-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="When to use this stack"
            />
            {errors["description"] && <p className="text-destructive text-xs">{errors["description"]}</p>}
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            {(catalog?.categories ?? []).map((c) => (
              <ChipInput
                key={c.id}
                id={`ts-${c.id}`}
                label={c.label}
                items={categories[c.id] ?? []}
                suggestions={c.suggestions}
                error={errors[`categories.${c.id}`]}
                onChange={(next) => setCategories((cur) => ({ ...cur, [c.id]: next }))}
              />
            ))}
          </div>
          {errors["categories"] && <p className="text-destructive text-xs">{errors["categories"]}</p>}
          <div className="space-y-1.5">
            <Label htmlFor="ts-notes">Notes</Label>
            <Textarea
              id="ts-notes"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={3}
              placeholder="Conventions, versions, what to avoid"
            />
            {errors["notes"] && <p className="text-destructive text-xs">{errors["notes"]}</p>}
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={save.isPending}>
              {save.isPending && <Loader2 className="size-4 animate-spin" aria-hidden />}
              {mode.kind === "edit" ? "Save" : "Create"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
