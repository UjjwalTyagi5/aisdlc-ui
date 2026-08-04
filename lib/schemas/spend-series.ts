import { z } from "zod";

/**
 * Monthly spend split by one dimension — the contract behind the dashboard's
 * spend chart and its filters.
 *
 * Separate from `OrgOverview` on purpose: the overview backs tiles that change
 * only when the organisation does, while this changes every time someone
 * touches a filter. Folding them together would make a group-by click refetch
 * the connector and people counts for no reason.
 */
/**
 * `provider` rolls the model split up one level — Anthropic rather than each
 * Claude. It is the grouping Model Management wants, because a provider is
 * what you onboard, credential and pay; a model is what you grant. Derived
 * from the same per-model figures, so the two views reconcile.
 */
export const SpendGroupBy = z.enum(["business_unit", "project", "model", "provider"]);
export type SpendGroupBy = z.infer<typeof SpendGroupBy>;

export const SPEND_GROUP_BY_LABEL: Record<SpendGroupBy, string> = {
  business_unit: "Business unit",
  project: "Project",
  model: "Model",
  provider: "Provider",
};

export const SpendSeriesEntry = z.object({
  /** Workspace id, project id or model name, depending on `groupBy`. */
  id: z.string(),
  name: z.string(),
  /** One figure per entry in `months`, oldest first. */
  points: z.array(z.number()),
});
export type SpendSeriesEntry = z.infer<typeof SpendSeriesEntry>;

export const SpendSeries = z.object({
  /** `YYYY-MM` labels, oldest first. */
  months: z.array(z.string()),
  groupBy: SpendGroupBy,
  series: z.array(SpendSeriesEntry),
});
export type SpendSeries = z.infer<typeof SpendSeries>;
