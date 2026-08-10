import type { CapabilityClass } from "@/lib/schemas/enums";

/**
 * Capability classification — PRD §13, §32.2, FR-04.
 *
 * Every agent action falls into exactly one of three classes. There is no
 * fourth class, and no sub-tiering of approvals ("sequential", "parallel" and
 * "exception" describe an approval *route* in FR-05, not a capability class).
 *
 *   🟢 Safe          Drafts, reads, analysis — stays inside the platform.
 *                    Runs immediately; no gate.
 *   🟡 Consequential Writes to an external system, or is irreversible.
 *                    Halts at a gate, routes to the owning role,
 *                    Project Admin is the fallback.
 *   🔴 Sign-off      Formal human acceptance for governance.
 *                    Explicit decision screen, audited distinctly from a
 *                    Consequential approval.
 *
 * This module is the single source of truth for how the three classes are
 * *presented*. Anything that renders a class — a gate row, a capability chip,
 * the approvals queue, an agent's action list — reads from here so the
 * vocabulary and colour never drift between screens.
 */

export interface CapabilityClassMeta {
  /** Display label, as the PRD names it. */
  label: string;
  /** What the class means, one line. */
  meaning: string;
  /** What the UI does when an action of this class fires. */
  uiBehaviour: string;
  /** Does this class halt for a human? */
  requiresApproval: boolean;
  /**
   * Semantic tone → maps to the existing design tokens in `globals.css`.
   * Deliberately reuses success/warning/destructive rather than introducing
   * a parallel palette.
   */
  tone: "success" | "warning" | "destructive";
  /** Tailwind classes for a chip rendering of this class. */
  chipClass: string;
  /** Tailwind classes for the small status dot form. */
  dotClass: string;
}

export const CAPABILITY_CLASS_ORDER: readonly CapabilityClass[] = [
  "safe",
  "consequential",
  "signoff",
] as const;

export const CAPABILITY_CLASS_META: Record<CapabilityClass, CapabilityClassMeta> = {
  safe: {
    label: "Safe",
    meaning: "Drafts, reads and analysis that stay inside the platform.",
    uiBehaviour: "Runs immediately — no gate.",
    requiresApproval: false,
    tone: "success",
    chipClass:
      "border-success/30 bg-success/10 text-success dark:bg-success/15",
    dotClass: "bg-success",
  },
  consequential: {
    label: "Consequential",
    meaning: "Writes to an external system, or is irreversible.",
    uiBehaviour:
      "Halts at an enforced gate and routes to the owning role; Project Admin is the fallback.",
    requiresApproval: true,
    tone: "warning",
    chipClass:
      "border-warning/40 bg-warning/10 text-warning-foreground dark:text-warning",
    dotClass: "bg-warning",
  },
  signoff: {
    label: "Sign-off",
    meaning: "Formal human acceptance for governance.",
    uiBehaviour:
      "Explicit decision screen, audited distinctly from a Consequential approval.",
    requiresApproval: true,
    tone: "destructive",
    chipClass:
      "border-destructive/35 bg-destructive/10 text-destructive",
    dotClass: "bg-destructive",
  },
};

/** Whether an action of this class halts for a human decision. */
export function requiresApproval(cls: CapabilityClass): boolean {
  return CAPABILITY_CLASS_META[cls].requiresApproval;
}

/** Display label for a capability class. */
export function capabilityClassLabel(cls: CapabilityClass): string {
  return CAPABILITY_CLASS_META[cls].label;
}

/**
 * The two mandatory checkpoints that cannot be waived (PRD §7 "Where the user
 * pauses", §37 "Blocked (security/release)", §44.5). Both are Sign-offs, and
 * neither the owning role nor the Project Admin fallback may skip them.
 */
export const MANDATORY_SIGNOFF_PHASES = ["security", "deployment"] as const;

export function isMandatorySignoff(phase: string): boolean {
  return (MANDATORY_SIGNOFF_PHASES as readonly string[]).includes(phase);
}
