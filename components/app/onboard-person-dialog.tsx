"use client";

import * as React from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { toast } from "sonner";
import { Loader2, UserPlus } from "lucide-react";
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
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { AssignableRole } from "@/hooks/use-assignable-roles";

const formSchema = z.object({
  email: z.string().email("Enter a valid email"),
  displayName: z.string().optional(),
  roleName: z.string().min(1, "Choose a role"),
  workspaceId: z.string().optional(),
});
type FormValues = z.infer<typeof formSchema>;

export interface OnboardPersonDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Which roles this onboarding surface may grant — governs the picker.
   *  Built-in roles and custom roles alike (see hooks/use-assignable-roles.ts). */
  roleOptions: readonly AssignableRole[];
  title: string;
  description: string;
  /**
   * When provided, a required Business Unit picker is shown and its value is
   * included in `onSubmit`'s payload — org/BU-scope onboarding needs to know
   * which BU to bind the membership to; project scope (no `workspaceOptions`)
   * doesn't, since the project itself is the scope.
   */
  workspaceOptions?: readonly { id: string; displayName: string }[];
  onSubmit: (input: {
    email: string;
    displayName?: string;
    roleName: string;
    workspaceId?: string;
  }) => Promise<unknown>;
}

/**
 * Scope-agnostic "onboard a new person" dialog — org, Business Unit, and
 * project scopes all reuse this, differing only in which roles are offered
 * and what `onSubmit` does with the result. Every scope funnels through
 * `findOrCreateIdentity` server-side, so an email that doesn't exist yet is
 * onboarded ("invited"), never rejected.
 */
export function OnboardPersonDialog({
  open,
  onOpenChange,
  roleOptions,
  title,
  description,
  workspaceOptions,
  onSubmit,
}: OnboardPersonDialogProps) {
  const [submitting, setSubmitting] = React.useState(false);
  const form = useForm<FormValues>({
    resolver: zodResolver(
      workspaceOptions
        ? formSchema.extend({ workspaceId: z.string().min(1, "Choose a Business Unit") })
        : formSchema,
    ),
    defaultValues: {
      email: "",
      displayName: "",
      roleName: roleOptions[0]?.value ?? "",
      workspaceId: workspaceOptions?.[0]?.id ?? "",
    },
  });

  React.useEffect(() => {
    if (open) {
      form.reset({
        email: "",
        displayName: "",
        roleName: roleOptions[0]?.value ?? "",
        workspaceId: workspaceOptions?.[0]?.id ?? "",
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, form, roleOptions]);

  const submit = form.handleSubmit(async (values) => {
    setSubmitting(true);
    try {
      await onSubmit({
        email: values.email,
        displayName: values.displayName || undefined,
        roleName: values.roleName,
        workspaceId: values.workspaceId || undefined,
      });
      onOpenChange(false);
    } catch (err) {
      toast.error("Couldn't onboard person", {
        description: err instanceof Error ? err.message : undefined,
      });
    } finally {
      setSubmitting(false);
    }
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="border-line-soft bg-panel-elevated max-w-md">
        <DialogHeader>
          <DialogTitle className="font-display text-lg font-bold tracking-tight">
            {title}
          </DialogTitle>
          <DialogDescription className="text-[13px]">{description}</DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={submit} className="space-y-4">
            <FormField
              control={form.control}
              name="email"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
                    Email
                  </FormLabel>
                  <FormControl>
                    <Input
                      autoFocus
                      type="email"
                      placeholder="name@company.com"
                      autoComplete="off"
                      {...field}
                    />
                  </FormControl>
                  <p className="text-muted-foreground text-[11px]">
                    A new email is onboarded automatically — no separate invite step.
                  </p>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="displayName"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
                    Name <span className="normal-case text-muted-foreground/60">(optional)</span>
                  </FormLabel>
                  <FormControl>
                    <Input placeholder="Jane Doe" autoComplete="off" {...field} />
                  </FormControl>
                </FormItem>
              )}
            />

            {workspaceOptions && (
              <FormField
                control={form.control}
                name="workspaceId"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
                      Business Unit
                    </FormLabel>
                    <Select value={field.value} onValueChange={field.onChange}>
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue placeholder="Select a Business Unit…" />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {workspaceOptions.map((w) => (
                          <SelectItem key={w.id} value={w.id}>
                            {w.displayName}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <FormMessage />
                  </FormItem>
                )}
              />
            )}

            <FormField
              control={form.control}
              name="roleName"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
                    Role
                  </FormLabel>
                  <Select value={field.value} onValueChange={field.onChange}>
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue placeholder="Select a role…" />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {roleOptions.map((r) => (
                        <SelectItem key={r.value} value={r.value}>
                          {r.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={submitting}
                className="border-line-soft"
              >
                Cancel
              </Button>
              <Button type="submit" disabled={submitting} aria-busy={submitting}>
                {submitting && <Loader2 className="size-4 animate-spin" aria-hidden />}
                <UserPlus className="size-4" aria-hidden />
                Onboard
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
