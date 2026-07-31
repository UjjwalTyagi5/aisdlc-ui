import type { Meta, StoryObj } from "@storybook/react";
import { Copy, Settings, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import {
  Drawer,
  DrawerContent,
  DrawerDescription,
  DrawerHeader,
  DrawerTitle,
  DrawerTrigger,
} from "@/components/ui/drawer";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command";
import { Kbd } from "@/components/ui/kbd";

const meta: Meta = { title: "Primitives/Overlays", tags: ["autodocs"] };
export default meta;

export const DialogStory: StoryObj = {
  name: "Dialog",
  render: () => (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="outline">Open dialog</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Archive project</DialogTitle>
          <DialogDescription>
            Archived projects become read-only. You can restore them within 30 days.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline">Cancel</Button>
          <Button variant="destructive">Archive</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  ),
};

export const SheetStory: StoryObj = {
  name: "Sheet (side drawer)",
  render: () => (
    <Sheet>
      <SheetTrigger asChild>
        <Button variant="outline">Open sheet</Button>
      </SheetTrigger>
      <SheetContent>
        <SheetHeader>
          <SheetTitle>Agent chat</SheetTitle>
          <SheetDescription>
            Context-aware copilot for the current page.
          </SheetDescription>
        </SheetHeader>
      </SheetContent>
    </Sheet>
  ),
};

export const DrawerStory: StoryObj = {
  name: "Drawer (bottom sheet)",
  render: () => (
    <Drawer>
      <DrawerTrigger asChild>
        <Button variant="outline">Open drawer</Button>
      </DrawerTrigger>
      <DrawerContent className="max-h-[80vh]">
        <DrawerHeader>
          <DrawerTitle>Run detail</DrawerTitle>
          <DrawerDescription>Step-by-step trace of the last run.</DrawerDescription>
        </DrawerHeader>
        <div className="p-4 text-sm">Content…</div>
      </DrawerContent>
    </Drawer>
  ),
};

export const PopoverStory: StoryObj = {
  name: "Popover",
  render: () => (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline">Open popover</Button>
      </PopoverTrigger>
      <PopoverContent>
        <p className="text-sm">Any content — filter form, color picker, quick info.</p>
      </PopoverContent>
    </Popover>
  ),
};

export const TooltipStory: StoryObj = {
  name: "Tooltip",
  render: () => (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button variant="outline">Hover me</Button>
      </TooltipTrigger>
      <TooltipContent>Tooltip content</TooltipContent>
    </Tooltip>
  ),
};

export const DropdownMenuStory: StoryObj = {
  name: "Dropdown menu",
  render: () => (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline">Actions</Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuLabel>Artifact</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem>
          <Copy /> Duplicate
          <DropdownMenuShortcut>
            <Kbd>⌘</Kbd>
            <Kbd>D</Kbd>
          </DropdownMenuShortcut>
        </DropdownMenuItem>
        <DropdownMenuItem>
          <Settings /> Settings
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem className="text-destructive focus:text-destructive">
          <Trash2 /> Delete
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  ),
};

export const ContextMenuStory: StoryObj = {
  name: "Context menu",
  render: () => (
    <ContextMenu>
      <ContextMenuTrigger className="border-border text-muted-foreground flex h-36 w-72 items-center justify-center rounded-md border border-dashed text-sm">
        Right-click here
      </ContextMenuTrigger>
      <ContextMenuContent className="w-48">
        <ContextMenuItem>Copy link</ContextMenuItem>
        <ContextMenuItem>Edit</ContextMenuItem>
        <ContextMenuItem className="text-destructive focus:text-destructive">Delete</ContextMenuItem>
      </ContextMenuContent>
    </ContextMenu>
  ),
};

export const CommandStory: StoryObj = {
  name: "Command (cmd+k)",
  render: () => (
    <Command className="w-80 rounded-md border shadow-md">
      <CommandInput placeholder="Type a command or search…" />
      <CommandList>
        <CommandEmpty>No results found.</CommandEmpty>
        <CommandGroup heading="Navigation">
          <CommandItem>Projects</CommandItem>
          <CommandItem>Integrations</CommandItem>
          <CommandItem>Audit log</CommandItem>
        </CommandGroup>
        <CommandSeparator />
        <CommandGroup heading="Actions">
          <CommandItem>New project</CommandItem>
          <CommandItem>Invite teammate</CommandItem>
        </CommandGroup>
      </CommandList>
    </Command>
  ),
};
