import { z } from "zod";

import { ProfileScope } from "./agent-profiles";

/**
 * Agent Studio skill-management schemas. Field casing mirrors the sibling
 * prompt-profile feature (snake_case on the wire), matching the FROZEN backend
 * contract for the `/agent-skills` router (vendor + custom skills merged).
 *
 * A "skill" is a reusable instruction pack an agent loads on demand. Vendor
 * skills ship with the platform (read-only, toggle-only); custom skills are
 * authored per workspace (create / edit / soft-delete + version history).
 *
 * Lint failures on create/update reuse the profile feature's 422 shape
 * ({detail: {violations: [...]}}) — see getLintViolations in
 * lib/api/agent-profiles.ts, which reads ApiRequestError.rawBody.
 */

/** Scope a skill applies at — shared with the prompt-profile feature. */
export const SkillScope = ProfileScope;
export type SkillScope = z.infer<typeof SkillScope>;

/** Provenance of a skill row. */
export const SkillOrigin = z.enum(["vendor", "custom"]);
export type SkillOrigin = z.infer<typeof SkillOrigin>;

/** How a skill executes — shell skills get a distinct badge. */
export const SkillRuntime = z.enum(["llm", "shell"]);
export type SkillRuntime = z.infer<typeof SkillRuntime>;

/**
 * One row in the merged skills list (vendor + custom). `editable`/`deletable`
 * are server-authoritative — vendor skills are never editable/deletable;
 * custom skills are both. `version`/`active_version` are null for vendor.
 */
export const SkillListItem = z.object({
  origin: SkillOrigin,
  skill_key: z.string(),
  agent_id: z.string(),
  display_name: z.string(),
  description: z.string().nullable(),
  when_to_use: z.string().nullable(),
  runtime: SkillRuntime,
  enabled: z.boolean(),
  editable: z.boolean(),
  deletable: z.boolean(),
  version: z.number().int().nullable(),
  active_version: z.number().int().nullable(),
  /** Which tier this item's content actually lives at — null for vendor skills
   *  (no scope of their own). Differs from the requested scope when the item is
   *  inherited from an ancestor tier rather than authored at this one.
   *  `.default(null)` is defensive, not load-bearing: the backend always sends
   *  this key now, but a future regression there should degrade gracefully
   *  rather than fail every skill request. */
  origin_scope: SkillScope.nullable().default(null),
});
export type SkillListItem = z.infer<typeof SkillListItem>;

/** GET /agent-skills response — vendor + custom merged. */
export const SkillList = z.object({
  skills: z.array(SkillListItem),
});
export type SkillList = z.infer<typeof SkillList>;

/** Full skill including its markdown body and provenance metadata. */
export const SkillDetail = SkillListItem.extend({
  body: z.string().nullable(),
  created_by: z.string().nullable(),
  created_at: z.string().nullable(),
  updated_at: z.string().nullable(),
});
export type SkillDetail = z.infer<typeof SkillDetail>;

/** One saved version of a custom skill (custom skills only). */
export const SkillVersion = z.object({
  version: z.number().int(),
  is_active: z.boolean(),
  display_name: z.string(),
  created_by: z.string().nullable(),
  created_at: z.string().nullable(),
});
export type SkillVersion = z.infer<typeof SkillVersion>;

/** GET /agent-skills/{skill_key}/versions response. */
export const SkillVersionList = z.object({
  versions: z.array(SkillVersion),
});
export type SkillVersionList = z.infer<typeof SkillVersionList>;

/** POST /agent-skills body — create a v1 custom skill. */
export const SkillCreateInput = z.object({
  agent_id: z.string(),
  scope: SkillScope,
  scope_id: z.string().nullish(),
  skill_key: z.string(),
  display_name: z.string(),
  description: z.string().optional(),
  when_to_use: z.string().optional(),
  body: z.string(),
});
export type SkillCreateInput = z.infer<typeof SkillCreateInput>;

/** PUT /agent-skills/{skill_key} body — publish a new active version. */
export const SkillUpdateInput = z.object({
  agent_id: z.string(),
  scope: SkillScope,
  scope_id: z.string().nullish(),
  display_name: z.string(),
  description: z.string().optional(),
  when_to_use: z.string().optional(),
  body: z.string(),
});
export type SkillUpdateInput = z.infer<typeof SkillUpdateInput>;

/** POST /agent-skills/toggle body — enable/disable a vendor or custom skill.
 *  `workspace_id` lets the backend find a CUSTOM skill that's inherited from an
 *  ancestor tier (its existence check walks the same chain list_skills_merged
 *  does) — the toggle itself still always writes at `scope`. */
export const SkillToggleInput = z.object({
  agent_id: z.string(),
  scope: SkillScope,
  scope_id: z.string().nullish(),
  origin: SkillOrigin,
  skill_key: z.string(),
  enabled: z.boolean(),
  workspace_id: z.string().nullish(),
});
export type SkillToggleInput = z.infer<typeof SkillToggleInput>;

/** POST /agent-skills/toggle response. */
export const SkillToggleResult = z.object({
  origin: SkillOrigin,
  skill_key: z.string(),
  enabled: z.boolean(),
});
export type SkillToggleResult = z.infer<typeof SkillToggleResult>;

/** DELETE /agent-skills/{skill_key} response — soft delete (custom only). */
export const SkillDeleteResult = z.object({
  deleted: z.literal(true),
});
export type SkillDeleteResult = z.infer<typeof SkillDeleteResult>;

/** Client-side field caps — mirrored from the backend lint limits. */
export const SKILL_CAPS = {
  display_name: 200,
  description: 500,
  when_to_use: 500,
  body: 8000,
} as const;

export type SkillField = keyof SkillCreateInput;

/** Skill-key slug rule mirrored from the backend: lowercase, digits, hyphens. */
export const SKILL_KEY_PATTERN = /^[a-z0-9-]{2,64}$/;
