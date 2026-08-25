"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { Boxes, Globe, Loader2, Plug, ShieldCheck, Terminal } from "lucide-react";
import { z } from "zod";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
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
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { BudgetWindowFieldsInput } from "@/components/app/budget-window-fields";
import { listConnectorGrants, setBuConnectorGrants } from "@/lib/api/connectors";
import { listOrgMembers } from "@/lib/api/access";
import { grantIntegrationAccess } from "@/lib/api/integration-access";
import { listMcpServers } from "@/lib/api/mcp";
import { getOrgModelGrants, setBuModelGrants } from "@/lib/api/models";
import { onboardPerson } from "@/lib/api/onboarding";
import { qk } from "@/lib/api/query-keys";
import { createWorkspace } from "@/lib/api/workspaces";
import { CONNECTOR_CATALOG_KINDS, CONNECTOR_KIND_LABEL } from "@/lib/connectors";
import { WorkspaceCreateInput } from "@/lib/schemas/workspace";
import type { ConnectorKind } from "@/lib/schemas/enums";
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
// The total cap is a *string* in the form and a `number | null` on the wire:
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

export interface CreateWorkspaceDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * One "what does this unit get" block: the things it gets anyway, then the
 * restricted ones to tick.
 *
 * Global grants are listed but not selectable. Rendering them as checked-and
 * -disabled boxes would read as "you could untick this", which is exactly the
 * thing that isn't true — a global grant reaches every unit and cannot be
 * withdrawn from one.
 */
function GrantPicker({
  icon: Icon,
  title,
  globals,
  options,
  selected,
  onToggle,
  idPrefix,
  emptyHint,
  loading,
}: {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  globals: string[];
  options: { value: string; label: string; hint?: string }[];
  selected: string[];
  onToggle: (next: string[]) => void;
  idPrefix: string;
  emptyHint: string;
  loading?: boolean;
}) {
  return (
    <div className="space-y-2">
      <p className="flex items-center gap-1.5 text-[12.5px] font-medium">
        <Icon className="text-muted-foreground size-3.5" aria-hidden />
        {title}
      </p>

      {globals.length > 0 && (
        <ul className="flex flex-wrap gap-1.5">
          {globals.map((g) => (
            <li
              key={g}
              className="border-line-soft bg-surface-1 text-muted-foreground inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10px]"
            >
              <Globe className="size-2.5" aria-hidden />
              {g}
            </li>
          ))}
        </ul>
      )}

      {loading ? (
        <p className="text-muted-foreground text-[11.5px]">Loading…</p>
      ) : options.length === 0 ? (
        <p className="text-muted-foreground text-[11.5px]">{emptyHint}</p>
      ) : (
        <ul className="divide-line-soft border-line-soft max-h-40 divide-y overflow-y-auto rounded-lg border">
          {options.map((o) => {
            const id = `${idPrefix}-${o.value}`;
            const on = selected.includes(o.value);
            return (
              <li key={o.value} className="flex items-center gap-2.5 px-2.5 py-2">
                <Checkbox
                  id={id}
                  checked={on}
                  onCheckedChange={() =>
                    onToggle(on ? selected.filter((v) => v !== o.value) : [...selected, o.value])
                  }
                />
                <Label
                  htmlFor={id}
                  className={cn(
                    "min-w-0 flex-1 cursor-pointer truncate font-mono text-[11.5px] font-normal",
                    !on && "text-muted-foreground",
                  )}
                >
                  {o.label}
                </Label>
                {o.hint && (
                  <span className="text-muted-foreground shrink-0 font-mono text-[10px]">
                    {o.hint}
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

export function CreateWorkspaceDialog({ open, onOpenChange }: CreateWorkspaceDialogProps) {
  const queryClient = useQueryClient();
  const setActive = useWorkspaceStore((s) => s.setActiveWorkspace);

  // What this unit gets on day one. Only restricted grants are choices —
  // global ones arrive automatically, and are shown so the answer to "what
  // will they be able to use?" is complete rather than just the part that
  // needed a decision.
  const modelGrantsQ = useQuery({
    queryKey: qk.model.orgGrants(),
    queryFn: getOrgModelGrants,
    enabled: open,
  });
  const connectorGrantsQ = useQuery({
    queryKey: qk.connectors.grants(null),
    queryFn: () => listConnectorGrants(),
    enabled: open,
  });
  // MCP servers were absent from this dialog entirely: a unit could be given
  // models and connectors at creation but never a tool server, so its MCP access
  // had to be set afterwards from somewhere else — easy to forget, and invisible
  // here in a section titled "Access".
  //
  // Unlike connectors, the list is genuinely dynamic and has to be: a connector
  // kind is a fixed catalogue this platform ships, while an MCP server exists only
  // because somebody registered one. There is nothing to offer but what is there.
  // The org's existing people, so the admin's email can be picked rather than
  // recalled and retyped. Typing an address that belongs to nobody is still
  // allowed — `onboardPerson` creates the account — but getting an existing
  // colleague's address subtly wrong creates a SECOND account for the same
  // person, which is the failure a plain text box invites and a suggestion list
  // removes.
  const orgMembersQ = useQuery({
    queryKey: qk.access.orgMembers(),
    queryFn: listOrgMembers,
    enabled: open,
  });

  const mcpServersQ = useQuery({
    queryKey: qk.mcp.list(),
    queryFn: () => listMcpServers(true),
    enabled: open,
  });

  const restrictedModels = (modelGrantsQ.data ?? []).filter((g) => g.visibility === "specific");
  const globalModels = (modelGrantsQ.data ?? []).filter((g) => g.visibility === "global");
  /**
   * Every GRANTABLE connector kind, annotated with who already holds it.
   *
   * Offering all of them is deliberate: this dialog is where a unit's connector
   * access is decided, and with global/specific gone there is no set a new unit
   * gets automatically — listing only kinds some other unit already holds would
   * leave the first unit to want something unable to ask for it here.
   *
   * CONNECTOR_CATALOG_KINDS, not Object.keys(CONNECTOR_KIND_LABEL). The label map
   * covers every ConnectorKind including `azure_repos` and the two SSO kinds,
   * which `_CATALOG_KINDS` on the backend does not accept as grants — so three of
   * the thirteen options offered here were ones the server would refuse. A picker
   * must not offer what create rejects.
   *
   * The grants query is what makes the list say something real rather than being
   * a static catalogue: each kind carries the number of units already holding it,
   * which is the context somebody deciding a new unit's access actually wants.
   * Its result used to be fetched and thrown away — only `isLoading` was read.
   */
  const grantsByKind = new Map(
    (connectorGrantsQ.data ?? []).map((g) => [g.kind, g.businessUnitIds.length]),
  );
  const restrictedConnectors = CONNECTOR_CATALOG_KINDS.map((kind) => ({
    kind,
    heldBy: grantsByKind.get(kind) ?? 0,
  }));
  const globalConnectors: { kind: ConnectorKind }[] = [];

  const [grantedModels, setGrantedModels] = React.useState<string[]>([]);
  const [grantedConnectors, setGrantedConnectors] = React.useState<string[]>([]);
  const [grantedMcp, setGrantedMcp] = React.useState<string[]>([]);

  const form = useForm<FormValues>({
    resolver: zodResolver(FormSchema),
    defaultValues: {
      displayName: "",
      costCenter: "",
      monthlyBudgetUsd: "",
      budgetStartDate: "",
      budgetEndDate: "",
      isActive: true,
      buAdminEmail: "",
      buAdminName: "",
    },
  });

  React.useEffect(() => {
    if (open) {
      form.reset({
        displayName: "",
        costCenter: "",
        monthlyBudgetUsd: "",
        budgetStartDate: "",
        budgetEndDate: "",
        isActive: true,
        buAdminEmail: "",
        buAdminName: "",
      });
      setGrantedModels([]);
      setGrantedConnectors([]);
      setGrantedMcp([]);
    }
  }, [open, form]);

  const mutation = useMutation({
    mutationFn: async (values: FormValues) => {
      const ws = await createWorkspace({
        displayName: values.displayName,
        costCenter: values.costCenter || undefined,
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
      // Grants come after the unit exists, since they're keyed by its id. A
      // failure here would leave a unit with an admin and no restricted
      // access, which is recoverable from its management page — so it is
      // reported rather than rolled back.
      if (grantedModels.length > 0) {
        await setBuModelGrants(
          ws.id,
          restrictedModels
            .filter((g) => grantedModels.includes(`${g.provider}::${g.model_id}`))
            .map((g) => ({ provider: g.provider, model_id: g.model_id })),
        );
      }
      if (grantedConnectors.length > 0) {
        await setBuConnectorGrants(ws.id, grantedConnectors as ConnectorKind[]);
      }
      // One call per server: `grantIntegrationAccess` grants a single target, and
      // there is no bulk MCP equivalent of setBuConnectorGrants. Sequential rather
      // than parallel so a failure part-way leaves a knowable state — the ones
      // before it are granted, and the unit's page shows exactly that.
      for (const id of grantedMcp) {
        await grantIntegrationAccess({
          kind: "mcp",
          id,
          workspaceId: ws.id,
          unitName: ws.displayName,
        });
      }
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
      {/* This form is long — name, cost centre, budget, status, per-model and
          per-connector access, then the admin. Scrolling the whole dialog
          worked but left Create somewhere below the fold with no sign it was
          there. So the body scrolls and the header and footer stay put: the
          primary action is always visible, and the inner model list keeps its
          own scroller without competing with the page. */}
      <DialogContent className="border-line-soft bg-panel-elevated flex max-h-[calc(100dvh-2rem)] max-w-lg flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="shrink-0 px-6 pt-6 pb-4">
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
          <form onSubmit={submit} className="flex min-h-0 flex-1 flex-col">
            <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-6 pb-5">
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

            <FormField
              control={form.control}
              name="monthlyBudgetUsd"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="text-muted-foreground font-mono text-xs tracking-wider uppercase">
                    Total budget <span className="normal-case opacity-60">(optional)</span>
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

            {/* What this unit will be able to use. Restricted grants are the
                only decisions here; the global ones are stated so the picture
                is complete rather than half of it. */}
            <div className="border-line-soft space-y-4 rounded-lg border border-dashed p-4">
              <p className="text-muted-foreground flex items-center gap-1.5 font-mono text-xs tracking-wider uppercase">
                <Boxes className="text-brand-bright size-3.5" aria-hidden />
                Access
              </p>

              <GrantPicker
                icon={Boxes}
                title="Models"
                globals={globalModels.map((g) => g.model_id)}
                options={restrictedModels.map((g) => ({
                  value: `${g.provider}::${g.model_id}`,
                  label: g.model_id,
                  hint: g.provider,
                }))}
                selected={grantedModels}
                onToggle={setGrantedModels}
                idPrefix="new-bu-model"
                emptyHint={`No restricted models — everything the org has approved reaches every ${BUSINESS_UNIT_LABEL.toLowerCase()}.`}
                loading={modelGrantsQ.isLoading}
              />

              <GrantPicker
                icon={Plug}
                title="Connectors"
                globals={globalConnectors.map((g) => CONNECTOR_KIND_LABEL[g.kind])}
                options={restrictedConnectors.map((g) => ({
                  value: g.kind,
                  label: CONNECTOR_KIND_LABEL[g.kind],
                  hint:
                    g.heldBy > 0
                      ? `${g.heldBy} ${g.heldBy === 1 ? "unit has" : "units have"} it`
                      : undefined,
                }))}
                selected={grantedConnectors}
                onToggle={setGrantedConnectors}
                idPrefix="new-bu-connector"
                emptyHint={`No restricted connectors — everything permitted reaches every ${BUSINESS_UNIT_LABEL.toLowerCase()}.`}
                loading={connectorGrantsQ.isLoading}
              />

              <GrantPicker
                icon={Terminal}
                title="MCP servers"
                globals={[]}
                options={(mcpServersQ.data ?? []).map((m) => ({
                  value: m.id,
                  label: m.server_name,
                  hint: m.description ?? undefined,
                }))}
                selected={grantedMcp}
                onToggle={setGrantedMcp}
                idPrefix="new-bu-mcp"
                emptyHint="No MCP server is registered yet — an Organization Admin adds them from Integrations."
                loading={mcpServersQ.isLoading}
              />

              <p className="text-muted-foreground text-[11px]">
                You can change any of this later from the {BUSINESS_UNIT_LABEL.toLowerCase()}&apos;s
                page. Models the organization has already keyed centrally need no further setup
                here.
              </p>
            </div>

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
                        list="new-bu-admin-emails"
                        className="border-line-soft bg-surface-1"
                        {...field}
                      />
                    </FormControl>
                    <datalist id="new-bu-admin-emails">
                      {(orgMembersQ.data ?? [])
                        .filter((m) => m.email)
                        .map((m) => (
                          <option key={m.userId} value={m.email!} />
                        ))}
                    </datalist>
                    <FormDescription className="text-[11px]">
                      Start typing to pick someone who already has an account, or
                      enter a new address to invite them.
                    </FormDescription>
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
            </div>

            <DialogFooter className="border-line-soft shrink-0 border-t px-6 py-4">
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
