import type { Meta, StoryObj } from "@storybook/react";
import type { ColumnDef } from "@tanstack/react-table";

import { DataTable } from "@/components/ui/data-table";
import { StatusBadge, type StatusKey } from "@/components/ui/status-badge";
import { CostBadge } from "@/components/ui/cost-badge";

type Run = {
  id: string;
  project: string;
  agent: string;
  status: StatusKey;
  usd: number;
  tokens: number;
  duration: string;
};

const data: Run[] = [
  { id: "R-2141", project: "ingest", agent: "Requirements", status: "approved", usd: 0.12, tokens: 18_420, duration: "42s" },
  { id: "R-2142", project: "ingest", agent: "Design", status: "awaiting_approval", usd: 1.03, tokens: 83_100, duration: "2m 14s" },
  { id: "R-2143", project: "checkout", agent: "Development", status: "running", usd: 1.84, tokens: 102_000, duration: "1m 50s" },
  { id: "R-2144", project: "checkout", agent: "Review", status: "failed", usd: 0.65, tokens: 44_000, duration: "58s" },
  { id: "R-2145", project: "reporting", agent: "Testing", status: "approved", usd: 0.18, tokens: 32_800, duration: "36s" },
];

const columns: ColumnDef<Run>[] = [
  { accessorKey: "id", header: "Run" },
  { accessorKey: "project", header: "Project" },
  { accessorKey: "agent", header: "Agent" },
  {
    accessorKey: "status",
    header: "Status",
    cell: ({ row }) => <StatusBadge status={row.original.status} />,
  },
  {
    accessorKey: "usd",
    header: "Cost",
    cell: ({ row }) => (
      <CostBadge usd={row.original.usd} tokens={row.original.tokens} />
    ),
  },
  { accessorKey: "duration", header: "Duration" },
];

const meta: Meta<typeof DataTable<Run, unknown>> = {
  title: "Primitives/DataTable",
  component: DataTable,
  tags: ["autodocs"],
  parameters: { layout: "padded" },
};

export default meta;
type Story = StoryObj<typeof DataTable<Run, unknown>>;

export const RunsTable: Story = {
  args: {
    columns,
    data,
    enableSorting: true,
    enableFiltering: true,
  },
};

export const Empty: Story = {
  args: {
    columns,
    data: [],
    emptyMessage: "No runs yet.",
  },
};
