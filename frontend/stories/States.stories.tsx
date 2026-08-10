import type { Meta, StoryObj } from "@storybook/react";
import { FolderPlus, FolderSearch } from "lucide-react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";

const meta: Meta = { title: "App/States", tags: ["autodocs"] };
export default meta;

export const EmptyProjects: StoryObj = {
  name: "Empty — projects",
  render: () => (
    <div className="w-[560px]">
      <EmptyState
        icon={FolderPlus}
        title="No projects yet"
        description="Create a project to start running agents on your own repos."
        action={<Button>Create project</Button>}
        secondaryAction={<Button variant="outline">Browse templates</Button>}
      />
    </div>
  ),
};

export const EmptyNoResults: StoryObj = {
  name: "Empty — no search results",
  render: () => (
    <div className="w-[560px]">
      <EmptyState
        icon={FolderSearch}
        title="No runs match"
        description="Try clearing filters or widening the date range."
        variant="plain"
      />
    </div>
  ),
};

export const Error: StoryObj = {
  name: "Error with retry",
  render: () => (
    <div className="w-[560px]">
      <ErrorState
        title="Couldn't load runs"
        description="The API returned 503. Your data is safe — this is a transient failure."
        detail={`GET /api/runs → 503 Service Unavailable\nrequestId: req_01HZ2J6Y...`}
        onRetry={() => {}}
      />
    </div>
  ),
};

export const LoadingList: StoryObj = {
  name: "Loading — list",
  render: () => (
    <div className="w-[560px]">
      <LoadingState variant="list" rows={4} />
    </div>
  ),
};

export const LoadingCard: StoryObj = {
  name: "Loading — card",
  render: () => (
    <div className="w-[560px]">
      <LoadingState variant="card" />
    </div>
  ),
};

export const LoadingTable: StoryObj = {
  name: "Loading — table",
  render: () => (
    <div className="w-[720px]">
      <LoadingState variant="table" rows={6} />
    </div>
  ),
};
