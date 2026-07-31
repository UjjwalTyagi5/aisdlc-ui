"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { FolderPlus, Moon, Settings, Sparkles, Sun, type LucideIcon } from "lucide-react";
import { useTheme } from "next-themes";

import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
  CommandShortcut,
} from "@/components/ui/command";
import { Kbd } from "@/components/ui/kbd";
import { useUiStore } from "@/stores/ui-store";
import { useRawSession } from "@/components/auth/session-provider";
import { useAccessScope } from "@/hooks/use-access-scope";
import { can } from "@/lib/auth/capabilities";
import { visibleNav } from "@/lib/nav";
import type { Capability } from "@/lib/auth/types";
import { BUSINESS_UNIT_LABEL } from "@/lib/scope";

interface CommandEntry {
  id: string;
  label: string;
  group: "Navigate" | "Actions" | "Theme";
  icon: LucideIcon;
  keywords?: string;
  shortcut?: string[];
  /** Hide this entry unless the current role has this capability. */
  requireCapability?: Capability;
  run: (ctx: CommandContext) => void;
}

interface CommandContext {
  router: ReturnType<typeof useRouter>;
  setTheme: (t: "light" | "dark" | "system") => void;
  toggleSidebar: () => void;
  close: () => void;
}

/**
 * The Navigate group is DERIVED from `visibleNav()` below, not listed here.
 *
 * It used to be six hardcoded entries gated on `can(session.role, …)` — the
 * coarse admin/member/viewer capability model — which made the palette an
 * unfiltered back door: ⌘K → "Go to Audit log" routed a viewer straight past
 * the permission that hides that page in the sidebar, and the two lists drifted
 * (the palette never learned about Approvals, Users, Business Units, Cost or
 * Activity at all). One source of truth for "which pages may this person open"
 * is the only way those stay in agreement.
 *
 * Actions and Theme stay declarative here: they are commands, not destinations,
 * and have no nav entry to derive from.
 */
const COMMANDS: CommandEntry[] = [
  {
    id: "nav.onboarding",
    label: `Start ${BUSINESS_UNIT_LABEL.toLowerCase()} onboarding`,
    group: "Navigate",
    icon: Sparkles,
    keywords: `onboarding setup wizard new ${BUSINESS_UNIT_LABEL.toLowerCase()} workspace`,
    requireCapability: "user:invite",
    run: ({ router, close }) => {
      router.push("/onboarding");
      close();
    },
  },
  {
    id: "action.new-project",
    label: "Create new project…",
    group: "Actions",
    icon: FolderPlus,
    keywords: "new project create",
    shortcut: ["N"],
    requireCapability: "project:create",
    run: ({ router, close }) => {
      router.push("/projects?new=1");
      close();
    },
  },
  {
    id: "action.toggle-sidebar",
    label: "Toggle sidebar",
    group: "Actions",
    icon: Settings,
    keywords: "collapse expand sidebar",
    shortcut: ["⌘", "B"],
    run: ({ toggleSidebar, close }) => {
      toggleSidebar();
      close();
    },
  },
  {
    id: "theme.light",
    label: "Theme: light",
    group: "Theme",
    icon: Sun,
    keywords: "light mode theme",
    run: ({ setTheme, close }) => {
      setTheme("light");
      close();
    },
  },
  {
    id: "theme.dark",
    label: "Theme: dark",
    group: "Theme",
    icon: Moon,
    keywords: "dark mode theme",
    run: ({ setTheme, close }) => {
      setTheme("dark");
      close();
    },
  },
  {
    id: "theme.system",
    label: "Theme: match system",
    group: "Theme",
    icon: Sun,
    keywords: "system theme auto",
    run: ({ setTheme, close }) => {
      setTheme("system");
      close();
    },
  },
];

export function CommandPalette() {
  const router = useRouter();
  const { setTheme } = useTheme();
  const open = useUiStore((s) => s.commandPaletteOpen);
  const setOpen = useUiStore((s) => s.setCommandPaletteOpen);
  const toggleSidebar = useUiStore((s) => s.toggleSidebar);

  // Global keyboard shortcut: ⌘K / Ctrl+K
  React.useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.key === "k" || e.key === "K") && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpen(!open);
      }
      if ((e.key === "b" || e.key === "B") && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        toggleSidebar();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, setOpen, toggleSidebar]);

  const ctx: CommandContext = {
    router,
    setTheme,
    toggleSidebar,
    close: () => setOpen(false),
  };

  const session = useRawSession();
  const { role, isOrgWide, managedBusinessUnitIds, scope } = useAccessScope();

  // Destinations come from the same filter the sidebar uses, so a page hidden
  // there can't be reached by typing its name here.
  const navCommands = React.useMemo<CommandEntry[]>(
    () =>
      visibleNav(session?.permissions ?? [], {
        role,
        isOrgWide: scope === null ? undefined : isOrgWide,
        managedBusinessUnitIds: scope === null ? undefined : managedBusinessUnitIds,
      }).map((item) => ({
        id: `nav.${item.segment}`,
        label: `Go to ${item.label}`,
        group: "Navigate" as const,
        icon: item.icon,
        keywords: `${item.label} ${item.segment}`.toLowerCase(),
        run: ({ router, close }: CommandContext) => {
          router.push(item.href);
          close();
        },
      })),
    [session?.permissions, role, isOrgWide, managedBusinessUnitIds, scope],
  );

  const grouped = React.useMemo(() => {
    const g: Record<string, CommandEntry[]> = {};
    for (const c of [...navCommands, ...COMMANDS]) {
      if (c.requireCapability && (!session || !can(session.role, c.requireCapability))) {
        continue;
      }
      (g[c.group] ??= []).push(c);
    }
    return g;
  }, [session, navCommands]);

  return (
    <CommandDialog open={open} onOpenChange={setOpen}>
      {/* Elevated palette: border-line-soft surface with editorial group labels */}
      <div className="border-line-soft flex items-center border-b px-3">
        <CommandInput
          placeholder="Type a command or search…"
          aria-label="Command palette search"
          className="font-sans"
        />
        {/* ⌘K mono hint in the search bar — northstar .search .kbd spec */}
        <span className="ml-2 flex shrink-0 items-center gap-1 font-mono text-[10.5px] text-muted-foreground">
          <Kbd>⌘</Kbd>
          <Kbd>K</Kbd>
        </span>
      </div>
      <CommandList>
        <CommandEmpty className="font-mono text-xs text-muted-foreground">
          No results found.
        </CommandEmpty>
        {Object.entries(grouped).map(([group, items], gi) => (
          <React.Fragment key={group}>
            {gi > 0 && <CommandSeparator />}
            <CommandGroup
              heading={
                <span className="font-mono text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
                  {group}
                </span>
              }
            >
              {items.map((cmd) => (
                <CommandItem
                  key={cmd.id}
                  value={`${cmd.label} ${cmd.keywords ?? ""}`}
                  onSelect={() => cmd.run(ctx)}
                  className="gap-3"
                >
                  <cmd.icon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                  <span className="font-medium">{cmd.label}</span>
                  {cmd.shortcut && (
                    <CommandShortcut>
                      {cmd.shortcut.map((k, i) => (
                        <Kbd key={i}>{k}</Kbd>
                      ))}
                    </CommandShortcut>
                  )}
                </CommandItem>
              ))}
            </CommandGroup>
          </React.Fragment>
        ))}
      </CommandList>
    </CommandDialog>
  );
}
