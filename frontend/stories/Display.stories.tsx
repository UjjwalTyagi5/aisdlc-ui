import type { Meta, StoryObj } from "@storybook/react";
import { CheckCircle2, Info, TriangleAlert, XCircle } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Progress } from "@/components/ui/progress";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { Kbd } from "@/components/ui/kbd";

const meta: Meta = { title: "Primitives/Display", tags: ["autodocs"] };
export default meta;

export const AlertStory: StoryObj = {
  name: "Alert",
  render: () => (
    <div className="grid w-[520px] gap-3">
      <Alert>
        <Info />
        <AlertTitle>Heads up</AlertTitle>
        <AlertDescription>Your agent finished with warnings.</AlertDescription>
      </Alert>
      <Alert variant="success">
        <CheckCircle2 />
        <AlertTitle>Approved</AlertTitle>
        <AlertDescription>The Requirements phase was approved.</AlertDescription>
      </Alert>
      <Alert variant="warning">
        <TriangleAlert />
        <AlertTitle>Budget near cap</AlertTitle>
        <AlertDescription>This project is at 90% of the monthly budget.</AlertDescription>
      </Alert>
      <Alert variant="destructive">
        <XCircle />
        <AlertTitle>Run failed</AlertTitle>
        <AlertDescription>Step 3 exceeded the tool-call budget.</AlertDescription>
      </Alert>
    </div>
  ),
};

export const BadgeStory: StoryObj = {
  name: "Badge",
  render: () => (
    <div className="flex flex-wrap gap-2">
      <Badge>Default</Badge>
      <Badge variant="secondary">Secondary</Badge>
      <Badge variant="outline">Outline</Badge>
      <Badge variant="destructive">Destructive</Badge>
      <Badge variant="success">Success</Badge>
      <Badge variant="warning">Warning</Badge>
      <Badge variant="info">Info</Badge>
    </div>
  ),
};

export const AvatarStory: StoryObj = {
  name: "Avatar",
  render: () => (
    <div className="flex items-center gap-3">
      <Avatar>
        <AvatarImage src="https://github.com/vercel.png" alt="V" />
        <AvatarFallback>V</AvatarFallback>
      </Avatar>
      <Avatar>
        <AvatarFallback>AL</AvatarFallback>
      </Avatar>
      <Avatar className="size-12">
        <AvatarFallback>JD</AvatarFallback>
      </Avatar>
    </div>
  ),
};

export const SeparatorStory: StoryObj = {
  name: "Separator",
  render: () => (
    <div className="flex items-center gap-4 text-sm">
      <span>Artifact</span>
      <Separator orientation="vertical" className="h-4" />
      <span>Run</span>
      <Separator orientation="vertical" className="h-4" />
      <span>Cost</span>
    </div>
  ),
};

export const SkeletonStory: StoryObj = {
  name: "Skeleton",
  render: () => (
    <div className="w-80 space-y-2">
      <Skeleton className="h-4 w-1/3" />
      <Skeleton className="h-3 w-full" />
      <Skeleton className="h-3 w-5/6" />
    </div>
  ),
};

export const ProgressStory: StoryObj = {
  name: "Progress",
  render: () => (
    <div className="w-80 space-y-3">
      <Progress value={30} />
      <Progress value={66} />
      <Progress value={92} />
    </div>
  ),
};

export const CardStory: StoryObj = {
  name: "Card",
  render: () => (
    <Card className="w-80">
      <CardHeader>
        <CardTitle>Login page</CardTitle>
        <CardDescription>Ships in Chunk 4 with Auth0 wiring.</CardDescription>
      </CardHeader>
      <CardContent>Planned.</CardContent>
    </Card>
  ),
};

export const ScrollAreaStory: StoryObj = {
  name: "ScrollArea",
  render: () => (
    <ScrollArea className="h-48 w-64 rounded-md border p-4">
      {Array.from({ length: 20 }).map((_, i) => (
        <div key={i} className="py-1 text-sm">
          Tag #{i + 1}
        </div>
      ))}
    </ScrollArea>
  ),
};

export const TabsStory: StoryObj = {
  name: "Tabs",
  render: () => (
    <Tabs defaultValue="general" className="w-80">
      <TabsList>
        <TabsTrigger value="general">General</TabsTrigger>
        <TabsTrigger value="model">Model</TabsTrigger>
        <TabsTrigger value="policies">Policies</TabsTrigger>
      </TabsList>
      <TabsContent value="general" className="text-sm">
        Project name, description, owner.
      </TabsContent>
      <TabsContent value="model" className="text-sm">
        Per-agent model routing.
      </TabsContent>
      <TabsContent value="policies" className="text-sm">
        Approval gates, branch protection.
      </TabsContent>
    </Tabs>
  ),
};

export const BreadcrumbStory: StoryObj = {
  name: "Breadcrumb",
  render: () => (
    <Breadcrumb>
      <BreadcrumbList>
        <BreadcrumbItem>
          <BreadcrumbLink href="/">Projects</BreadcrumbLink>
        </BreadcrumbItem>
        <BreadcrumbSeparator />
        <BreadcrumbItem>
          <BreadcrumbLink href="/projects/ingest">Ingest</BreadcrumbLink>
        </BreadcrumbItem>
        <BreadcrumbSeparator />
        <BreadcrumbItem>
          <BreadcrumbPage>Requirements</BreadcrumbPage>
        </BreadcrumbItem>
      </BreadcrumbList>
    </Breadcrumb>
  ),
};

export const KbdStory: StoryObj = {
  name: "Kbd",
  render: () => (
    <div className="text-sm">
      Open palette: <Kbd>⌘</Kbd> <Kbd>K</Kbd>
    </div>
  ),
};
