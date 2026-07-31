import Link from "next/link";
import { Compass } from "lucide-react";

import { Button } from "@/components/ui/button";

export default function NotFound() {
  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col items-center justify-center gap-5 p-8 text-center">
      <div className="bg-muted text-muted-foreground grid size-12 place-items-center rounded-full">
        <Compass className="size-5" aria-hidden />
      </div>
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Not found</h1>
        <p className="text-muted-foreground text-sm">
          The page you&apos;re looking for doesn&apos;t exist, or has moved.
        </p>
      </div>
      <div className="flex gap-2">
        <Button asChild>
          <Link href="/projects">Back to Projects</Link>
        </Button>
        <Button variant="outline" asChild>
          <Link href="/">Home</Link>
        </Button>
      </div>
    </main>
  );
}
