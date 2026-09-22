import { describe, expect, it } from "vitest";

import { ProjectTechStack, TechStack } from "@/lib/schemas/tech-stacks";

const stack = {
  id: "s1", scope: "workspace", workspace_id: "w1", project_id: null, name: "Node + Next.js",
  description: "", categories: { languages: ["TypeScript"] }, notes: "", is_default: true,
};

describe("tech stack schemas", () => {
  it("parses the backend's stack and project view", () => {
    expect(TechStack.parse(stack).categories.languages).toEqual(["TypeScript"]);
    const view = ProjectTechStack.parse({
      project_id: "p1", workspace_id: "w1",
      options: { business_unit: [stack], project: [] },
      selection: { tech_stack_id: null },
      effective: { stack, source: "bu_default", warning: null },
      can_manage: true, can_manage_business_unit: false,
    });
    expect(view.effective.source).toBe("bu_default");
    expect(view.options.business_unit[0]?.is_default).toBe(true);
  });

  it("refuses a source it does not know", () => {
    expect(() => ProjectTechStack.parse({
      project_id: "p", workspace_id: null, options: { business_unit: [], project: [] },
      selection: { tech_stack_id: null }, effective: { stack: null, source: "org", warning: null },
    })).toThrow();
  });
});
