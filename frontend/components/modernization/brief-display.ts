import type {
  BriefDriver,
  BriefLayer,
  BriefMilestone,
  MigrationIntentBrief,
} from "@/lib/schemas/modernization";

/**
 * What the brief view shows, derived from the stored brief — the same rules as the
 * backend's `brief.py`, so the page, the chat and the Word/PDF downloads agree:
 *
 *   - a version-1 brief (no tagged drivers, no per-layer change) still renders: its
 *     plain drivers are tagged by their words, and today → target becomes one row;
 *   - the key facts are only what the brief actually holds (no empty tiles).
 */

export const CHANGE_LABEL: Record<string, string> = {
  upgrade: "Upgrade", rewrite: "Rewrite", replatform: "Re-platform", replace: "Replace",
  retire: "Retire", keep: "Keep as is", new: "New",
};
export const STATUS_LABEL: Record<string, string> = {
  eol: "End of life", approaching: "Support ending", legacy: "Legacy", supported: "Supported",
};
export const DRIVER_LABEL: Record<string, string> = {
  end_of_support: "End of support", security: "Security", cost: "Cost", skills: "Skills",
  compliance: "Compliance", performance: "Performance", other: "Other",
};
export const EFFORT_LABEL: Record<string, string> = { low: "Low", medium: "Medium", high: "High" };
export const MILESTONE_LABEL: Record<string, string> = {
  start: "Start", freeze: "Freeze", compliance: "Compliance", deadline: "Deadline",
  cutover: "Cutover", decommission: "Decommission", other: "Milestone",
};

const CATEGORY_WORDS: [RegExp, string][] = [
  [/end.of.(support|life)|\beol\b|unsupported|out of support/i, "end_of_support"],
  [/secur|vulnerab|\bcve|log4j/i, "security"],
  [/complian|regulat|audit|soc ?2/i, "compliance"],
  [/cost|lease|hosting|licen|budget/i, "cost"],
  [/hir(e|ing)|skill|talent|retir/i, "skills"],
  [/perform|latency|scal|slow/i, "performance"],
];

export function guessCategory(text: string): string {
  return CATEGORY_WORDS.find(([re]) => re.test(text))?.[1] ?? "other";
}

export function displayDrivers(brief: MigrationIntentBrief): BriefDriver[] {
  if (brief.drivers.length) return brief.drivers.filter((d) => d.title || d.detail);
  return brief.business_drivers
    .map((t) => t.trim())
    .filter(Boolean)
    .map((title) => ({ category: guessCategory(title), title, detail: "" }));
}

export function displayLayers(brief: MigrationIntentBrief): BriefLayer[] {
  if (brief.layers.length) return brief.layers;
  if (brief.current_state.stack || brief.target_state.stack) {
    return [{
      layer: "Whole system", current: brief.current_state.stack, current_status: "",
      target: brief.target_state.stack, change_type: "", modules: [],
    }];
  }
  return [];
}

export function endOfLifeCount(brief: MigrationIntentBrief): number {
  const parts = brief.module_changes.length ? brief.module_changes : brief.layers;
  return parts.filter((p) => p.current_status === "eol").length;
}

/** "2027-06-30" or "30 June 2027" → a local date; null when it is not a date. */
export function parseDate(text: string): Date | null {
  const t = (text || "").trim();
  const iso = /^(\d{4})-(\d{2})-(\d{2})/.exec(t);
  if (iso) return new Date(Number(iso[1]), Number(iso[2]) - 1, Number(iso[3]));
  const parsed = Date.parse(t);
  return Number.isNaN(parsed) ? null : new Date(parsed);
}

export function prettyDate(text: string): string {
  const d = parseDate(text);
  return d ? d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : text;
}

export function sortedMilestones(brief: MigrationIntentBrief): BriefMilestone[] {
  return [...brief.milestones].sort((a, b) => {
    const da = parseDate(a.date)?.getTime() ?? Number.MAX_SAFE_INTEGER;
    const db = parseDate(b.date)?.getTime() ?? Number.MAX_SAFE_INTEGER;
    return da - db || a.label.localeCompare(b.label);
  });
}

export type KeyFact = { key: "deadline" | "budget" | "scope" | "eol" | "target"; label: string; value: string };

export function keyFacts(brief: MigrationIntentBrief): KeyFact[] {
  const facts: KeyFact[] = [];
  if (brief.deadline) facts.push({ key: "deadline", label: "Deadline", value: prettyDate(brief.deadline) });
  if (brief.budget) facts.push({ key: "budget", label: "Budget", value: brief.budget });
  const scope = brief.in_scope.filter((s) => s.trim()).length;
  if (scope) facts.push({ key: "scope", label: "In scope", value: `${scope} item${scope === 1 ? "" : "s"}` });
  const eol = endOfLifeCount(brief);
  if (eol) facts.push({ key: "eol", label: "End of life today", value: `${eol} part${eol === 1 ? "" : "s"}` });
  const rec = brief.recommendation;
  if (rec && (rec.summary || brief.layers.length)) {
    facts.push({
      key: "target", label: "Target stack",
      value: rec.recommended_by === "user" ? "Set by business" : "Recommended",
    });
  }
  return facts;
}

/** The sections the brief has, in order — the same plan as the Word/PDF documents. */
export type BriefSection =
  | "glance" | "why" | "recommendation" | "target_state" | "scope" | "modules"
  | "tradeoffs" | "timeline" | "constraints" | "success" | "people" | "risks";

export function sectionPlan(brief: MigrationIntentBrief): BriefSection[] {
  const plan: BriefSection[] = ["glance", "why"];
  const rec = brief.recommendation;
  if (rec && (rec.summary || rec.rationale.length || rec.alternatives.length)) plan.push("recommendation");
  else if (brief.target_state.description) plan.push("target_state");
  plan.push("scope");
  if (brief.module_changes.length) plan.push("modules");
  if (brief.trade_offs.length) plan.push("tradeoffs");
  if (brief.milestones.length) plan.push("timeline");
  plan.push("constraints", "success");
  if (brief.stakeholders.length) plan.push("people");
  plan.push("risks");
  return plan;
}
