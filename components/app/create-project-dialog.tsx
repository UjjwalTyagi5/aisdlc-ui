"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { ChevronDown, Loader2, ShieldAlert, Trash2, UserPlus, Wrench } from "lucide-react";
import { z } from "zod";

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
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ToolsStagePicker, type AccessModeMap, type StageMap } from "@/components/app/tools-stage-picker";
import { useSession } from "@/hooks/use-session";
import { useActiveWorkspace } from "@/hooks/use-workspaces";
import { effectivePlatformRole } from "@/lib/auth/effective-role";
import { createProject, listProjects } from "@/lib/api/projects";
import { budgetAllocation } from "@/lib/budget-allocation";
import { BudgetAllocationNotice } from "@/components/app/budget-allocation-notice";
import { BudgetWindowFieldsInput } from "@/components/app/budget-window-fields";
import { listWorkspaceMembers } from "@/lib/api/workspaces";
import { qk } from "@/lib/api/query-keys";
import { agentsForTrack, TRACK_META, TRACK_ORDER } from "@/lib/tracks";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";
import { DeliveryTrack, type Project, type ProjectCreateInput } from "@/lib/schemas";
import { resolveRoleLabel, useAllCustomRoles, useAssignableRoles } from "@/hooks/use-assignable-roles";

const formSchema = z
  .object({
    name: z.string().min(2, "Name must be at least 2 characters").max(60),
    description: z.string().max(2000).optional(),
    /** PRD §6 / FR-01 — the delivery track selects the agent roster and gates. */
    track: DeliveryTrack,
    workspaceId: z.string().min(1, `Choose a ${BUSINESS_UNIT_LABEL}`),
    // String, not number: blank has to mean "inherit the Business Unit's cap",
    // which is a different state from a cap of 0. Same reasoning as
    // create-workspace-dialog.tsx.
    monthlyBudgetUsd: z
      .string()
      .refine((v) => v.trim() === "" || (Number.isFinite(Number(v)) && Number(v) >= 0), {
        message: "Enter a positive amount, or leave blank to inherit",
      }),
    budgetStartDate: z.string(),
    budgetEndDate: z.string(),
  })
  .refine((v) => !(v.budgetStartDate && v.budgetEndDate) || v.budgetEndDate >= v.budgetStartDate, {
    message: "End date must be on or after the start date",
    path: ["budgetEndDate"],
  });
type FormValues = z.infer<typeof formSchema>;

interface ContributorRow {
  email: string;
  roleName: string;
}

// No repository-template picker at creation — the delivery track alone
// selects the agent roster (PRD §6); every project starts from a blank
// scaffold and the project settings page is where connectors/repos get
// wired up, not a canned template.
const DEFAULT_TEMPLATE: Project["template"] = "blank";

const cleanMap = (m: StageMap): Record<string, string[]> =>
  Object.fromEntries(Object.entries(m).filter(([, ids]) => ids.length > 0));

export interface CreateProjectDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** If provided, the user is navigated to the project on success. */
  navigateOnSuccess?: boolean;
}

export function CreateProjectDialog({
  open,
  onOpenChange,
  navigateOnSuccess = true,
}: CreateProjectDialogProps) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const session = useSession();
  const creatorRole = effectivePlatformRole(session);
  const needsApproval = creatorRole === "project_admin";
  const { workspaces, active: activeWorkspace } = useActiveWorkspace();
  const contributorRoles = useAssignableRoles("project");
  const allCustomRoles = useAllCustomRoles();

  // Per-stage tool assignment: MCP servers + connectors, each with its own
  // read/write/both access mode (lib/schemas/project.ts::ToolAccessMode).
  const [mcpSel, setMcpSel] = React.useState<StageMap>({});
  const [connSel, setConnSel] = React.useState<StageMap>({});
  const [accessModeSel, setAccessModeSel] = React.useState<AccessModeMap>({});
  const [toolsOpen, setToolsOpen] = React.useState(false);
  const toolsCount =
    Object.values(mcpSel).reduce((n, ids) => n + ids.length, 0) +
    Object.values(connSel).reduce((n, ids) => n + ids.length, 0);

  // Contributors assigned at creation time — added to the project the moment
  // it's created (or the moment it's approved, for a pending one), each
  // getting that role's agent access automatically via AGENT_OWNERSHIP.
  const [contributors, setContributors] = React.useState<ContributorRow[]>([]);
  const [contribEmail, setContribEmail] = React.useState("");
  const [contribRole, setContribRole] = React.useState<string>(contributorRoles[0]?.value ?? "");
  const [contributorsOpen, setContributorsOpen] = React.useState(false);

  const form = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      name: "",
      description: "",
      track: "greenfield",
      workspaceId: activeWorkspace?.id ?? "",
      monthlyBudgetUsd: "",
      budgetStartDate: "",
      budgetEndDate: "",
    },
  });

  // Reset whenever the dialog opens fresh
  React.useEffect(() => {
    if (open) {
      form.reset({
        name: "",
        description: "",
        track: "greenfield",
        workspaceId: activeWorkspace?.id ?? "",
      });
      setMcpSel({});
      setConnSel({});
      setAccessModeSel({});
      setToolsOpen(false);
      setContributors([]);
      setContribEmail("");
      setContributorsOpen(false);
    }
  }, [open, form, activeWorkspace?.id]);

  // Existing members of the selected BU — lets the creator pick a real person
  // instead of only inviting by email. Re-fetches if they switch the BU
  // picker mid-dialog.
  const selectedWorkspaceId = form.watch("workspaceId");
  const membersQ = useQuery({
    queryKey: qk.workspaces.members(selectedWorkspaceId),
    queryFn: () => listWorkspaceMembers(selectedWorkspaceId),
    enabled: open && contributorsOpen && !!selectedWorkspaceId,
  });
  const existingMembers = (membersQ.data ?? []).filter(
    (m): m is typeof m & { email: string } => !!m.email,
  );

  // Over-allocation is reported, not prevented (lib/budget-allocation.ts), so
  // this only ever needs to be accurate enough to explain the position — hence
  // one list fetched lazily, and only once a cap is actually being typed.
  const budgetInput = form.watch("monthlyBudgetUsd");
  const budgetEntered = budgetInput.trim() !== "";
  const siblingsQ = useQuery({
    queryKey: qk.projects.list({ pageSize: 200 }),
    queryFn: () => listProjects({ pageSize: 200 }),
    enabled: open && budgetEntered && !!selectedWorkspaceId,
    staleTime: 60_000,
  });
  const allocation = React.useMemo(() => {
    const unit = workspaces.find((w) => w.id === selectedWorkspaceId);
    const siblings = (siblingsQ.data?.items ?? []).filter(
      (p) => p.workspaceId === selectedWorkspaceId,
    );
    const proposed = budgetEntered ? Number(budgetInput) : null;
    return budgetAllocation(
      unit?.monthlyBudgetUsd ?? null,
      siblings,
      Number.isFinite(proposed) ? proposed : null,
    );
  }, [workspaces, selectedWorkspaceId, siblingsQ.data, budgetEntered, budgetInput]);
  const pickableMembers = existingMembers.filter(
    (m) => !contributors.some((c) => c.email.toLowerCase() === m.email.toLowerCase()),
  );

  const mutation = useMutation({
    mutationFn: (input: ProjectCreateInput) => createProject(input),
    onMutate: async (input) => {
      await queryClient.cancelQueries({ queryKey: qk.projects.all() });
      const previous = queryClient.getQueriesData<{ items: Project[]; pagination: unknown }>({
        queryKey: qk.projects.all(),
      });
      const now = new Date().toISOString();
      const optimistic = {
        id: `tmp_${Date.now()}`,
        tenantId: "ws_acme",
        name: input.name,
        slug: input.name.toLowerCase().replace(/[^a-z0-9]+/g, "-"),
        description: input.description,
        workspaceId: input.workspaceId,
        approvalStatus: needsApproval ? "pending_approval" : "active",
        template: DEFAULT_TEMPLATE,
        track: input.track,
        archived: false,
        owners: [],
        // The roster is a function of the track (PRD §6) — never a fixed list.
        pipeline: agentsForTrack(input.track).map((phase, i) => ({
          phase,
          status: i === 0 ? "draft" : "queued",
        })),
        lastActivityAt: now,
        createdAt: now,
      } as unknown as Project;
      for (const [key, data] of previous) {
        if (!data || !Array.isArray(data.items)) continue;
        queryClient.setQueryData(key, { ...data, items: [optimistic, ...data.items] });
      }
      return { previous };
    },
    onError: (err, _input, ctx) => {
      if (ctx?.previous) {
        for (const [key, data] of ctx.previous) queryClient.setQueryData(key, data);
      }
      toast.error("Couldn't create project", {
        description: err instanceof Error ? err.message : undefined,
      });
    },
    onSuccess: (project) => {
      if (project.approvalStatus === "pending_approval") {
        toast.info(`"${project.name}" sent for approval`, {
          description: `A Business Unit Admin needs to approve it before it goes live.`,
        });
      } else {
        toast.success(`Project "${project.name}" created`);
      }
      onOpenChange(false);
      if (navigateOnSuccess) router.push(`/projects/${project.id}`);
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: qk.projects.all() });
    },
  });

  const submit = form.handleSubmit((values) => {
    const mcp_servers = cleanMap(mcpSel);
    const connectors = cleanMap(connSel);
    mutation.mutate({
      name: values.name,
      track: values.track,
      template: DEFAULT_TEMPLATE,
      description: values.description || undefined,
      workspaceId: values.workspaceId,
      monthlyBudgetUsd:
        values.monthlyBudgetUsd.trim() === "" ? null : Number(values.monthlyBudgetUsd),
      budgetStartDate: values.budgetStartDate || null,
      budgetEndDate: values.budgetEndDate || null,
      contributors: contributors.length
        ? contributors.map((c) => ({ email: c.email, roleName: c.roleName }))
        : undefined,
      mcp_servers: Object.keys(mcp_servers).length ? mcp_servers : undefined,
      connectors: Object.keys(connectors).length ? connectors : undefined,
      tool_access_modes: Object.keys(accessModeSel).length ? accessModeSel : undefined,
    });
  });

  const addContributorEmail = (email: string, roleName: string) => {
    if (contributors.some((c) => c.email.toLowerCase() === email.toLowerCase())) return;
    setContributors((prev) => [...prev, { email, roleName }]);
  };

  const addContributor = () => {
    const email = contribEmail.trim();
    if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return;
    addContributorEmail(email, contribRole);
    setContribEmail("");
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] max-w-2xl overflow-y-auto border-line-soft bg-panel-elevated">
        <DialogHeader>
          <div className="mb-1 font-mono text-[11px] uppercase tracking-widest text-brand-bright">
            New project
          </div>
          <DialogTitle className="font-display text-xl font-bold tracking-tight">
            Create a project
          </DialogTitle>
          <DialogDescription className="text-[13px]">
            A project is where your agents run. Pick a template to seed sensible defaults.
          </DialogDescription>
        </DialogHeader>

        {needsApproval && (
          <div className="flex items-start gap-2 rounded-lg border border-warning/30 bg-warning/10 px-3 py-2.5 text-[12.5px] text-warning">
            <ShieldAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
            <span>
              This project will be sent to the {BUSINESS_UNIT_LABEL}&apos;s Admin for approval
              before it goes live.
            </span>
          </div>
        )}

        <Form {...form}>
          <form onSubmit={submit} className="space-y-5">
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
                    Project name
                  </FormLabel>
                  <FormControl>
                    <Input
                      autoFocus
                      placeholder="Checkout service"
                      autoComplete="off"
                      className="border-line-soft bg-surface-1 font-mono focus-visible:border-primary"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="workspaceId"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
                    {BUSINESS_UNIT_LABEL}
                  </FormLabel>
                  <Select value={field.value} onValueChange={field.onChange}>
                    <FormControl>
                      <SelectTrigger className="border-line-soft bg-surface-1">
                        <SelectValue placeholder={`Select a ${BUSINESS_UNIT_LABEL.toLowerCase()}…`} />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {workspaces.map((w) => (
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

            <FormField
              control={form.control}
              name="monthlyBudgetUsd"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="text-muted-foreground font-mono text-xs tracking-wider uppercase">
                    Monthly budget{" "}
                    <span className="text-muted-foreground/60 normal-case">(optional)</span>
                  </FormLabel>
                  <FormControl>
                    <div className="relative">
                      <span className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 font-mono text-sm">
                        $
                      </span>
                      <Input
                        inputMode="decimal"
                        placeholder={`Inherit the ${BUSINESS_UNIT_LABEL.toLowerCase()}'s cap`}
                        autoComplete="off"
                        className="border-line-soft bg-surface-1 pl-7 font-mono"
                        {...field}
                      />
                    </div>
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            {budgetInput.trim() !== "" && (
              <div className="space-y-3">
                <BudgetWindowFieldsInput
                  start={form.watch("budgetStartDate")}
                  end={form.watch("budgetEndDate")}
                  onStartChange={(v) =>
                    form.setValue("budgetStartDate", v, { shouldValidate: true })
                  }
                  onEndChange={(v) => form.setValue("budgetEndDate", v, { shouldValidate: true })}
                  error={form.formState.errors.budgetEndDate?.message ?? null}
                />
                <BudgetAllocationNotice allocation={allocation} />
              </div>
            )}

            <FormField
              control={form.control}
              name="description"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
                    Description{" "}
                    <span className="normal-case text-muted-foreground/60">(optional)</span>
                  </FormLabel>
                  <FormControl>
                    <Textarea
                      rows={2}
                      placeholder="What is this project for? Agents use this for context."
                      className="border-line-soft bg-surface-1 text-sm focus-visible:border-primary"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            {/* Delivery track — PRD §6 / FR-01. Chosen before the technical
                template because it selects the agent roster and the gates. */}
            <FormField
              control={form.control}
              name="track"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
                    Delivery track
                  </FormLabel>
                  <p className="text-muted-foreground -mt-1 text-[12px]">
                    Selects the agent roster, required evidence and approval route.
                  </p>
                  <FormControl>
                    <RadioGroup
                      value={field.value}
                      onValueChange={field.onChange}
                      className="grid grid-cols-1 gap-2"
                    >
                      {TRACK_ORDER.map((t) => {
                        const meta = TRACK_META[t];
                        const count = agentsForTrack(t).length;
                        return (
                          <Label
                            key={t}
                            htmlFor={`track-${t}`}
                            className={cn(
                              "border-line-soft bg-surface-1 flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors",
                              "hover:border-primary/40",
                              field.value === t && "border-primary bg-primary/5",
                            )}
                          >
                            <RadioGroupItem
                              id={`track-${t}`}
                              value={t}
                              className="mt-0.5"
                            />
                            <span className="min-w-0 flex-1">
                              <span className="flex flex-wrap items-center gap-2">
                                <span className="text-muted-foreground font-mono text-[10px] tracking-wider uppercase">
                                  Track {meta.number}
                                </span>
                                <span className="text-[13px] font-semibold">
                                  {meta.label}
                                </span>
                                <span className="border-line-soft text-muted-foreground rounded-full border px-1.5 py-px font-mono text-[10px]">
                                  {count} agents
                                </span>
                              </span>
                              <span className="text-muted-foreground mt-1 block text-[12px] leading-snug">
                                {meta.summary}
                              </span>
                            </span>
                          </Label>
                        );
                      })}
                    </RadioGroup>
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            {/* Contributors — assigned at creation time; each gets that role's
                agent access on this project automatically. */}
            <div className="rounded-xl border border-line-soft bg-surface-1">
              <button
                type="button"
                onClick={() => setContributorsOpen((v) => !v)}
                aria-expanded={contributorsOpen}
                className="flex w-full items-center justify-between gap-2 px-3 py-2.5 text-left"
              >
                <span className="flex items-center gap-2">
                  <UserPlus className="size-4 text-muted-foreground" aria-hidden />
                  <span className="text-sm font-medium">Contributors</span>
                  <span className="text-[12px] text-muted-foreground">
                    {contributors.length > 0
                      ? `${contributors.length} assigned`
                      : "Optional — assign BA, Architect, Developer, and other roles"}
                  </span>
                </span>
                <ChevronDown
                  className={cn(
                    "size-4 shrink-0 text-muted-foreground transition-transform",
                    contributorsOpen && "rotate-180",
                  )}
                  aria-hidden
                />
              </button>
              {contributorsOpen && (
                <div className="space-y-3 border-t border-line-soft p-3">
                  {contributors.length > 0 && (
                    <ul className="flex flex-col gap-1.5">
                      {contributors.map((c) => (
                        <li
                          key={c.email}
                          className="flex items-center justify-between gap-2 rounded-md border border-line-soft bg-panel-elevated px-2.5 py-1.5"
                        >
                          <span className="min-w-0 flex-1 truncate text-[12.5px]">{c.email}</span>
                          <Badge variant="secondary" className="shrink-0 font-mono text-[10px]">
                            {resolveRoleLabel(c.roleName, allCustomRoles)}
                          </Badge>
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            className="size-6 shrink-0"
                            aria-label={`Remove ${c.email}`}
                            onClick={() =>
                              setContributors((prev) => prev.filter((x) => x.email !== c.email))
                            }
                          >
                            <Trash2 className="size-3.5" aria-hidden />
                          </Button>
                        </li>
                      ))}
                    </ul>
                  )}
                  <div className="flex items-center gap-2">
                    <Select
                      value=""
                      onValueChange={(email) => addContributorEmail(email, contribRole)}
                      disabled={!selectedWorkspaceId || membersQ.isLoading}
                    >
                      <SelectTrigger className="border-line-soft bg-panel-elevated flex-1">
                        <SelectValue
                          placeholder={
                            !selectedWorkspaceId
                              ? `Choose a ${BUSINESS_UNIT_LABEL.toLowerCase()} first`
                              : membersQ.isLoading
                                ? "Loading members…"
                                : pickableMembers.length === 0
                                  ? `No more ${BUSINESS_UNIT_LABEL.toLowerCase()} members to add`
                                  : `Add an existing ${BUSINESS_UNIT_LABEL.toLowerCase()} member…`
                          }
                        />
                      </SelectTrigger>
                      <SelectContent>
                        {pickableMembers.map((m) => (
                          <SelectItem key={m.userId} value={m.email}>
                            {m.displayName ?? m.email} <span className="text-muted-foreground">· {m.email}</span>
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <Select value={contribRole} onValueChange={(v) => setContribRole(v)}>
                      <SelectTrigger className="w-44 shrink-0 border-line-soft bg-panel-elevated">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {contributorRoles.map((r) => (
                          <SelectItem key={r.value} value={r.value}>
                            {r.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>

                  <div className="flex items-center gap-2">
                    <Input
                      placeholder="Or invite someone new by email — name@company.com"
                      value={contribEmail}
                      onChange={(e) => setContribEmail(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          addContributor();
                        }
                      }}
                      className="border-line-soft bg-panel-elevated text-sm"
                    />
                    <Button type="button" variant="outline" onClick={addContributor}>
                      Add
                    </Button>
                  </div>
                  <p className="text-[11px] text-muted-foreground">
                    Pick someone already in this {BUSINESS_UNIT_LABEL.toLowerCase()}, or invite a new
                    email — new people are onboarded automatically, no separate invite step.
                  </p>
                </div>
              )}
            </div>

            {/* Tools per stage — connectors + MCP servers, assigned per agent stage. */}
            <div className="rounded-xl border border-line-soft bg-surface-1">
              <button
                type="button"
                onClick={() => setToolsOpen((v) => !v)}
                aria-expanded={toolsOpen}
                className="flex w-full items-center justify-between gap-2 px-3 py-2.5 text-left"
              >
                <span className="flex items-center gap-2">
                  <Wrench className="size-4 text-muted-foreground" aria-hidden />
                  <span className="text-sm font-medium">Tools per stage</span>
                  <span className="text-[12px] text-muted-foreground">
                    {toolsCount > 0
                      ? `${toolsCount} selected`
                      : "Optional — connectors & MCP servers per agent"}
                  </span>
                </span>
                <ChevronDown
                  className={cn(
                    "size-4 shrink-0 text-muted-foreground transition-transform",
                    toolsOpen && "rotate-180",
                  )}
                  aria-hidden
                />
              </button>
              {toolsOpen && (
                <div className="border-t border-line-soft p-3">
                  <ToolsStagePicker
                    mcpValue={mcpSel}
                    onMcpChange={setMcpSel}
                    connectorValue={connSel}
                    onConnectorChange={setConnSel}
                    accessModeValue={accessModeSel}
                    onAccessModeChange={setAccessModeSel}
                    enabled={open}
                  />
                </div>
              )}
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
                className="bg-gradient-to-br from-brand-gradient-from to-brand-gradient-to font-semibold text-white"
              >
                {mutation.isPending && <Loader2 className="size-4 animate-spin" aria-hidden />}
                Create project
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
