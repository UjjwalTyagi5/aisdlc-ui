import type { Meta, StoryObj } from "@storybook/react";

import { OpenApiViewer } from "@/components/app/openapi-viewer";

const meta: Meta<typeof OpenApiViewer> = {
  title: "App/OpenApiViewer",
  component: OpenApiViewer,
  tags: ["autodocs"],
  parameters: { layout: "padded" },
};

export default meta;
type Story = StoryObj<typeof OpenApiViewer>;

const SPEC = `openapi: 3.1.0
info:
  title: Ingest API
  version: 1.2.0
  description: Idempotent replay endpoints for Acme's ingest pipeline.
servers:
  - url: https://api.acme.dev
    description: Production
  - url: https://api.staging.acme.dev
    description: Staging
paths:
  /batches:
    get:
      tags: [batches]
      summary: List batches
      parameters:
        - in: query
          name: status
          description: Filter by batch status
          schema: { type: string }
      responses:
        '200': { description: OK }
  /batches/{id}:
    get:
      tags: [batches]
      summary: Get a batch
      parameters:
        - in: path
          name: id
          required: true
          schema: { type: string }
      responses:
        '200': { description: OK }
        '404': { description: Not found }
  /batches/{id}/replay:
    post:
      tags: [batches]
      summary: Replay a failed batch
      parameters:
        - in: path
          name: id
          required: true
          schema: { type: string }
        - in: header
          name: Idempotency-Key
          required: true
          description: Prevents double-replay
          schema: { type: string }
      responses:
        '202': { description: Accepted }
        '409': { description: Already succeeded }
  /webhooks:
    post:
      tags: [webhooks]
      summary: Register a webhook
      responses:
        '201': { description: Created }
`;

export const Spec: Story = { args: { source: SPEC, className: "h-[520px]" } };

export const InvalidSpec: Story = {
  args: { source: "this: is: not: valid: yaml: {{}}", className: "h-40" },
};
