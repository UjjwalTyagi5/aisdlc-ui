"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, ChevronsUpDown, Eye, Pencil, PencilRuler } from "lucide-react";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { listConnectors } from "@/lib/api/connectors";
import { listMcpServers } from "@/lib/api/mcp";
import { qk } from "@/lib/api/query-keys";
import { PHASE_ORDER, PHASE_LABEL } from "@/lib/agents";
import type { ToolAccessMode } from "@/lib/schemas/project";

/** stage -> selected ids (MCP server ids, or connector kinds) */
export type StageMap = Record<string, string[]>;

/** stage+tool composite key -> access mode. Unset = "both" (default). */
export type AccessModeMap = Record<string, ToolAccessMode>;

type ToolKind = "connector" | "mcp";

/** The key an assigned tool's access mode is stored under in AccessModeMap —
 *  a connector/MCP server can be read-only on one stage and read-write on
 *  another, so the mode is keyed per (stage, tool), not on the tool itself. */
export function accessModeKey(stageId: string, kind: ToolKind, toolId: string): string {
  return `${stageId}::${kind}::${toolId}`;
}

const ACCESS_MODE_META: Record<ToolAccessMode, { label: string; short: string; icon: React.ComponentType<{ className?: string }> }> = {
  read: { label: "Read only", short: "R", icon: Eye },
  write: { label: "Write only", short: "W", icon: Pencil },
  both: { label: "Read & write", short: "R+W", icon: PencilRuler },
};

// Per-stage tool assignment keys are AGENT IDs (Project.mcp_servers[agent_id] /
// connectors[agent_id]). Every phase id equals its agent id except the pipeline's
// `review` phase, whose agent id is `code_review` (see backend AGENT_REGISTRY).
const PHASE_TO_AGENT_ID: Record<string, string> = { review: "code_review" };

// Derived from the canonical pipeline so this list always matches the stages shown
// everywhere else (all 8, in order) instead of drifting when new stages are added.
export const TOOL_STAGES: { id: string; label: string }[] = PHASE_ORDER.map((p) => ({
  id: PHASE_TO_AGENT_ID[p] ?? p,
  label: PHASE_LABEL[p],
}));

type Opt = { id: string; label: string; hint?: string };

/** Compact 3-way Read/Write/Read&Write toggle for one assigned tool. */
function AccessModeToggle({
  value,
  onChange,
  disabled,
}: {
  value: ToolAccessMode;
  onChange: (mode: ToolAccessMode) => void;
  disabled?: boolean;
}) {
  return (
    <div className="border-line-soft inline-flex shrink-0 overflow-hidden rounded-md border">
      {(Object.keys(ACCESS_MODE_META) as ToolAccessMode[]).map((mode) => {
        const meta = ACCESS_MODE_META[mode];
        const active = value === mode;
        return (
          <Tooltip key={mode}>
            <TooltipTrigger asChild>
              <button
                type="button"
                disabled={disabled}
                onClick={() => onChange(mode)}
                aria-pressed={active}
                aria-label={meta.label}
                className={cn(
                  "px-1.5 py-1 font-mono text-[9.5px] font-semibold transition-colors",
                  active
                    ? "bg-primary/15 text-primary"
                    : "text-muted-foreground hover:bg-surface-2",
                )}
              >
                {meta.short}
              </button>
            </TooltipTrigger>
            <TooltipContent side="top">{meta.label}</TooltipContent>
          </Tooltip>
        );
      })}
    </div>
  );
}

/** The selected tools for one stage, each with its own access-mode toggle —
 *  rendered below the multi-select so the popover trigger stays a single
 *  click target for opening/closing the tool list. */
function SelectedToolsList({
  stageId,
  chosenConn,
  chosenMcp,
  accessModes,
  onAccessModeChange,
  disabled,
}: {
  stageId: string;
  chosenConn: Opt[];
  chosenMcp: Opt[];
  accessModes: AccessModeMap;
  onAccessModeChange: (key: string, mode: ToolAccessMode) => void;
  disabled?: boolean;
}) {
  if (chosenConn.length === 0 && chosenMcp.length === 0) return null;
  const rows: Array<{ key: string; label: string; variant: "outline" | "secondary" }> = [
    ...chosenConn.map((o) => ({
      key: accessModeKey(stageId, "connector", o.id),
      label: o.label,
      variant: "outline" as const,
    })),
    ...chosenMcp.map((o) => ({
      key: accessModeKey(stageId, "mcp", o.id),
      label: o.label,
      variant: "secondary" as const,
    })),
  ];
  return (
    <ul className="flex flex-col gap-1">
      {rows.map((r) => (
        <li key={r.key} className="flex items-center justify-between gap-2">
          <Badge variant={r.variant} className="min-w-0 truncate text-[10px]">
            {r.label}
          </Badge>
          <AccessModeToggle
            value={accessModes[r.key] ?? "both"}
            onChange={(mode) => onAccessModeChange(r.key, mode)}
            disabled={disabled}
          />
        </li>
      ))}
    </ul>
  );
}

function StageMultiSelect({
  connectorOptions,
  mcpOptions,
  selectedConnectors,
  selectedMcp,
  onToggleConnector,
  onToggleMcp,
  disabled,
}: {
  connectorOptions: Opt[];
  mcpOptions: Opt[];
  selectedConnectors: string[];
  selectedMcp: string[];
  onToggleConnector: (kind: string) => void;
  onToggleMcp: (id: string) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = React.useState(false);
  const chosenConn = connectorOptions.filter((o) => selectedConnectors.includes(o.id));
  const chosenMcp = mcpOptions.filter((o) => selectedMcp.includes(o.id));
  const empty = chosenConn.length === 0 && chosenMcp.length === 0;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          disabled={disabled}
          className="h-auto min-h-9 w-full justify-between border-line-soft bg-surface-1 font-normal"
        >
          <span className="flex flex-wrap gap-1">
            {empty ? (
              <span className="text-muted-foreground">Select tools…</span>
            ) : (
              <>
                {chosenConn.map((o) => (
                  <Badge key={`c:${o.id}`} variant="outline" className="text-[10px]">
                    {o.label}
                  </Badge>
                ))}
                {chosenMcp.map((o) => (
                  <Badge key={`m:${o.id}`} variant="secondary" className="text-[10px]">
                    {o.label}
                  </Badge>
                ))}
              </>
            )}
          </span>
          <ChevronsUpDown className="ml-2 size-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[var(--radix-popover-trigger-width)] p-0" align="start">
        <Command>
          <CommandInput placeholder="Search tools…" />
          <CommandList>
            <CommandEmpty>No tools found.</CommandEmpty>
            {connectorOptions.length > 0 && (
              <CommandGroup heading="Connectors">
                {connectorOptions.map((o) => {
                  const checked = selectedConnectors.includes(o.id);
                  return (
                    <CommandItem
                      key={`c:${o.id}`}
                      value={`connector ${o.label}`}
                      onSelect={() => onToggleConnector(o.id)}
                    >
                      <Check className={cn("mr-2 size-4", checked ? "opacity-100" : "opacity-0")} />
                      <span className="flex-1">{o.label}</span>
                    </CommandItem>
                  );
                })}
              </CommandGroup>
            )}
            {mcpOptions.length > 0 && (
              <CommandGroup heading="MCP servers">
                {mcpOptions.map((o) => {
                  const checked = selectedMcp.includes(o.id);
                  return (
                    <CommandItem
                      key={`m:${o.id}`}
                      value={`mcp ${o.label}`}
                      onSelect={() => onToggleMcp(o.id)}
                    >
                      <Check className={cn("mr-2 size-4", checked ? "opacity-100" : "opacity-0")} />
                      <span className="flex-1">{o.label}</span>
                      {o.hint && (
                        <span className="text-[10px] text-muted-foreground">{o.hint}</span>
                      )}
                    </CommandItem>
                  );
                })}
              </CommandGroup>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

/**
 * Per-stage tool assignment: each stage gets one searchable multi-select listing
 * both the tenant's installed connectors and the user's MCP servers. Controlled via
 * two stage→ids maps (connectors keyed by kind, MCP keyed by server id).
 */
export function ToolsStagePicker({
  mcpValue,
  onMcpChange,
  connectorValue,
  onConnectorChange,
  accessModeValue,
  onAccessModeChange,
  disabled,
  enabled = true,
  workspaceId,
}: {
  mcpValue: StageMap;
  onMcpChange: (next: StageMap) => void;
  connectorValue: StageMap;
  onConnectorChange: (next: StageMap) => void;
  /** Per (stage, tool) access mode — see accessModeKey(). Unset = "both". */
  accessModeValue: AccessModeMap;
  onAccessModeChange: (next: AccessModeMap) => void;
  disabled?: boolean;
  enabled?: boolean;
  /**
   * The project's parent Business Unit. Scopes the connector list to what the
   * project actually inherits — org-wide connectors plus that unit's own —
   * so a stage can never be wired to a sibling unit's integration
   * (PRD §34.3). Omitted only where there is no unit context yet, such as the
   * create-project dialog before a unit is chosen.
   */
  workspaceId?: string | null;
}) {
  const servers = useQuery({
    queryKey: qk.mcp.list(true),
    queryFn: () => listMcpServers(true),
    enabled,
  });
  const connectors = useQuery({
    queryKey: qk.connectors.list(workspaceId),
    queryFn: () => listConnectors(workspaceId),
    enabled,
  });

  const mcpOptions: Opt[] = (servers.data ?? []).map((s) => ({
    id: s.id,
    label: s.server_name,
    hint: s.transport,
  }));
  const connectorOptions: Opt[] = (connectors.data ?? [])
    .filter((c) => c.installed)
    .map((c) => ({ id: c.kind, label: c.name }));

  const toggle = (
    map: StageMap,
    onChange: (m: StageMap) => void,
    stage: string,
    kind: ToolKind,
    id: string,
  ) => {
    const cur = map[stage] ?? [];
    const removing = cur.includes(id);
    const next = removing ? cur.filter((x) => x !== id) : [...cur, id];
    onChange({ ...map, [stage]: next });
    if (removing) {
      // Drop the now-orphaned access-mode entry rather than leaving stale
      // state around for a tool that's no longer assigned to this stage.
      const key = accessModeKey(stage, kind, id);
      const { [key]: _removed, ...rest } = accessModeValue;
      onAccessModeChange(rest);
    }
  };

  const setAccessMode = (key: string, mode: ToolAccessMode) => {
    onAccessModeChange({ ...accessModeValue, [key]: mode });
  };

  if (mcpOptions.length === 0 && connectorOptions.length === 0) {
    return (
      <p className="text-[12px] text-muted-foreground">
        Nothing to assign yet. Add MCP servers or connect tools in{" "}
        <a href="/integrations" className="text-brand-bright underline">
          Integrations
        </a>
        .
      </p>
    );
  }

  return (
    <div className="space-y-3">
      {TOOL_STAGES.map((st) => {
        const selectedConnectors = connectorValue[st.id] ?? [];
        const selectedMcp = mcpValue[st.id] ?? [];
        const chosenConn = connectorOptions.filter((o) => selectedConnectors.includes(o.id));
        const chosenMcp = mcpOptions.filter((o) => selectedMcp.includes(o.id));
        return (
          <div
            key={st.id}
            className="grid grid-cols-1 items-start gap-2 sm:grid-cols-[130px_1fr]"
          >
            <span className="pt-2 text-xs font-medium text-muted-foreground">{st.label}</span>
            <div className="space-y-1.5">
              <StageMultiSelect
                connectorOptions={connectorOptions}
                mcpOptions={mcpOptions}
                selectedConnectors={selectedConnectors}
                selectedMcp={selectedMcp}
                onToggleConnector={(id) => toggle(connectorValue, onConnectorChange, st.id, "connector", id)}
                onToggleMcp={(id) => toggle(mcpValue, onMcpChange, st.id, "mcp", id)}
                disabled={disabled}
              />
              <SelectedToolsList
                stageId={st.id}
                chosenConn={chosenConn}
                chosenMcp={chosenMcp}
                accessModes={accessModeValue}
                onAccessModeChange={setAccessMode}
                disabled={disabled}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
