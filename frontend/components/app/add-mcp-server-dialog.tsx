"use client";

import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { CheckCircle2, Loader2, Plus, XCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { createMcpServer, probeMcpServer, testMcpConnection } from "@/lib/api/mcp";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";
import type { McpTestResult, McpTransport } from "@/lib/schemas/mcp";

const TRANSPORTS: { id: McpTransport; label: string }[] = [
  { id: "streamable_http", label: "Streamable HTTP" },
  { id: "sse", label: "SSE" },
  { id: "stdio", label: "STDIO (local)" },
];

/**
 * Register an MCP server.
 *
 * WHY THIS EXISTS WHEN CONNECTORS HAVE NO EQUIVALENT. A connector kind is a
 * known vendor — Jira, GitHub, SharePoint — so the catalogue ships with them
 * and there is nothing to add. An MCP server is whatever someone stood up at
 * whatever address, so it does not exist until it is named here. Without this
 * the estate could only ever shrink.
 *
 * REGISTERING IS NOT GRANTING. A new server reaches nobody until it is given
 * to a {BUSINESS_UNIT_LABEL} on its own screen — the same two-step everything
 * else on this page follows, and the reason the form asks for no units.
 *
 * NO CREDENTIALS. Headers, env vars and tokens are the calling project's, on
 * /projects/[id]/integrations. This form takes only what identifies the server.
 *
 * TEST BEFORE SAVING. `POST /mcp/registry/test-connection` opens a real session
 * against the address typed above and lists the tools it answers with, on an
 * unsaved config. A registration that turns out to point nowhere is discovered
 * by whichever project first wires it to a stage, long after whoever typed the
 * address has left the screen — so the check belongs here, while the person who
 * knows the right address is still looking at the wrong one. Optional, not a
 * gate: a server that is legitimately not reachable from here (an STDIO command
 * that runs beside the agent, an internal host behind a network the API cannot
 * see) is still worth naming.
 */
export function AddMcpServerDialog({ onAdded }: { onAdded?: () => void }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = React.useState(false);
  const [name, setName] = React.useState("");
  const [description, setDescription] = React.useState("");
  const [transport, setTransport] = React.useState<McpTransport>("streamable_http");
  const [endpoint, setEndpoint] = React.useState("");

  const isStdio = transport === "stdio";
  // Last probe result, or null when nothing has been tested yet. Cleared the
  // moment any field changes: a green tick sitting above an edited URL is a
  // claim about a config that no longer exists.
  const [tested, setTested] = React.useState<McpTestResult | null>(null);

  const reset = () => {
    setName("");
    setDescription("");
    setTransport("streamable_http");
    setEndpoint("");
    setTested(null);
  };

  const probe = useMutation({
    mutationFn: () =>
      testMcpConnection({
        server_name: name.trim(),
        description: description.trim() || null,
        transport,
        url: isStdio ? null : endpoint.trim() || null,
        command: isStdio ? endpoint.trim() || null : null,
        args: [],
      }),
    // The backend answers `{ok:false, error}` for a server that refused rather
    // than raising, so a failed probe is a normal result — reported in place,
    // not as a toast that outlives the dialog.
    onSuccess: (r) => setTested(r),
    onError: (e: Error) => setTested({ ok: false, tools: [], error: e.message }),
  });

  // Keyed on what decides reachability, not on every field: renaming a server
  // that just answered does not make the answer stale, but re-pointing it does.
  React.useEffect(() => {
    setTested(null);
  }, [transport, endpoint]);

  const save = useMutation({
    mutationFn: () =>
      createMcpServer({
        server_name: name.trim(),
        description: description.trim() || null,
        transport,
        url: isStdio ? null : endpoint.trim() || null,
        command: isStdio ? endpoint.trim() || null : null,
        args: [],
        is_active: true,
      }),
    onSuccess: async (server) => {
      toast.success(`${name.trim()} registered`, {
        description: `Grant it to a ${BUSINESS_UNIT_LABEL.toLowerCase()} to make it usable.`,
      });
      setOpen(false);
      reset();
      // Record what it offers, now, while the person who registered it is still
      // the one holding `connector:manage` over it. `test-connection` above
      // proves the address answers but saves nothing — only `probe` writes
      // `tools_snapshot`, and that snapshot is the ONLY way anyone but the
      // creator can ever see this server's tools (the registry's per-id read is
      // creator-scoped). Left to the first person who wondered, the answer
      // would be "not probed yet" for a server that had just been tested.
      //
      // Deliberately not awaited into the save's own success/failure: the
      // registration stands whether or not the probe lands, and a server that
      // is legitimately unreachable from the API host must not report itself
      // as failed to register.
      try {
        await probeMcpServer(server.id);
      } catch {
        // Unreachable from here — the snapshot stays empty and the card says so.
      }
      queryClient.invalidateQueries({ queryKey: ["integration-access"] });
      queryClient.invalidateQueries({ queryKey: ["mcp"] });
      onAdded?.();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <Dialog open={open} onOpenChange={(o) => (o ? setOpen(true) : (setOpen(false), reset()))}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" className="border-line-soft shrink-0">
          <Plus className="size-4" aria-hidden />
          Add MCP server
        </Button>
      </DialogTrigger>

      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add an MCP server</DialogTitle>
          <DialogDescription>
            Names a server the organization may use. It reaches nobody until you grant it to a{" "}
            {BUSINESS_UNIT_LABEL.toLowerCase()}.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="mcp-name">Name</Label>
            <Input
              id="mcp-name"
              autoFocus
              value={name}
              placeholder="Postgres (staging)"
              onChange={(e) => setName(e.target.value)}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="mcp-description">What it does</Label>
            <Input
              id="mcp-description"
              value={description}
              placeholder="Query access to the staging database."
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>

          <div className="space-y-2">
            <Label>Transport</Label>
            <div className="flex flex-wrap gap-2">
              {TRANSPORTS.map((t) => (
                <Button
                  key={t.id}
                  type="button"
                  variant={transport === t.id ? "default" : "outline"}
                  size="sm"
                  onClick={() => setTransport(t.id)}
                  className={transport === t.id ? undefined : "border-line-soft"}
                >
                  {t.label}
                </Button>
              ))}
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="mcp-endpoint">{isStdio ? "Command" : "URL"}</Label>
            <Input
              id="mcp-endpoint"
              value={endpoint}
              placeholder={isStdio ? "mcp-server-filesystem" : "https://mcp.internal.example/"}
              onChange={(e) => setEndpoint(e.target.value)}
            />
            <p className="text-muted-foreground text-[11.5px]">
              {isStdio
                ? "Run locally beside the agent — no network address."
                : "Where the server answers. Any token it needs is supplied per project."}
            </p>
          </div>

          {/* The probe's answer, stated where the address it judged is still
              visible. A failure names the server's own error rather than
              "couldn't connect": the reason is the part that tells you which
              field to change. */}
          {tested && (
            <div
              className={cn(
                "rounded-lg border p-3 text-[12px]",
                tested.ok
                  ? "border-success/30 bg-success/5"
                  : "border-destructive/30 bg-destructive/5",
              )}
            >
              <div className="flex items-center gap-1.5 font-medium">
                {tested.ok ? (
                  <>
                    <CheckCircle2 className="text-success size-4 shrink-0" aria-hidden />
                    Answered with {tested.tools.length}{" "}
                    {tested.tools.length === 1 ? "tool" : "tools"}
                  </>
                ) : (
                  <>
                    <XCircle className="text-destructive size-4 shrink-0" aria-hidden />
                    Didn&apos;t answer
                  </>
                )}
              </div>
              {tested.ok && tested.tools.length > 0 && (
                <p className="text-muted-foreground mt-1 font-mono text-[11px] break-words">
                  {tested.tools.map((t) => t.name).join(" · ")}
                </p>
              )}
              {!tested.ok && tested.error && (
                <p className="text-muted-foreground mt-1 break-words">{tested.error}</p>
              )}
            </div>
          )}
        </div>

        <DialogFooter className="sm:justify-between">
          {/* Testing is optional, so it sits apart from the save path rather
              than in front of it — an unreachable-from-here server is still
              worth registering. */}
          <Button
            type="button"
            variant="outline"
            className="border-line-soft"
            onClick={() => probe.mutate()}
            disabled={!endpoint.trim() || probe.isPending || save.isPending}
          >
            {probe.isPending ? (
              <>
                <Loader2 className="size-4 animate-spin" aria-hidden />
                Testing…
              </>
            ) : (
              "Test connection"
            )}
          </Button>
          <div className="flex items-center gap-2">
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={() => save.mutate()}
              disabled={!name.trim() || !endpoint.trim() || save.isPending || probe.isPending}
            >
              {save.isPending ? "Adding…" : "Add server"}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
