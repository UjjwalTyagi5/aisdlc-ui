import type { Meta, StoryObj } from "@storybook/react";
import { ArrowRight, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";

const meta: Meta<typeof Button> = {
  title: "Primitives/Button",
  component: Button,
  tags: ["autodocs"],
};

export default meta;
type Story = StoryObj<typeof Button>;

export const Default: Story = { args: { children: "Button" } };
export const Secondary: Story = { args: { children: "Secondary", variant: "secondary" } };
export const Outline: Story = { args: { children: "Outline", variant: "outline" } };
export const Ghost: Story = { args: { children: "Ghost", variant: "ghost" } };
export const LinkStyle: Story = { args: { children: "Link", variant: "link" } };
export const Destructive: Story = { args: { children: "Delete", variant: "destructive" } };
export const Disabled: Story = { args: { children: "Disabled", disabled: true } };

export const Sizes: Story = {
  render: () => (
    <div className="flex items-center gap-3">
      <Button size="sm">Small</Button>
      <Button size="default">Default</Button>
      <Button size="lg">Large</Button>
      <Button size="icon" aria-label="Go">
        <ArrowRight />
      </Button>
    </div>
  ),
};

export const WithIcon: Story = {
  render: () => (
    <Button>
      <Loader2 className="animate-spin" />
      Running…
    </Button>
  ),
};
