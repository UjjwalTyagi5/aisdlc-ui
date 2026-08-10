/**
 * Minimal LCS line diff — no dependencies. Used by Agent Studio's version
 * "View" to show what a saved version changes versus the currently active one.
 *
 * Returns a flat sequence of lines tagged added / removed / unchanged, in a
 * readable order (removed lines shown before the added lines that replace
 * them). Whitespace is preserved; an empty string diffs as a single empty line.
 */

export type DiffLineType = "add" | "remove" | "context";

export interface DiffLine {
  type: DiffLineType;
  /** Line content without the trailing newline. */
  text: string;
}

/** Split into lines, treating "" as one empty line (not zero lines). */
function toLines(value: string): string[] {
  return value.length === 0 ? [""] : value.split("\n");
}

/**
 * Longest-common-subsequence line diff of `before` → `after`.
 * O(n·m) table; inputs here are small (≤ a few KB of prompt text).
 */
export function diffLines(before: string, after: string): DiffLine[] {
  const a = toLines(before);
  const b = toLines(after);
  const n = a.length;
  const m = b.length;

  // lcs[i][j] = length of LCS of a[i:] and b[j:]
  const lcs: number[][] = Array.from({ length: n + 1 }, () =>
    new Array<number>(m + 1).fill(0),
  );
  for (let i = n - 1; i >= 0; i--) {
    const row = lcs[i]!;
    const rowBelow = lcs[i + 1]!;
    for (let j = m - 1; j >= 0; j--) {
      row[j] =
        a[i] === b[j]
          ? rowBelow[j + 1]! + 1
          : Math.max(rowBelow[j]!, row[j + 1]!);
    }
  }

  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      out.push({ type: "context", text: a[i]! });
      i++;
      j++;
    } else if (lcs[i + 1]![j]! >= lcs[i]![j + 1]!) {
      out.push({ type: "remove", text: a[i]! });
      i++;
    } else {
      out.push({ type: "add", text: b[j]! });
      j++;
    }
  }
  while (i < n) out.push({ type: "remove", text: a[i++]! });
  while (j < m) out.push({ type: "add", text: b[j++]! });

  return out;
}

/** True when the two strings differ (after normalizing to line arrays). */
export function hasLineChanges(before: string, after: string): boolean {
  return before !== after;
}
