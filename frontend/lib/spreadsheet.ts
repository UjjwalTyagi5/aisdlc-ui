/**
 * Read a spreadsheet in the browser, with no dependency.
 *
 * WHY NOT A LIBRARY. An `.xlsx` is a ZIP of XML, and both halves of that are
 * already in the platform: `DecompressionStream("deflate-raw")` inflates the
 * entries and `DOMParser` reads them. The usual package for this ships about a
 * megabyte, carries unpatched advisories on npm, and would be pulled into a
 * bundle for one admin screen's file input. Two hundred lines that do exactly
 * what we need, and nothing else, is the cheaper thing to own.
 *
 * WHAT IT DELIBERATELY DOES NOT DO: formulas (the cached value is read, which
 * is what a person exporting a roster wants), styles, dates as anything but
 * their raw serial, multiple sheets (the first is the roster), or files large
 * enough to matter. It reads a table of text out of a file someone exported
 * from Excel, which is the whole job.
 */

/** A parsed sheet: rows of cell text, ragged, with empty trailing cells cut. */
export type SheetRows = string[][];

export class SpreadsheetError extends Error {}

/**
 * Parse a `.xlsx` or `.csv` file into rows of text.
 *
 * The format is decided by the file's first bytes rather than its name: a ZIP
 * always starts `PK\x03\x04`, and a file renamed `.csv` by a well-meaning
 * exporter is still a ZIP. Guessing from the extension is how you hand a CSV
 * parser a binary blob and report "no rows found".
 */
export async function parseSpreadsheet(file: File): Promise<SheetRows> {
  const buffer = await file.arrayBuffer();
  const head = new Uint8Array(buffer.slice(0, 4));
  const isZip = head[0] === 0x50 && head[1] === 0x4b && head[2] === 0x03 && head[3] === 0x04;
  return isZip ? parseXlsx(buffer) : parseCsv(new TextDecoder().decode(buffer));
}

// ─── CSV ──────────────────────────────────────────────────────────────────────

/**
 * RFC-4180-ish: quoted fields may contain commas, newlines and doubled quotes.
 *
 * Hand-written rather than split(",") because a roster's most likely quoted
 * field is a person's name — "Reyes, Marcus" — and splitting on commas turns
 * that into two columns and shifts every field after it.
 */
export function parseCsv(text: string): SheetRows {
  const rows: SheetRows = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;

  // A BOM survives Excel's "CSV UTF-8" export and would otherwise become part
  // of the first header, so "email" never matches.
  const src = text.charCodeAt(0) === 0xfeff ? text.slice(1) : text;

  for (let i = 0; i < src.length; i++) {
    const c = src[i]!;
    if (quoted) {
      if (c === '"') {
        if (src[i + 1] === '"') {
          field += '"';
          i++;
        } else quoted = false;
      } else field += c;
      continue;
    }
    if (c === '"') quoted = true;
    else if (c === ",") {
      row.push(field);
      field = "";
    } else if (c === "\n" || c === "\r") {
      // Swallow the \n of a \r\n pair rather than emitting a blank row.
      if (c === "\r" && src[i + 1] === "\n") i++;
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else field += c;
  }
  if (field !== "" || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  return rows.map((r) => r.map((cell) => cell.trim())).filter((r) => r.some((cell) => cell !== ""));
}

// ─── XLSX ─────────────────────────────────────────────────────────────────────

interface ZipEntry {
  name: string;
  /** 0 = stored, 8 = deflate. Anything else we refuse rather than guess. */
  method: number;
  offset: number;
  compressedSize: number;
}

/**
 * The ZIP central directory, read from the end of the file.
 *
 * Read from the CENTRAL directory rather than by walking local headers,
 * because a local header is allowed to carry zero sizes and defer them to a
 * trailing data descriptor. Some writers do exactly that, and a walker that
 * trusted the local sizes would read zero bytes and report an empty sheet.
 */
function readZipEntries(buffer: ArrayBuffer): Map<string, ZipEntry> {
  const view = new DataView(buffer);
  const bytes = new Uint8Array(buffer);

  // End-of-central-directory record: fixed 22 bytes plus a comment of up to
  // 65535, so it lives somewhere in the last 65557.
  let eocd = -1;
  const from = Math.max(0, bytes.length - 65_557);
  for (let i = bytes.length - 22; i >= from; i--) {
    if (view.getUint32(i, true) === 0x0605_4b50) {
      eocd = i;
      break;
    }
  }
  if (eocd === -1) throw new SpreadsheetError("That file isn't a readable .xlsx.");

  const count = view.getUint16(eocd + 10, true);
  let p = view.getUint32(eocd + 16, true);

  const entries = new Map<string, ZipEntry>();
  for (let i = 0; i < count; i++) {
    if (view.getUint32(p, true) !== 0x0201_4b50) break;
    const method = view.getUint16(p + 10, true);
    const compressedSize = view.getUint32(p + 20, true);
    const nameLen = view.getUint16(p + 28, true);
    const extraLen = view.getUint16(p + 30, true);
    const commentLen = view.getUint16(p + 32, true);
    const offset = view.getUint32(p + 42, true);
    const name = new TextDecoder().decode(bytes.subarray(p + 46, p + 46 + nameLen));
    entries.set(name, { name, method, offset, compressedSize });
    p += 46 + nameLen + extraLen + commentLen;
  }
  return entries;
}

async function readEntry(buffer: ArrayBuffer, entry: ZipEntry): Promise<string> {
  const view = new DataView(buffer);
  // The local header repeats the name and extra lengths, and only they say
  // where this entry's bytes actually start.
  const nameLen = view.getUint16(entry.offset + 26, true);
  const extraLen = view.getUint16(entry.offset + 28, true);
  const start = entry.offset + 30 + nameLen + extraLen;
  const raw = buffer.slice(start, start + entry.compressedSize);

  if (entry.method === 0) return new TextDecoder().decode(raw);
  if (entry.method !== 8) {
    throw new SpreadsheetError("That .xlsx uses a compression this reader doesn't support.");
  }
  const stream = new Blob([raw]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
  return new Response(stream).text();
}

/** "BC12" → 54. Column letters are base-26 with no zero. */
function columnIndex(ref: string): number {
  let n = 0;
  for (const ch of ref) {
    const code = ch.charCodeAt(0);
    if (code < 65 || code > 90) break;
    n = n * 26 + (code - 64);
  }
  return n - 1;
}

function textOf(node: Element | null): string {
  return node?.textContent ?? "";
}

async function parseXlsx(buffer: ArrayBuffer): Promise<SheetRows> {
  if (typeof DecompressionStream === "undefined") {
    throw new SpreadsheetError(
      "This browser can't read .xlsx files. Save the sheet as CSV and upload that.",
    );
  }

  const entries = readZipEntries(buffer);
  // Excel numbers its sheets from 1; the roster is the first one. Looking it up
  // through workbook.xml's relationships would be more correct and buys nothing
  // for a file a person exported ten seconds ago.
  const sheetEntry =
    entries.get("xl/worksheets/sheet1.xml") ??
    [...entries.values()].find((e) => e.name.startsWith("xl/worksheets/sheet"));
  if (!sheetEntry) throw new SpreadsheetError("That .xlsx has no worksheet in it.");

  const parser = new DOMParser();

  // Shared strings are xlsx's string table — most text cells are an INDEX into
  // it (`t="s"`), so without this every name reads as a number.
  const sharedEntry = entries.get("xl/sharedStrings.xml");
  const shared: string[] = [];
  if (sharedEntry) {
    const doc = parser.parseFromString(await readEntry(buffer, sharedEntry), "application/xml");
    for (const si of Array.from(doc.getElementsByTagName("si"))) {
      // A run-formatted string is several <t> nodes; joining them is what keeps
      // "Marcus Reyes" from becoming "Marcus".
      shared.push(
        Array.from(si.getElementsByTagName("t"))
          .map((t) => t.textContent ?? "")
          .join(""),
      );
    }
  }

  const doc = parser.parseFromString(await readEntry(buffer, sheetEntry), "application/xml");
  const rows: SheetRows = [];

  for (const rowEl of Array.from(doc.getElementsByTagName("row"))) {
    const cells: string[] = [];
    for (const c of Array.from(rowEl.getElementsByTagName("c"))) {
      // Cells are sparse: an empty one is simply absent, so the reference is
      // the only thing that says which column this value belongs to.
      const idx = columnIndex(c.getAttribute("r") ?? "");
      const type = c.getAttribute("t");
      let value: string;
      if (type === "s") {
        value = shared[Number(textOf(c.querySelector("v")))] ?? "";
      } else if (type === "inlineStr") {
        value = Array.from(c.getElementsByTagName("t"))
          .map((t) => t.textContent ?? "")
          .join("");
      } else {
        value = textOf(c.querySelector("v"));
      }
      if (idx >= 0) {
        while (cells.length < idx) cells.push("");
        cells[idx] = value.trim();
      }
    }
    if (cells.some((v) => v !== "")) rows.push(cells);
  }
  return rows;
}
