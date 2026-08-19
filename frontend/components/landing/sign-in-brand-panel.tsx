import { CheckCircle2, Network, Plug } from "lucide-react";

import { PwcMark } from "@/components/brand/pwc-mark";
import { cn } from "@/lib/utils";

/**
 * The brand half of the sign-in surface, shared by the landing popup and the
 * dedicated /login route.
 *
 * IT EXISTS BECAUSE THE TWO HAD DRIFTED. Both rendered a logo, a headline and three
 * selling points, and only one of them had been kept up: the popup used the display
 * face, the brand gradient on "coordinated AI agents" and the mesh background, while
 * /login used plain semibold text on a flat panel and still said "Ship FEATURES
 * through", "SSO (SAML / OIDC)" and a longer agent blurb nobody had revised. Landing
 * on /login directly — which is where an expired session and every emailed link send
 * you — looked like a different, older product.
 *
 * One copy, two hosts. The dialog gets it inside a translucent card; the page gets it
 * beside the sign-in column. Nothing here is auth-aware — the form and its mode
 * branching stay with the caller, which is what kept those in step already.
 */

const PROPS = [
  {
    icon: Network,
    title: "Thirteen agents, one control plane",
    text: "Requirements through Documentation, plus five track-specific agents for modernization, migration, and data engineering.",
  },
  {
    icon: CheckCircle2,
    title: "Enterprise-ready on day one",
    text: "SSO (OIDC), tenant isolation, immutable audit, cost & quality observability.",
  },
  {
    icon: Plug,
    title: "Plugs into your existing tools",
    text: "Azure DevOps, Jira, GitHub, Slack. No rip-and-replace.",
  },
];

export function SignInBrandPanel({ className }: { className?: string }) {
  return (
    <div className={cn("relative flex flex-col justify-between gap-10", className)}>
      {/* The atmosphere the flat version was missing. Both layers are decorative and
          pointer-events-none, so neither can intercept a click on the form. */}
      <div className="bg-mesh pointer-events-none absolute inset-0 opacity-70" aria-hidden />
      <div
        className="pointer-events-none absolute inset-x-0 top-0 h-px"
        aria-hidden
        style={{
          background:
            "linear-gradient(90deg, transparent, color-mix(in oklab, var(--brand-bright) 60%, transparent), transparent)",
        }}
      />

      <div className="relative flex items-center gap-3">
        <PwcMark size={42} />
        <div className="flex flex-col leading-tight">
          <span className="font-display text-sm font-bold tracking-tight">SDLC Platform</span>
          <span className="text-brand-bright font-mono text-[10px] font-semibold tracking-[0.14em] uppercase">
            Powered by PwC
          </span>
        </div>
      </div>

      <div className="relative space-y-6">
        <h2 className="font-display text-2xl leading-snug font-bold tracking-tight">
          Ship software through{" "}
          <span className="bg-brand-gradient bg-clip-text text-transparent">
            coordinated AI agents
          </span>
          .
        </h2>
        <ul className="space-y-5">
          {PROPS.map((p) => (
            <li key={p.title} className="flex items-start gap-3">
              <span className="bg-brand-bright/12 text-brand-bright ring-brand-bright/15 mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg ring-1">
                <p.icon className="size-4" aria-hidden />
              </span>
              <div className="space-y-0.5">
                <p className="text-sm font-semibold">{p.title}</p>
                <p className="text-muted-foreground text-xs">{p.text}</p>
              </div>
            </li>
          ))}
        </ul>
      </div>

      <p className="text-muted-foreground relative font-mono text-[10px] tracking-wide uppercase">
        SOC 2 Type I · GDPR-ready · BYO-LLM-key
      </p>
    </div>
  );
}
