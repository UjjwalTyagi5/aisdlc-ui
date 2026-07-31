"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Info, Loader2, Pencil, Plug, Plus, RefreshCw, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
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
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Textarea } from "@/components/ui/textarea";
import { ApiErrorState } from "@/components/feedback/api-error-state";
import { LoadingState } from "@/components/ui/loading-state";
import { qk } from "@/lib/api/query-keys";
import {
  createMcpServer,
  deleteMcpServer,
  listMcpServers,
  probeMcpServer,
  testMcpConnection,
  updateMcpServer,
  type McpServerInput,
} from "@/lib/api/mcp";
import type { McpServer, McpTransport } from "@/lib/schemas/mcp";

const TRANSPORTS: { id: McpTransport; label: string }[] = [
  { id: "streamable_http", label: "Streamable HTTP" },
  { id: "sse", label: "SSE" },
  { id: "stdio", label: "STDIO (local)" },
];

function parseKV(text: string): Record<string, string> | null {
  const out: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const t = line.trim();
    if (!t) continue;
    const i = t.indexOf("=");
    if (i === -1) continue;
    out[t.slice(0, i).trim()] = t.slice(i + 1).trim();
  }
  return Object.keys(out).length ? out : null;
}

const EMPTY_FORM = {
  server_name: "",
  description: "",
  transport: "streamable_http" as McpTransport,
  url: "",
  command: "",
  argsText: "",
  headersText: "",
  envText: "",
};

/** Info popover listing the server's cached tools (name + description). */
function ToolsPopover({ server }: { server: McpServer }) {
  const tools = server.tools_snapshot ?? [];
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="sm" aria-label="View tools">
          <Info className="size-4" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 p-0">
        <div className="border-b px-3 py-2 text-sm font-medium">
          Tools {tools.length > 0 && `(${tools.length})`}
        </div>
        {tools.length === 0 ? (
          <p className="text-muted-foreground p-3 text-xs">
            No tools cached yet. Click <span className="font-medium">Probe</span> to discover them.
          </p>
        ) : (
          <ul className="max-h-72 divide-y overflow-y-auto">
            {tools.map((t) => (
              <li key={t.name} className="px-3 py-2">
                <div className="font-mono text-xs font-medium">{t.name}</div>
                {t.description && (
                  <p className="text-muted-foreground mt-0.5 line-clamp-3 text-[11px]">
                    {t.description}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </PopoverContent>
    </Popover>
  );
}

/** Self-contained MCP server management: list + register/edit + probe + delete.
 *  Rendered standalone (/admin/mcp) and embedded in the Integrations page. */
export function McpServersPanel() {
  const queryClient = useQueryClient();
  const [open, setOpen] = React.useState(false);
  const [editingId, setEditingId] = React.useState<string | null>(null);
  const [form, setForm] = React.useState({ ...EMPTY_FORM });
  const [testing, setTesting] = React.useState(false);

  const servers = useQuery({
    queryKey: qk.mcp.list(false),
    queryFn: () => listMcpServers(false),
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["mcp"] });

  function closeDialog() {
    setOpen(false);
    setEditingId(null);
    setForm({ ...EMPTY_FORM });
  }

  function openCreate() {
    setEditingId(null);
    setForm({ ...EMPTY_FORM });
    setOpen(true);
  }

  function openEdit(s: McpServer) {
    setEditingId(s.id);
    setForm({
      server_name: s.server_name,
      description: s.description ?? "",
      transport: s.transport,
      url: s.url ?? "",
      command: s.command ?? "",
      argsText: (s.args ?? []).join("\n"),
      headersText: "",
      envText: "",
    });
    setOpen(true);
  }

  const buildPayload = (): McpServerInput => {
    const p: McpServerInput = {
      server_name: form.server_name.trim(),
      description: form.description.trim() || null,
      transport: form.transport,
      url: form.transport === "stdio" ? null : form.url.trim() || null,
      command: form.transport === "stdio" ? form.command.trim() || null : null,
      args:
        form.transport === "stdio"
          ? form.argsText.split("\n").map((a) => a.trim()).filter(Boolean)
          : [],
      is_active: true,
    };
    const headers = form.transport === "stdio" ? null : parseKV(form.headersText);
    if (headers) p.headers = headers;
    const env = parseKV(form.envText);
    if (env) p.env_vars = env;
    return p;
  };

  const save = useMutation({
    mutationFn: () =>
      editingId ? updateMcpServer(editingId, buildPayload()) : createMcpServer(buildPayload()),
    onSuccess: () => {
      toast.success(editingId ? "MCP server updated" : "MCP server registered");
      closeDialog();
      invalidate();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const remove = useMutation({
    mutationFn: (id: string) => deleteMcpServer(id),
    onSuccess: () => {
      toast.success("MCP server removed");
      invalidate();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const probe = useMutation({
    mutationFn: (id: string) => probeMcpServer(id),
    onSuccess: (r) => {
      if (r.ok) toast.success(`Connected — ${r.tools.length} tool(s) discovered`);
      else toast.error(r.error ?? "Connection failed");
      invalidate();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  async function handleTest() {
    setTesting(true);
    try {
      const p = buildPayload();
      const r = await testMcpConnection({
        server_name: p.server_name || "probe",
        transport: p.transport,
        url: p.url,
        command: p.command,
        args: p.args,
        headers: p.headers,
        env_vars: p.env_vars,
      });
      if (r.ok) toast.success(`Connected — ${r.tools.length} tool(s) found`);
      else toast.error(r.error ?? "Connection failed");
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setTesting(false);
    }
  }

  const isStdio = form.transport === "stdio";

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1">
          <span className="text-muted-foreground font-mono text-[10px] tracking-[0.16em] uppercase">
            Your servers
          </span>
          <h2 className="font-display flex items-center gap-2 text-xl font-bold tracking-[-0.015em]">
            <Plug className="size-5" /> MCP Servers
          </h2>
          <p className="text-muted-foreground text-[13px]">
            Only you can see the servers you add. Choose which stages may use them when you create a
            project.
          </p>
        </div>
        <Button onClick={openCreate} className="shrink-0">
          <Plus className="mr-2 h-4 w-4" /> Register MCP Server
        </Button>
      </div>

      {servers.isLoading ? (
        <LoadingState />
      ) : servers.isError ? (
        <ApiErrorState
          title="Couldn't load MCP servers"
          error={
            servers.error && "code" in servers.error && "message" in servers.error
              ? (servers.error as { code: string; message: string; requestId?: string })
              : undefined
          }
          description={servers.error instanceof Error ? servers.error.message : undefined}
          onRetry={() => servers.refetch()}
        />
      ) : servers.data && servers.data.length > 0 ? (
        <div className="grid gap-3">
          {servers.data.map((s) => (
            <Card key={s.id} className="flex items-start justify-between p-4">
              <div className="space-y-1">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{s.server_name}</span>
                  <Badge variant="outline">{s.transport}</Badge>
                  {!s.is_active && <Badge variant="secondary">inactive</Badge>}
                </div>
                {s.description && <p className="text-muted-foreground text-sm">{s.description}</p>}
                <p className="text-muted-foreground text-xs">
                  {s.url || s.command}
                  {s.tools_snapshot && s.tools_snapshot.length > 0 &&
                    ` · ${s.tools_snapshot.length} tool(s)`}
                </p>
              </div>
              <div className="flex items-center gap-1">
                <ToolsPopover server={s} />
                <Button variant="outline" size="sm" onClick={() => openEdit(s)}>
                  <Pencil className="mr-1 h-3 w-3" /> Edit
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => probe.mutate(s.id)}
                  disabled={probe.isPending}
                >
                  <RefreshCw className="mr-1 h-3 w-3" /> Probe
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => remove.mutate(s.id)}
                  disabled={remove.isPending}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
            </Card>
          ))}
        </div>
      ) : (
        <Card className="text-muted-foreground p-8 text-center text-sm">
          No MCP servers registered yet.
        </Card>
      )}

      <Dialog open={open} onOpenChange={(o) => (o ? setOpen(true) : closeDialog())}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{editingId ? "Edit MCP Server" : "Register MCP Server"}</DialogTitle>
            <DialogDescription>
              Connect an MCP server. Stage access is assigned later, when you create a project.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            <div className="space-y-2">
              <Label>Transport</Label>
              <div className="flex gap-2">
                {TRANSPORTS.map((t) => (
                  <Button
                    key={t.id}
                    type="button"
                    variant={form.transport === t.id ? "default" : "outline"}
                    size="sm"
                    onClick={() => setForm((f) => ({ ...f, transport: t.id }))}
                  >
                    {t.label}
                  </Button>
                ))}
              </div>
            </div>

            <div className="space-y-2">
              <Label htmlFor="mcp-name">Name</Label>
              <Input
                id="mcp-name"
                value={form.server_name}
                onChange={(e) => setForm((f) => ({ ...f, server_name: e.target.value }))}
                placeholder="github-mcp"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="mcp-desc">Description</Label>
              <Input
                id="mcp-desc"
                value={form.description}
                onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                placeholder="Optional"
              />
            </div>

            {isStdio ? (
              <>
                <div className="space-y-2">
                  <Label htmlFor="mcp-cmd">Command</Label>
                  <Input
                    id="mcp-cmd"
                    value={form.command}
                    onChange={(e) => setForm((f) => ({ ...f, command: e.target.value }))}
                    placeholder="npx"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="mcp-args">Arguments (one per line)</Label>
                  <Textarea
                    id="mcp-args"
                    rows={3}
                    value={form.argsText}
                    onChange={(e) => setForm((f) => ({ ...f, argsText: e.target.value }))}
                    placeholder={"-y\n@modelcontextprotocol/server-filesystem\n/path"}
                  />
                </div>
              </>
            ) : (
              <>
                <div className="space-y-2">
                  <Label htmlFor="mcp-url">Server URL</Label>
                  <Input
                    id="mcp-url"
                    value={form.url}
                    onChange={(e) => setForm((f) => ({ ...f, url: e.target.value }))}
                    placeholder="https://example.com/mcp"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="mcp-headers">
                    Headers (KEY=VALUE per line){editingId ? " — leave blank to keep existing" : ""}
                  </Label>
                  <Textarea
                    id="mcp-headers"
                    rows={2}
                    value={form.headersText}
                    onChange={(e) => setForm((f) => ({ ...f, headersText: e.target.value }))}
                    placeholder="Authorization=Bearer xxx"
                  />
                </div>
              </>
            )}

            <div className="space-y-2">
              <Label htmlFor="mcp-env">
                Environment variables (KEY=VALUE per line)
                {editingId ? " — leave blank to keep existing" : ""}
              </Label>
              <Textarea
                id="mcp-env"
                rows={2}
                value={form.envText}
                onChange={(e) => setForm((f) => ({ ...f, envText: e.target.value }))}
                placeholder="API_TOKEN=xxx"
              />
            </div>
          </div>

          <DialogFooter className="gap-2">
            <Button variant="outline" onClick={handleTest} disabled={testing}>
              {testing && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Test Connection
            </Button>
            <Button onClick={() => save.mutate()} disabled={save.isPending || !form.server_name.trim()}>
              {save.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {editingId ? "Save changes" : "Register"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
