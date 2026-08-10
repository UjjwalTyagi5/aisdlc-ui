import type { Meta, StoryObj } from "@storybook/react";

import { MermaidRenderer } from "@/components/app/mermaid-renderer";

const meta: Meta<typeof MermaidRenderer> = {
  title: "App/MermaidRenderer",
  component: MermaidRenderer,
  tags: ["autodocs"],
  parameters: { layout: "padded" },
};

export default meta;
type Story = StoryObj<typeof MermaidRenderer>;

const C4 = `graph LR
  subgraph External
    Client[Web / Mobile Client]
    Jira[(Jira Cloud)]
  end
  subgraph Platform
    Gateway[API Gateway]
    IngestAPI[Ingest API]
    Queue[[Queue]]
    Worker[Ingest Worker]
    DB[(Postgres)]
  end
  Client --> Gateway --> IngestAPI
  Jira -.webhook.-> IngestAPI
  IngestAPI --> Queue
  Queue --> Worker
  Worker --> DB`;

const FLOW = `sequenceDiagram
  participant U as User
  participant UI as Web UI
  participant API
  participant Orch as Orchestrator
  participant LLM as Claude
  U->>UI: Click "Run"
  UI->>API: POST /runs
  API->>Orch: enqueue
  Orch->>LLM: plan + stream
  LLM-->>UI: SSE tokens
  Orch-->>UI: artifact.updated`;

const BROKEN = `graph TD\n  !!! not valid`;

export const C4Diagram: Story = { args: { source: C4, height: 460 } };
export const SequenceDiagram: Story = { args: { source: FLOW, height: 460 } };
export const RenderError: Story = { args: { source: BROKEN, height: 200 } };
