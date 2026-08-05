"use client";

import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Plus } from "lucide-react";

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
import { createMcpServer } from "@/lib/api/mcp";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";
import type { McpTransport } from "@/lib/schemas/mcp";

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
 */
export function AddMcpServerDialog({ onAdded }: { onAdded?: () => void }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = React.useState(false);
  const [name, setName] = React.useState("");
  const [description, setDescription] = React.useState("");
  const [transport, setTransport] = React.useState<McpTransport>("streamable_http");
  const [endpoint, setEndpoint] = React.useState("");

  const isStdio = transport === "stdio";

  const reset = () => {
    setName("");
    setDescription("");
    setTransport("streamable_http");
    setEndpoint("");
  };

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
    onSuccess: () => {
      toast.success(`${name.trim()} registered`, {
        description: `Grant it to a ${BUSINESS_UNIT_LABEL.toLowerCase()} to make it usable.`,
      });
      setOpen(false);
      reset();
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
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button
            onClick={() => save.mutate()}
            disabled={!name.trim() || !endpoint.trim() || save.isPending}
          >
            {save.isPending ? "Adding…" : "Add server"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
