import { Activity, CheckCircle2, GitPullRequest, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

const phases = [
  { name: "Requirements", status: "approved", icon: CheckCircle2, tone: "success" as const },
  { name: "Design", status: "approved", icon: CheckCircle2, tone: "success" as const },
  { name: "Development", status: "in progress", icon: GitPullRequest, tone: "info" as const },
  { name: "Review", status: "queued", icon: Activity, tone: "muted" as const },
  { name: "Testing", status: "queued", icon: Activity, tone: "muted" as const },
];

const toneClass: Record<"success" | "info" | "muted" | "warning" | "error", string> = {
  success: "bg-success/10 text-success border-success/20",
  info: "bg-info/10 text-info border-info/20",
  muted: "bg-muted text-muted-foreground border-border",
  warning: "bg-warning/10 text-warning-foreground border-warning/20",
  error: "bg-destructive/10 text-destructive border-destructive/20",
};

export default function Playground() {
  return (
    <div className="mx-auto max-w-5xl space-y-10 p-4 md:p-10">
      {/* Editorial page header — Mission Control elevation */}
      <header className="space-y-1">
        <p className="font-mono text-[11px] font-semibold uppercase tracking-[0.14em] text-brand-bright">
          Developer
        </p>
        <div className="flex items-center gap-3">
          <div className="bg-primary text-primary-foreground grid size-9 place-items-center rounded-lg">
            <Sparkles className="size-5" aria-hidden />
          </div>
          <div className="flex flex-col leading-tight">
            <h1 className="font-display text-2xl font-bold tracking-tight">Design playground</h1>
            <span className="text-muted-foreground text-sm">
              Tokens, semantic tones, button matrix
            </span>
          </div>
        </div>
      </header>

      <Card className="border-line-soft bg-panel-elevated shadow-none">
        <CardHeader>
          <CardTitle className="font-display font-semibold">Semantic surfaces</CardTitle>
          <CardDescription>Tokens read from CSS variables in globals.css.</CardDescription>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-6">
          {[
            { label: "background", cls: "bg-background text-foreground border" },
            { label: "card", cls: "bg-card text-card-foreground border" },
            { label: "muted", cls: "bg-muted text-muted-foreground" },
            { label: "accent", cls: "bg-accent text-accent-foreground" },
            { label: "secondary", cls: "bg-secondary text-secondary-foreground" },
            { label: "primary", cls: "bg-primary text-primary-foreground" },
          ].map((s) => (
            <div
              key={s.label}
              className={`flex h-16 items-center justify-center rounded-md font-mono text-xs font-medium ${s.cls}`}
            >
              {s.label}
            </div>
          ))}
        </CardContent>
      </Card>

      <Card className="border-line-soft bg-panel-elevated shadow-none">
        <CardHeader>
          <CardTitle className="font-display font-semibold">Phase pipeline (preview)</CardTitle>
          <CardDescription>Real component ships in Chunk 6.</CardDescription>
        </CardHeader>
        <CardContent>
          <ol className="flex flex-wrap gap-2">
            {phases.map((p, i) => (
              <li
                key={p.name}
                className={`flex items-center gap-2 rounded-md border px-3 py-2 text-sm ${toneClass[p.tone]}`}
              >
                <span className="font-mono text-xs text-muted-foreground">{i + 1}</span>
                <p.icon className="size-4" aria-hidden />
                <span className="font-medium">{p.name}</span>
                <span className="font-mono text-xs opacity-80">{p.status}</span>
              </li>
            ))}
          </ol>
        </CardContent>
        <CardFooter className="flex items-center justify-between gap-3">
          <p className="text-muted-foreground text-sm">
            Focus ring, dark mode, motion-reduced fallback — all functional.
          </p>
          <div className="flex gap-2">
            <Button variant="outline" size="sm">
              Cancel
            </Button>
            <Button size="sm">Run agent</Button>
          </div>
        </CardFooter>
      </Card>

      <Card className="border-line-soft bg-panel-elevated shadow-none">
        <CardHeader>
          <CardTitle className="font-display font-semibold">Button variants</CardTitle>
          <CardDescription>Tab through to verify focus rings.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-3">
          <Button>Default</Button>
          <Button variant="secondary">Secondary</Button>
          <Button variant="outline">Outline</Button>
          <Button variant="ghost">Ghost</Button>
          <Button variant="link">Link</Button>
          <Button variant="destructive">Destructive</Button>
          <Button disabled>Disabled</Button>
        </CardContent>
      </Card>

      {/* Elevation tokens showcase */}
      <Card className="border-line-soft bg-panel-elevated shadow-none">
        <CardHeader>
          <CardTitle className="font-display font-semibold">Elevation surfaces</CardTitle>
          <CardDescription>Plan-01 token layer — surface-1, surface-2, panel-elevated.</CardDescription>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          {[
            { label: "surface-1", cls: "bg-surface-1 text-foreground border-line-soft" },
            { label: "surface-2", cls: "bg-surface-2 text-foreground border-line-soft" },
            { label: "panel-elevated", cls: "bg-panel-elevated text-foreground border-line-soft" },
          ].map((s) => (
            <div
              key={s.label}
              className={`flex h-16 items-center justify-center rounded-md border font-mono text-xs font-medium ${s.cls}`}
            >
              {s.label}
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
