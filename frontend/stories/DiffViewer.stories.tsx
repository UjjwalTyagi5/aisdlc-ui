import type { Meta, StoryObj } from "@storybook/react";

import { DiffViewer } from "@/components/app/diff-viewer";

const meta: Meta<typeof DiffViewer> = {
  title: "App/DiffViewer",
  component: DiffViewer,
  tags: ["autodocs"],
  parameters: { layout: "padded" },
};

export default meta;
type Story = StoryObj<typeof DiffViewer>;

const ORIGINAL = `import { z } from "zod";

export const Story = z.object({
  title: z.string(),
  description: z.string(),
});
`;

const MODIFIED = `import { z } from "zod";

export const Story = z.object({
  title: z.string().min(1).max(200),
  description: z.string().max(10_000),
  acceptanceCriteria: z.array(
    z.object({
      given: z.string(),
      when: z.string(),
      then: z.string(),
    }),
  ),
});
`;

export const SplitView: Story = {
  args: {
    original: ORIGINAL,
    modified: MODIFIED,
    filename: "packages/schemas/story.ts",
    defaultMode: "split",
    height: 360,
  },
};

export const UnifiedView: Story = {
  args: {
    original: ORIGINAL,
    modified: MODIFIED,
    filename: "packages/schemas/story.ts",
    defaultMode: "unified",
    height: 360,
  },
};

export const NoToolbar: Story = {
  args: {
    original: ORIGINAL,
    modified: MODIFIED,
    filename: "schema.ts",
    showToolbar: false,
    height: 300,
  },
};
