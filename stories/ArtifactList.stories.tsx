import type { Meta, StoryObj } from "@storybook/react";

import { ArtifactList } from "@/components/app/artifact-list";
import type { Artifact, ArtifactId, ProjectId } from "@/lib/schemas";

const meta: Meta<typeof ArtifactList> = {
  title: "App/ArtifactList",
  component: ArtifactList,
  tags: ["autodocs"],
  parameters: { layout: "padded" },
};

export default meta;
type Story = StoryObj<typeof ArtifactList>;

const projectId = "ingest" as ProjectId;
const now = new Date();
const iso = (hoursAgo: number) =>
  new Date(now.getTime() - hoursAgo * 3_600_000).toISOString();

const items: Artifact[] = [
  {
    id: "art_1" as ArtifactId,
    projectId,
    runId: null,
    type: "story",
    phase: "requirements",
    title: "Replay failed ingest batch",
    version: 2,
    contentHash: "a".repeat(64),
    status: "approved",
    body: {
      kind: "story",
      title: "Replay failed ingest batch",
      description: "…",
      acceptanceCriteria: [],
    },
    createdBy: "agent",
    createdAt: iso(12),
    updatedAt: iso(2),
  },
  {
    id: "art_2" as ArtifactId,
    projectId,
    runId: null,
    type: "c4_diagram",
    phase: "design",
    title: "Container diagram",
    version: 1,
    contentHash: "b".repeat(64),
    status: "awaiting_approval",
    body: { kind: "c4_diagram", source: "graph LR\n  A-->B" },
    createdBy: "agent",
    createdAt: iso(6),
    updatedAt: iso(1),
  },
  {
    id: "art_3" as ArtifactId,
    projectId,
    runId: null,
    type: "openapi_spec",
    phase: "design",
    title: "Ingest API v1",
    version: 3,
    contentHash: "c".repeat(64),
    status: "running",
    body: { kind: "openapi_spec", yaml: "openapi: 3.1.0" },
    createdBy: "agent",
    createdAt: iso(3),
    updatedAt: iso(0.5),
  },
  {
    id: "art_4" as ArtifactId,
    projectId,
    runId: null,
    type: "pr",
    phase: "development",
    title: "feat(ingest): idempotent replay endpoint",
    version: 1,
    contentHash: "d".repeat(64),
    status: "merged",
    body: {
      kind: "pr",
      url: "https://github.com/acme/ingest/pull/42",
      branch: "feat/replay",
      title: "feat(ingest): idempotent replay endpoint",
      additions: 420,
      deletions: 88,
      filesChanged: 17,
      files: [],
      reviewComments: [],
      checks: [],
    },
    createdBy: "agent",
    createdAt: iso(20),
    updatedAt: iso(18),
  },
];

export const Populated: Story = {
  render: () => (
    <div className="w-96">
      <ArtifactList items={items} selectedId="art_2" onSelect={() => {}} />
    </div>
  ),
};

export const Loading: Story = {
  render: () => (
    <div className="w-96">
      <ArtifactList items={null} isLoading />
    </div>
  ),
};

export const Empty: Story = {
  render: () => (
    <div className="w-96">
      <ArtifactList items={[]} />
    </div>
  ),
};
