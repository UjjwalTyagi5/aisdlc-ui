"use client";

import * as React from "react";
import {
  ChevronDown,
  ChevronRight,
  FileCode,
  FileJson,
  FileText,
  File as FileIcon,
  FileType,
  Folder,
  FolderOpen,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";

interface TreeNode {
  name: string;
  path: string; // full repo-relative path
  isDir: boolean;
  children: TreeNode[];
}

function buildTree(paths: readonly string[]): TreeNode {
  const root: TreeNode = { name: "", path: "", isDir: true, children: [] };
  for (const p of paths) {
    const parts = p.split("/").filter(Boolean);
    let cursor = root;
    parts.forEach((part, i) => {
      const isLeaf = i === parts.length - 1;
      const full = parts.slice(0, i + 1).join("/");
      let next = cursor.children.find((c) => c.name === part);
      if (!next) {
        next = { name: part, path: full, isDir: !isLeaf, children: [] };
        cursor.children.push(next);
      }
      cursor = next;
    });
  }
  const sort = (n: TreeNode) => {
    n.children.sort((a, b) => {
      if (a.isDir !== b.isDir) return a.isDir ? -1 : 1; // folders first
      return a.name.localeCompare(b.name);
    });
    n.children.forEach(sort);
  };
  sort(root);
  return root;
}

const EXT_ICON: Record<string, LucideIcon> = {
  ts: FileCode, tsx: FileCode, js: FileCode, jsx: FileCode, py: FileCode,
  go: FileCode, rs: FileCode, java: FileCode, rb: FileCode, php: FileCode,
  c: FileCode, cpp: FileCode, cs: FileCode, sh: FileCode, css: FileCode,
  scss: FileCode, html: FileCode, vue: FileCode, svelte: FileCode,
  json: FileJson, yaml: FileType, yml: FileType, toml: FileType, xml: FileType,
  md: FileText, txt: FileText, rst: FileText, env: FileType,
};

function fileIcon(name: string): LucideIcon {
  const ext = name.split(".").pop()?.toLowerCase();
  return (ext && EXT_ICON[ext]) || FileIcon;
}

export type ChangeStatus = "modified" | "added" | "deleted" | "renamed" | "copied";

const STATUS_LETTER: Record<ChangeStatus, string> = {
  modified: "M", added: "A", deleted: "D", renamed: "R", copied: "C",
};
const STATUS_TONE: Record<ChangeStatus, string> = {
  modified: "text-amber-600 dark:text-amber-400",
  added: "text-emerald-600 dark:text-emerald-400",
  deleted: "text-red-600 dark:text-red-400",
  renamed: "text-sky-600 dark:text-sky-400",
  copied: "text-sky-600 dark:text-sky-400",
};

function Row({
  node,
  depth,
  expanded,
  toggle,
  selectedPath,
  onSelect,
  changes,
  dirtyDirs,
}: {
  node: TreeNode;
  depth: number;
  expanded: Set<string>;
  toggle: (path: string) => void;
  selectedPath?: string;
  onSelect: (path: string) => void;
  changes?: ReadonlyMap<string, ChangeStatus>;
  dirtyDirs: ReadonlySet<string>;
}) {
  const isOpen = expanded.has(node.path);
  const isSelected = !node.isDir && node.path === selectedPath;
  const Icon = node.isDir ? (isOpen ? FolderOpen : Folder) : fileIcon(node.name);

  const status = !node.isDir ? changes?.get(node.path) : undefined;
  const dirDirty = node.isDir && dirtyDirs.has(node.path);
  const tone = status ? STATUS_TONE[status] : dirDirty ? STATUS_TONE.modified : undefined;

  return (
    <li>
      <button
        type="button"
        onClick={() => (node.isDir ? toggle(node.path) : onSelect(node.path))}
        aria-expanded={node.isDir ? isOpen : undefined}
        aria-current={isSelected ? "true" : undefined}
        title={node.path}
        style={{ paddingLeft: 6 + depth * 12 }}
        className={cn(
          "flex w-full items-center gap-1.5 rounded-sm py-[3px] pr-2 text-left text-[13px] leading-tight transition-colors",
          "hover:bg-accent focus-visible:ring-ring focus-visible:outline-none focus-visible:ring-1",
          isSelected && "bg-accent text-foreground font-medium",
        )}
      >
        {node.isDir ? (
          isOpen ? (
            <ChevronDown className="text-muted-foreground size-3.5 shrink-0" aria-hidden />
          ) : (
            <ChevronRight className="text-muted-foreground size-3.5 shrink-0" aria-hidden />
          )
        ) : (
          <span className="w-3.5 shrink-0" aria-hidden />
        )}
        <Icon
          className={cn(
            "size-3.5 shrink-0",
            node.isDir ? "text-primary/80" : "text-muted-foreground",
          )}
          aria-hidden
        />
        <span className={cn("truncate", tone, status === "deleted" && "line-through")}>
          {node.name}
        </span>
        {status && (
          <span className={cn("ml-auto shrink-0 pl-1 font-mono text-[11px] font-semibold", STATUS_TONE[status])}>
            {STATUS_LETTER[status]}
          </span>
        )}
      </button>
      {node.isDir && isOpen && node.children.length > 0 && (
        <ul>
          {node.children.map((c) => (
            <Row
              key={c.path}
              node={c}
              depth={depth + 1}
              expanded={expanded}
              toggle={toggle}
              selectedPath={selectedPath}
              onSelect={onSelect}
              changes={changes}
              dirtyDirs={dirtyDirs}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

export interface RepoFileTreeProps {
  paths: readonly string[];
  selectedPath?: string;
  onSelect: (path: string) => void;
  /** path → git status, for VS Code-style change decorations. */
  changes?: ReadonlyMap<string, ChangeStatus>;
  className?: string;
}

/**
 * VS Code-style file explorer for the pulled Development workspace. Builds a
 * collapsible folder/file tree from a flat path list; folders start collapsed
 * but the ancestors of the selected file auto-expand.
 */
export function RepoFileTree({
  paths,
  selectedPath,
  onSelect,
  changes,
  className,
}: RepoFileTreeProps) {
  const tree = React.useMemo(() => buildTree(paths), [paths]);

  // Ancestor directories of any changed file — so folders show a "dirty" tint.
  const dirtyDirs = React.useMemo(() => {
    const dirs = new Set<string>();
    if (changes) {
      for (const p of changes.keys()) {
        const parts = p.split("/").filter(Boolean);
        for (let i = 1; i < parts.length; i++) dirs.add(parts.slice(0, i).join("/"));
      }
    }
    return dirs;
  }, [changes]);

  const [expanded, setExpanded] = React.useState<Set<string>>(new Set());
  const toggle = React.useCallback((path: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  // Auto-expand the folders leading to the selected file (deep-link friendly).
  React.useEffect(() => {
    if (!selectedPath) return;
    const parts = selectedPath.split("/").filter(Boolean);
    if (parts.length < 2) return;
    setExpanded((prev) => {
      const next = new Set(prev);
      for (let i = 1; i < parts.length; i++) next.add(parts.slice(0, i).join("/"));
      return next;
    });
  }, [selectedPath]);

  if (paths.length === 0) {
    return (
      <p className="text-muted-foreground p-3 text-xs">This repository has no files to show.</p>
    );
  }

  return (
    <ul className={cn("select-none py-1", className)} role="tree" aria-label="Repository files">
      {tree.children.map((c) => (
        <Row
          key={c.path}
          node={c}
          depth={0}
          expanded={expanded}
          toggle={toggle}
          selectedPath={selectedPath}
          onSelect={onSelect}
          changes={changes}
          dirtyDirs={dirtyDirs}
        />
      ))}
    </ul>
  );
}
