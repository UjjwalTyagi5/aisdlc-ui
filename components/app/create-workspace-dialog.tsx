"use client";

import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { Loader2, ShieldCheck } from "lucide-react";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { BudgetWindowFieldsInput } from "@/components/app/budget-window-fields";
import { onboardPerson } from "@/lib/api/onboarding";
import { createWorkspace } from "@/lib/api/workspaces";
import { WorkspaceCreateInput, type Workspace } from "@/lib/schemas/workspace";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";
import { ROLE_META } from "@/lib/roles";
import { useWorkspaceStore } from "@/stores/workspace-store";

// Creation form = WorkspaceCreateInput's fields plus who to appoint as this
// business unit's admin (PRD §15.2 — "Org Admin creates business units and
// appoints their admins"). Required: a BU with no admin has no one who can
// run it (budget, members, project creation all route to that role). Reuses
// the onboarding primitive (lib/api/onboarding.ts) rather than
// addWorkspaceMember — an email always resolves via findOrCreateIdentity,
// whether the person already exists in the org or not.
//
// The monthly cap is a *string* in the form and a `number | null` on the wire:
// an empty box has to mean "no cap set" (null — the unit's own Admin sets one
// later), which a numeric field can't express without conflating it with 0.
const FormSchema = WorkspaceCreateInput.omit({
  monthlyBudgetUsd: true,
  budgetStartDate: true,
  budgetEndDate: true,
})
  .extend({
    buAdminEmail: z.string().email("Enter a valid email"),
    buAdminName: z.string().optional(),
    monthlyBudgetUsd: z
      .string()
      .refine((v) => v.trim() === "" || (Number.isFinite(Number(v)) && Number(v) >= 0), {
        message: "Enter a positive amount, or leave blank for no cap",
      }),
    budgetStartDate: z.string(),
    budgetEndDate: z.string(),
  })
  .refine((v) => !(v.budgetStartDate && v.budgetEndDate) || v.budgetEndDate >= v.budgetStartDate, {
    message: "End date must be on or after the start date",
    path: ["budgetEndDate"],
  });
type FormValues = z.infer<typeof FormSchema>;

const CLASSIFICATIONS: { value: Workspace["dataClassification"]; label: string; hint: string }[] = [
  { value: "public", label: "Public", hint: "No sensitive data" },
  { value: "internal", label: "Internal", hint: "Default — internal use" },
  { value: "confidential", label: "Confidential", hint: "Restricted access" },
  { value: "restricted", label: "Restricted", hint: "Regulated / highest controls" },
];

export interface CreateWorkspaceDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function CreateWorkspaceDialog({ open, onOpenChange }: CreateWorkspaceDialogProps) {
  const queryClient = useQueryClient();
  const setActive = useWorkspaceStore((s) => s.setActiveWorkspace);

  const form = useForm<FormValues>({
    resolver: zodResolver(FormSchema),
    defaultValues: {
      displayName: "",
      businessUnit: "",
      costCenter: "",
      dataClassification: "internal",
      monthlyBudgetUsd: "",
      budgetStartDate: "",
      budgetEndDate: "",
      isActive: true,
      buAdminEmail: "",
      buAdminName: "",
    },
  });

  React.useEffect(() => {
    if (open)
      form.reset({
        displayName: "",
        businessUnit: "",
        costCenter: "",
        dataClassification: "internal",
        monthlyBudgetUsd: "",
        isActive: true,
        buAdminEmail: "",
        buAdminName: "",
      });
  }, [open, form]);

  const mutation = useMutation({
    mutationFn: async (values: FormValues) => {
      const ws = await createWorkspace({
        displayName: values.displayName,
        businessUnit: values.businessUnit || undefined,
        costCenter: values.costCenter || undefined,
        dataClassification: values.dataClassification,
        // Blank → null, never 0: "no cap set" and "capped at zero" are
        // different states, and only the first lets the BU Admin set one.
        monthlyBudgetUsd:
          values.monthlyBudgetUsd.trim() === "" ? null : Number(values.monthlyBudgetUsd),
        budgetStartDate: values.budgetStartDate || null,
        budgetEndDate: values.budgetEndDate || null,
        isActive: values.isActive,
      });
      const admin = await onboardPerson({
        email: values.buAdminEmail,
        displayName: values.buAdminName || undefined,
        workspaceId: ws.id,
        role: "bu_admin",
      });
      return { ws, admin };
    },
    onSuccess: ({ ws, admin }) => {
      toast.success(`${BUSINESS_UNIT_LABEL} "${ws.displayName}" created`, {
        description: `${admin.displayName} appointed as its admin.`,
      });
      setActive(ws.id); // new workspace becomes the active context
      // It's now active → re-scope every surface (projects/models) to it.
      queryClient.invalidateQueries();
      onOpenChange(false);
    },
    onError: (err) =>
      toast.error(`Couldn't create ${BUSINESS_UNIT_LABEL.toLowerCase()}`, {
        description: err instanceof Error ? err.message : undefined,
      }),
  });

  const submit = form.handleSubmit((values) => mutation.mutate(values));

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="border-line-soft bg-panel-elevated max-w-lg">
        <DialogHeader>
          <div className="text-brand-bright mb-1 font-mono text-[11px] tracking-widest uppercase">
            New {BUSINESS_UNIT_LABEL.toLowerCase()}
          </div>
          <DialogTitle className="font-display text-xl font-bold tracking-tight">
            Create a {BUSINESS_UNIT_LABEL.toLowerCase()}
          </DialogTitle>
          <DialogDescription className="text-[13px]">
            A {BUSINESS_UNIT_LABEL.toLowerCase()} owns its own projects, members, connectors, budget,
            and audit slice.
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={submit} className="space-y-5">
            <FormField
              control={form.control}
              name="displayName"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="text-muted-foreground font-mono text-xs tracking-wider uppercase">
                    {BUSINESS_UNIT_LABEL} name
                  </FormLabel>
                  <FormControl>
                    <Input
                      autoFocus
                      placeholder="Payments"
                      autoComplete="off"
                      className="border-line-soft bg-surface-1 font-display focus-visible:border-primary"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <div className="grid gap-4 sm:grid-cols-2">
              <FormField
                control={form.control}
                name="businessUnit"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-muted-foreground font-mono text-xs tracking-wider uppercase">
                      Business unit{" "}
                      <span className="normal-case opacity-60">(optional)</span>
                    </FormLabel>
                    <FormControl>
                      <Input
                        placeholder="Payments"
                        autoComplete="off"
                        className="border-line-soft bg-surface-1"
                        {...field}
                      />
                    </FormControl>
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="costCenter"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-muted-foreground font-mono text-xs tracking-wider uppercase">
                      Cost center{" "}
                      <span className="normal-case opacity-60">(optional)</span>
                    </FormLabel>
                    <FormControl>
                      <Input
                        placeholder="CC-4100"
                        autoComplete="off"
                        className="border-line-soft bg-surface-1 font-mono"
                        {...field}
                      />
                    </FormControl>
                  </FormItem>
                )}
              />
            </div>

            <FormField
              control={form.control}
              name="dataClassification"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="text-muted-foreground font-mono text-xs tracking-wider uppercase">
                    Data classification
                  </FormLabel>
                  <Select value={field.value} onValueChange={field.onChange}>
                    <FormControl>
                      <SelectTrigger className="border-line-soft bg-surface-1">
                        <SelectValue />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {CLASSIFICATIONS.map((c) => (
                        <SelectItem key={c.value} value={c.value}>
                          <span className="flex items-center gap-2">
                            <span className="font-medium">{c.label}</span>
                            <span className="text-muted-foreground text-[11px]">· {c.hint}</span>
                          </span>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormDescription className="text-[11px]">
                    Drives which models and connectors the {BUSINESS_UNIT_LABEL.toLowerCase()} is
                    allowed to use.
                  </FormDescription>
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="monthlyBudgetUsd"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="text-muted-foreground font-mono text-xs tracking-wider uppercase">
                    Monthly budget <span className="normal-case opacity-60">(optional)</span>
                  </FormLabel>
                  <FormControl>
                    <div className="relative">
                      <span className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 font-mono text-sm">
                        $
                      </span>
                      <Input
                        inputMode="decimal"
                        placeholder="No cap"
                        autoComplete="off"
                        className="border-line-soft bg-surface-1 pl-7 font-mono"
                        {...field}
                      />
                    </div>
                  </FormControl>
                  <FormDescription className="text-[11px]">
                    Leave blank to set no cap — the {ROLE_META.bu_admin.label.toLowerCase()} can set
                    one later. Project budgets are allocated out of this.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            {/* Only worth asking once there's a cap to scope. */}
            {form.watch("monthlyBudgetUsd").trim() !== "" && (
              <FormField
                control={form.control}
                name="budgetEndDate"
                render={() => (
                  <FormItem>
                    <FormLabel className="text-muted-foreground font-mono text-xs tracking-wider uppercase">
                      Budget valid <span className="normal-case opacity-60">(optional)</span>
                    </FormLabel>
                    <BudgetWindowFieldsInput
                      start={form.watch("budgetStartDate")}
                      end={form.watch("budgetEndDate")}
                      onStartChange={(v) =>
                        form.setValue("budgetStartDate", v, { shouldValidate: true })
                      }
                      onEndChange={(v) =>
                        form.setValue("budgetEndDate", v, { shouldValidate: true })
                      }
                    />
                    <FormMessage />
                  </FormItem>
                )}
              />
            )}

            <FormField
              control={form.control}
              name="isActive"
              render={({ field }) => (
                <FormItem className="border-line-soft flex items-center justify-between gap-4 rounded-lg border p-3">
                  <div className="space-y-0.5">
                    <FormLabel className="text-muted-foreground font-mono text-xs tracking-wider uppercase">
                      Active
                    </FormLabel>
                    <FormDescription className="text-[11px]">
                      Only an {ROLE_META.org_admin.label.toLowerCase()} can change this. Marking a{" "}
                      {BUSINESS_UNIT_LABEL.toLowerCase()} inactive labels it for everyone; it does
                      not restrict anything on its own.
                    </FormDescription>
                  </div>
                  <FormControl>
                    <Switch
                      checked={field.value}
                      onCheckedChange={field.onChange}
                      aria-label={`${BUSINESS_UNIT_LABEL} is active`}
                    />
                  </FormControl>
                </FormItem>
              )}
            />

            <div className="border-line-soft space-y-4 rounded-lg border border-dashed p-4">
              <p className="text-muted-foreground flex items-center gap-1.5 font-mono text-xs tracking-wider uppercase">
                <ShieldCheck className="text-brand-bright size-3.5" aria-hidden />
                {ROLE_META.bu_admin.label}
              </p>
              <FormField
                control={form.control}
                name="buAdminEmail"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-muted-foreground font-mono text-xs tracking-wider uppercase">
                      Email
                    </FormLabel>
                    <FormControl>
                      <Input
                        type="email"
                        placeholder="name@company.com"
                        autoComplete="off"
                        className="border-line-soft bg-surface-1"
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="buAdminName"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-muted-foreground font-mono text-xs tracking-wider uppercase">
                      Name <span className="normal-case opacity-60">(optional)</span>
                    </FormLabel>
                    <FormControl>
                      <Input
                        placeholder="Jane Doe"
                        autoComplete="off"
                        className="border-line-soft bg-surface-1"
                        {...field}
                      />
                    </FormControl>
                  </FormItem>
                )}
              />
              <p className="text-muted-foreground text-[11px]">
                Runs this {BUSINESS_UNIT_LABEL.toLowerCase()}: its budget, connections, members, and
                project creation. A new email is onboarded automatically — no separate invite step.
                Change who holds this role anytime from the {BUSINESS_UNIT_LABEL.toLowerCase()}&apos;s
                Members list.
              </p>
            </div>

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={mutation.isPending}
                className="border-line-soft"
              >
                Cancel
              </Button>
              <Button
                type="submit"
                disabled={mutation.isPending}
                aria-busy={mutation.isPending}
                className="from-brand-gradient-from to-brand-gradient-to bg-gradient-to-br font-semibold text-white"
              >
                {mutation.isPending && <Loader2 className="size-4 animate-spin" aria-hidden />}
                Create {BUSINESS_UNIT_LABEL.toLowerCase()}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
