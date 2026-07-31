"use client";

import * as React from "react";
import {
  ChevronDown,
  ChevronRight,
  File as FileIcon,
  FilePlus,
  FileText,
  FileX,
  Folder,
  FolderOpen,
} from "lucide-react";

import { cn } from "@/lib/utils";
import type { PrFile } from "@/lib/schemas";

export interface FileTreeProps {
  files: readonly PrFile[];
  selectedPath?: string;
  onSelect?: (file: PrFile) => void;
  className?: string;
}

interface Node {
  name: string;
  /** Full path from root — only set on leaves. */
  path?: string;
  file?: PrFile;
  children: Node[];
}

function buildTree(files: readonly PrFile[]): Node {
  const root: Node = { name: "", children: [] };
  for (const f of files) {
    const parts = f.path.split("/").filter(Boolean);
    let cursor = root;
    parts.forEach((part, i) => {
      const isLeaf = i === parts.length - 1;
      let next = cursor.children.find((c) => c.name === part);
      if (!next) {
        next = {
          name: part,
          children: [],
          path: isLeaf ? f.path : undefined,
          file: isLeaf ? f : undefined,
        };
        cursor.children.push(next);
      } else if (isLeaf) {
        next.path = f.path;
        next.file = f;
      }
      cursor = next;
    });
  }
  const sort = (n: Node) => {
    n.children.sort((a, b) => {
      const aLeaf = !!a.file;
      const bLeaf = !!b.file;
      if (aLeaf !== bLeaf) return aLeaf ? 1 : -1;
      return a.name.localeCompare(b.name);
    });
    for (const c of n.children) sort(c);
  };
  sort(root);
  return root;
}

const STATUS_GLYPH: Record<PrFile["status"], React.ComponentType<{ className?: string }>> = {
  added: FilePlus,
  removed: FileX,
  modified: FileText,
  renamed: FileIcon,
};

const STATUS_COLOR: Record<PrFile["status"], string> = {
  added: "text-success",
  removed: "text-destructive",
  modified: "text-info",
  renamed: "text-muted-foreground",
};

export function FileTree({ files, selectedPath, onSelect, className }: FileTreeProps) {
  const tree = React.useMemo(() => buildTree(files), [files]);

  return (
    <ul className={cn("select-none text-sm", className)} role="tree" aria-label="Changed files">
      {tree.children.map((node) => (
        <TreeNode
          key={node.name}
          node={node}
          depth={0}
          selectedPath={selectedPath}
          onSelect={onSelect}
        />
      ))}
    </ul>
  );
}

function TreeNode({
  node,
  depth,
  selectedPath,
  onSelect,
}: {
  node: Node;
  depth: number;
  selectedPath?: string;
  onSelect?: (file: PrFile) => void;
}) {
  const [open, setOpen] = React.useState(true);

  if (node.file) {
    const Icon = STATUS_GLYPH[node.file.status];
    const color = STATUS_COLOR[node.file.status];
    const active = selectedPath === node.file.path;
    return (
      <li role="treeitem" aria-selected={active}>
        <button
          type="button"
          onClick={() => onSelect?.(node.file!)}
          className={cn(
            "flex w-full items-center gap-2 rounded-md px-2 py-1 text-left transition-colors",
            "hover:bg-accent",
            "focus-visible:ring-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-1",
            active && "bg-accent text-accent-foreground",
          )}
          style={{ paddingLeft: 12 + depth * 12 }}
          title={node.file.path}
        >
          <Icon className={cn("size-3.5 shrink-0", color)} aria-hidden />
          <span className="truncate">{node.name}</span>
          <span className="text-muted-foreground ml-auto flex items-center gap-1 font-mono text-[10px]">
            <span className="text-success">+{node.file.additions}</span>
            <span className="text-destructive">−{node.file.deletions}</span>
          </span>
        </button>
      </li>
    );
  }

  return (
    <li role="treeitem" aria-expanded={open} aria-selected={false}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="hover:bg-accent flex w-full items-center gap-1.5 rounded-md px-2 py-1 text-left"
        style={{ paddingLeft: 12 + depth * 12 }}
      >
        {open ? (
          <ChevronDown className="text-muted-foreground size-3.5" aria-hidden />
        ) : (
          <ChevronRight className="text-muted-foreground size-3.5" aria-hidden />
        )}
        {open ? (
          <FolderOpen className="text-muted-foreground size-3.5" aria-hidden />
        ) : (
          <Folder className="text-muted-foreground size-3.5" aria-hidden />
        )}
        <span className="truncate">{node.name}</span>
      </button>
      {open && (
        <ul role="group">
          {node.children.map((c) => (
            <TreeNode
              key={c.name + (c.path ?? "")}
              node={c}
              depth={depth + 1}
              selectedPath={selectedPath}
              onSelect={onSelect}
            />
          ))}
        </ul>
      )}
    </li>
  );
}
