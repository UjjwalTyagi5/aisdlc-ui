import { z } from "zod";

/**
 * Agent Studio tech stacks — backend `shared/routers/tech_stacks.py` (snake_case, like the
 * agent-skills API). A Business Unit offers stacks and marks one its default; a project
 * follows that default or picks another, or keeps its own. Exactly one applies.
 */

export const TechStackCategory = z.object({
  id: z.string(),
  label: z.string(),
  suggestions: z.array(z.string()).default([]),
});
export const TechStackCatalog = z.object({ categories: z.array(TechStackCategory) });
export type TechStackCatalog = z.infer<typeof TechStackCatalog>;

export const TechStack = z.object({
  id: z.string(),
  scope: z.enum(["workspace", "project"]),
  workspace_id: z.string().nullable(),
  project_id: z.string().nullable(),
  name: z.string(),
  description: z.string().default(""),
  categories: z.record(z.string(), z.array(z.string())).default({}),
  notes: z.string().default(""),
  is_default: z.boolean().default(false),
});
export type TechStack = z.infer<typeof TechStack>;

export const BusinessUnitTechStacks = z.object({
  items: z.array(TechStack),
  can_manage: z.boolean().default(false),
});
export type BusinessUnitTechStacks = z.infer<typeof BusinessUnitTechStacks>;

export const EffectiveTechStack = z.object({
  stack: TechStack.nullable(),
  source: z.enum(["project_selection", "bu_default", "none"]),
  warning: z.string().nullable().default(null),
});
export type EffectiveTechStack = z.infer<typeof EffectiveTechStack>;

export const ProjectTechStack = z.object({
  project_id: z.string(),
  workspace_id: z.string().nullable(),
  options: z.object({
    business_unit: z.array(TechStack).default([]),
    project: z.array(TechStack).default([]),
  }),
  selection: z.object({ tech_stack_id: z.string().nullable() }),
  effective: EffectiveTechStack,
  can_manage: z.boolean().default(false),
  can_manage_business_unit: z.boolean().default(false),
});
export type ProjectTechStack = z.infer<typeof ProjectTechStack>;

export const TechStackDeleted = z.object({ deleted: z.boolean(), id: z.string() });

export interface TechStackFieldsInput {
  name: string;
  description: string;
  categories: Record<string, string[]>;
  notes: string;
}
export interface TechStackCreateInput extends TechStackFieldsInput {
  scope: "workspace" | "project";
  scope_id: string;
}

export const SOURCE_LABEL: Record<EffectiveTechStack["source"], string> = {
  project_selection: "Chosen for this project",
  bu_default: "Business Unit default",
  none: "Not set",
};
