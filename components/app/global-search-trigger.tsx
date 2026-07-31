"use client";

import * as React from "react";
import { Search } from "lucide-react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import { useUiStore } from "@/stores/ui-store";

/**
 * Search-styled button in the top bar that opens the command palette.
 * The palette itself lives in <CommandPalette />.
 */
export function GlobalSearchTrigger({ className }: { className?: string }) {
  const open = useUiStore((s) => s.openCommandPalette);
  const [mac, setMac] = React.useState(false);

  React.useEffect(() => {
    if (typeof navigator !== "undefined") {
      setMac(/Mac|iPhone|iPad|iPod/.test(navigator.platform));
    }
  }, []);

  return (
    <Button
      variant="outline"
      onClick={open}
      className={cn(
        "text-muted-foreground hover:text-foreground h-9 w-full max-w-sm justify-start gap-2 px-3 font-normal",
        className,
      )}
      aria-label="Search (open command palette)"
      aria-keyshortcuts={mac ? "Meta+K" : "Control+K"}
    >
      <Search className="size-4" aria-hidden />
      <span className="flex-1 truncate text-left text-sm">Search or run a command…</span>
      <span className="hidden items-center gap-1 sm:flex" aria-hidden>
        <Kbd>{mac ? "⌘" : "Ctrl"}</Kbd>
        <Kbd>K</Kbd>
      </span>
    </Button>
  );
}
