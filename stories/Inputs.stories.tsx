import type { Meta, StoryObj } from "@storybook/react";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Switch } from "@/components/ui/switch";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Button } from "@/components/ui/button";

const meta: Meta = { title: "Primitives/Inputs", tags: ["autodocs"] };
export default meta;

export const InputStory: StoryObj = {
  name: "Input",
  render: () => (
    <div className="grid w-72 gap-2">
      <Label htmlFor="email">Email</Label>
      <Input id="email" type="email" placeholder="name@company.com" />
    </div>
  ),
};

export const TextareaStory: StoryObj = {
  name: "Textarea",
  render: () => (
    <div className="grid w-96 gap-2">
      <Label htmlFor="notes">Acceptance criteria</Label>
      <Textarea id="notes" placeholder="Given…\nWhen…\nThen…" rows={5} />
    </div>
  ),
};

export const CheckboxStory: StoryObj = {
  name: "Checkbox",
  render: () => (
    <div className="flex items-center gap-2">
      <Checkbox id="gate" defaultChecked />
      <Label htmlFor="gate">Require human approval</Label>
    </div>
  ),
};

export const SwitchStory: StoryObj = {
  name: "Switch",
  render: () => (
    <div className="flex items-center gap-2">
      <Switch id="dryrun" />
      <Label htmlFor="dryrun">Dry run only</Label>
    </div>
  ),
};

export const RadioStory: StoryObj = {
  name: "Radio group",
  render: () => (
    <RadioGroup defaultValue="sonnet" className="w-64">
      {(
        [
          ["opus", "Claude Opus 4.7 — critical"],
          ["sonnet", "Claude Sonnet 4.6 — balanced"],
          ["haiku", "Claude Haiku 4.5 — fast"],
        ] as const
      ).map(([v, l]) => (
        <div key={v} className="flex items-center gap-2">
          <RadioGroupItem id={v} value={v} />
          <Label htmlFor={v} className="font-normal">
            {l}
          </Label>
        </div>
      ))}
    </RadioGroup>
  ),
};

export const SelectStory: StoryObj = {
  name: "Select",
  render: () => (
    <div className="w-64">
      <Select>
        <SelectTrigger>
          <SelectValue placeholder="Pick a model" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="opus">Claude Opus 4.7</SelectItem>
          <SelectItem value="sonnet">Claude Sonnet 4.6</SelectItem>
          <SelectItem value="haiku">Claude Haiku 4.5</SelectItem>
          <SelectItem value="gemini">Gemini 2.5 Pro</SelectItem>
          <SelectItem value="deepseek">DeepSeek V3.1</SelectItem>
        </SelectContent>
      </Select>
    </div>
  ),
};

// RHF + Zod example — proves the Form primitive wires correctly
const schema = z.object({
  name: z.string().min(2, "Name must be at least 2 characters"),
  email: z.string().email("Enter a valid email"),
});

function FormDemo() {
  const form = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema),
    defaultValues: { name: "", email: "" },
  });
  return (
    <Form {...form}>
      <form
        onSubmit={form.handleSubmit((v) => alert(JSON.stringify(v)))}
        className="w-80 space-y-4"
      >
        <FormField
          control={form.control}
          name="name"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Name</FormLabel>
              <FormControl>
                <Input placeholder="Ada Lovelace" {...field} />
              </FormControl>
              <FormDescription>Shown on your profile.</FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="email"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Email</FormLabel>
              <FormControl>
                <Input type="email" placeholder="ada@work.com" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <Button type="submit">Submit</Button>
      </form>
    </Form>
  );
}

export const FormStory: StoryObj = {
  name: "Form (RHF + Zod)",
  render: () => <FormDemo />,
};
