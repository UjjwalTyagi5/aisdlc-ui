import type { Meta, StoryObj } from "@storybook/react";

import { PhasePipeline } from "@/components/app/phase-pipeline";
import type { PhasePipelineEntry } from "@/lib/schemas";

const meta: Meta<typeof PhasePipeline> = {
  title: "App/PhasePipeline",
  component: PhasePipeline,
  tags: ["autodocs"],
  parameters: { layout: "padded" },
};

export default meta;
type Story = StoryObj<typeof PhasePipeline>;

const inFlight: PhasePipelineEntry[] = [
  { phase: "requirements", status: "approved" },
  { phase: "design", status: "approved" },
  { phase: "development", status: "running" },
  { phase: "review", status: "queued" },
  { phase: "testing", status: "queued" },
];

const awaitingReview: PhasePipelineEntry[] = [
  { phase: "requirements", status: "approved" },
  { phase: "design", status: "approved" },
  { phase: "development", status: "approved" },
  { phase: "review", status: "awaiting_approval" },
  { phase: "testing", status: "queued" },
];

const failed: PhasePipelineEntry[] = [
  { phase: "requirements", status: "approved" },
  { phase: "design", status: "approved" },
  { phase: "development", status: "failed" },
  { phase: "review", status: "queued" },
  { phase: "testing", status: "queued" },
];

const complete: PhasePipelineEntry[] = [
  { phase: "requirements", status: "approved" },
  { phase: "design", status: "approved" },
  { phase: "development", status: "approved" },
  { phase: "review", status: "merged" },
  { phase: "testing", status: "approved" },
];

export const InFlight: Story = { args: { pipeline: inFlight } };
export const AwaitingReview: Story = { args: { pipeline: awaitingReview } };
export const Failed: Story = { args: { pipeline: failed } };
export const Complete: Story = { args: { pipeline: complete } };
export const Compact: Story = { args: { pipeline: inFlight, density: "compact" } };
export const WithLinks: Story = {
  args: {
    pipeline: inFlight,
    hrefFor: (p) => `/projects/example/${p}`,
  },
};
