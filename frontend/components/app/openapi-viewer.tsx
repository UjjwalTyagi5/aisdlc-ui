"use client";

import * as React from "react";
import YAML from "yaml";
import { ChevronDown, ChevronRight } from "lucide-react";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { ErrorState } from "@/components/ui/error-state";

/**
 * Lightweight read-only OpenAPI viewer themed with our tokens.
 * Parses YAML or JSON spec and renders paths grouped by resource.
 * For full try-it UX swap in `@scalar/api-reference-react` post-MVP.
 */

interface Operation {
  method: string;
  path: string;
  summary?: string;
  description?: string;
  tags?: string[];
  parameters?: Array<{ name: string; in: string; required?: boolean; description?: string }>;
  responses?: Record<string, { description?: string }>;
}

interface ParsedSpec {
  title: string;
  version: string;
  description?: string;
  servers: Array<{ url: string; description?: string }>;
  operations: Operation[];
  tags: string[];
}

function parseSpec(input: string): ParsedSpec {
  const trimmed = input.trim();
  const raw =
    trimmed.startsWith("{") ? (JSON.parse(trimmed) as Record<string, unknown>) : (YAML.parse(trimmed) as Record<string, unknown>);
  const info = (raw.info as Record<string, unknown>) ?? {};
  const paths = (raw.paths as Record<string, Record<string, Operation>>) ?? {};
  const operations: Operation[] = [];
  const tags = new Set<string>();
  for (const [path, ops] of Object.entries(paths)) {
    for (const [method, spec] of Object.entries(ops)) {
      if (!/^(get|post|put|patch|delete|options|head|trace)$/i.test(method)) continue;
      const op = spec as Operation;
      operations.push({ ...op, method: method.toUpperCase(), path });
      (op.tags ?? []).forEach((t) => tags.add(t));
    }
  }
  return {
    title: String(info.title ?? "API"),
    version: String(info.version ?? "0.0.0"),
    description: info.description ? String(info.description) : undefined,
    servers:
      ((raw.servers as Array<{ url: string; description?: string }>) ?? []).map((s) => ({
        url: s.url,
        description: s.description,
      })),
    operations,
    tags: Array.from(tags),
  };
}

const METHOD_TONE: Record<string, string> = {
  GET: "bg-info/15 text-info border-info/30",
  POST: "bg-success/15 text-success border-success/30",
  PUT: "bg-warning/15 text-foreground border-warning/40",
  PATCH: "bg-warning/15 text-foreground border-warning/40",
  DELETE: "bg-destructive/15 text-destructive border-destructive/30",
};

export interface OpenApiViewerProps {
  /** Raw spec — YAML or JSON string. */
  source: string;
  className?: string;
}

export function OpenApiViewer({ source, className }: OpenApiViewerProps) {
  const [filter, setFilter] = React.useState("");
  const parsed = React.useMemo(() => {
    try {
      return { ok: true as const, spec: parseSpec(source) };
    } catch (err) {
      return {
        ok: false as const,
        message: err instanceof Error ? err.message : "Failed to parse spec",
      };
    }
  }, [source]);

  if (!parsed.ok) {
    return (
      <ErrorState
        title="Couldn't parse OpenAPI spec"
        description="Check that the input is valid YAML or JSON."
        detail={parsed.message}
      />
    );
  }

  const { spec } = parsed;
  const q = filter.trim().toLowerCase();
  const grouped = new Map<string, Operation[]>();
  for (const op of spec.operations) {
    if (
      q &&
      !op.path.toLowerCase().includes(q) &&
      !op.summary?.toLowerCase().includes(q) &&
      !op.method.toLowerCase().includes(q)
    ) {
      continue;
    }
    const tag = op.tags?.[0] ?? "default";
    const arr = grouped.get(tag) ?? [];
    arr.push(op);
    grouped.set(tag, arr);
  }

  return (
    <div className={cn("bg-panel-elevated flex flex-col rounded-md border border-line-soft", className)}>
      {/* Elevated panel header */}
      <header className="space-y-2 border-b border-line-soft p-4">
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="font-display text-lg font-semibold">{spec.title}</h2>
          <Badge variant="secondary" className="font-mono text-[10.5px]">
            v{spec.version}
          </Badge>
        </div>
        {spec.description && (
          <p className="text-muted-foreground text-sm">{spec.description}</p>
        )}
        {spec.servers.length > 0 && (
          <ul className="text-muted-foreground font-mono text-xs">
            {spec.servers.map((s) => (
              <li key={s.url}>
                <span className="text-foreground">server</span> · {s.url}
                {s.description && <span className="ml-2">{s.description}</span>}
              </li>
            ))}
          </ul>
        )}
        <Input
          placeholder="Filter paths or methods…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="h-8 font-sans text-sm"
          aria-label="Filter operations"
        />
      </header>

      <div className="flex-1 space-y-4 overflow-auto p-4">
        {grouped.size === 0 ? (
          <p className="text-muted-foreground text-sm">No operations match.</p>
        ) : (
          Array.from(grouped.entries()).map(([tag, ops]) => (
            <section key={tag}>
              <h3 className="font-display text-muted-foreground mb-2 text-[10.5px] font-semibold uppercase tracking-[0.1em]">
                {tag}
              </h3>
              <ul className="space-y-1">
                {ops.map((op) => (
                  <OperationRow key={`${op.method}-${op.path}`} op={op} />
                ))}
              </ul>
            </section>
          ))
        )}
      </div>
    </div>
  );
}

function OperationRow({ op }: { op: Operation }) {
  const [open, setOpen] = React.useState(false);
  return (
    <li className="rounded-md border border-line-soft bg-surface-1">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="hover:bg-surface-2 flex w-full items-center gap-3 rounded-md px-3 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1"
        aria-expanded={open}
      >
        {open ? (
          <ChevronDown className="text-muted-foreground size-4" aria-hidden />
        ) : (
          <ChevronRight className="text-muted-foreground size-4" aria-hidden />
        )}
        <span
          className={cn(
            "inline-flex w-16 justify-center rounded border px-1.5 py-0.5 font-mono text-[10px] font-bold uppercase",
            METHOD_TONE[op.method] ?? "bg-muted text-muted-foreground border-border",
          )}
        >
          {op.method}
        </span>
        <span className="flex-1 truncate font-mono text-sm">{op.path}</span>
        {op.summary && (
          <span className="text-muted-foreground truncate text-sm">{op.summary}</span>
        )}
      </button>
      {open && (
        <div className="space-y-3 border-t border-line-soft px-3 py-3 text-sm">
          {op.description && <p className="text-muted-foreground">{op.description}</p>}
          {op.parameters && op.parameters.length > 0 && (
            <div>
              <h4 className="font-display mb-1 text-xs font-semibold uppercase tracking-wider">
                Parameters
              </h4>
              <ul className="space-y-1">
                {op.parameters.map((p) => (
                  <li key={`${p.in}-${p.name}`} className="font-mono text-xs">
                    <span className="text-foreground font-semibold">{p.name}</span>
                    <span className="text-muted-foreground">
                      {" "}
                      in {p.in}
                      {p.required && " · required"}
                    </span>
                    {p.description && (
                      <span className="text-muted-foreground ml-2 font-sans">
                        — {p.description}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {op.responses && (
            <div>
              <h4 className="font-display mb-1 text-xs font-semibold uppercase tracking-wider">
                Responses
              </h4>
              <ul className="space-y-1 font-mono text-xs">
                {Object.entries(op.responses).map(([code, r]) => (
                  <li key={code}>
                    <span className="text-foreground font-semibold">{code}</span>
                    {r.description && (
                      <span className="text-muted-foreground ml-2 font-sans">
                        — {r.description}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </li>
  );
}
