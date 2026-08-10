/**
 * Client-side export helpers. Used by the Audit log and Runs pages —
 * everything happens in-browser so PII never hits a third-party service.
 */

function escapeCsvCell(v: unknown): string {
  if (v === null || v === undefined) return "";
  const s = typeof v === "string" ? v : JSON.stringify(v);
  if (/[,"\n\r]/.test(s)) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

export function toCsv<T extends Record<string, unknown>>(
  rows: readonly T[],
  columns: Array<{ key: keyof T; header: string; format?: (v: unknown) => unknown }>,
): string {
  const head = columns.map((c) => escapeCsvCell(c.header)).join(",");
  const body = rows.map((row) =>
    columns
      .map((col) => {
        const raw = row[col.key];
        const formatted = col.format ? col.format(raw) : raw;
        return escapeCsvCell(formatted);
      })
      .join(","),
  );
  return [head, ...body].join("\r\n");
}

export function downloadBlob(text: string, filename: string, mime: string) {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function downloadCsv<T extends Record<string, unknown>>(
  filename: string,
  rows: readonly T[],
  columns: Parameters<typeof toCsv<T>>[1],
) {
  downloadBlob(toCsv(rows, columns), filename, "text/csv;charset=utf-8");
}

export function downloadJson(filename: string, value: unknown) {
  downloadBlob(JSON.stringify(value, null, 2), filename, "application/json");
}
