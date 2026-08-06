import { describe, expect, it } from "vitest";

import { parseCsv } from "@/lib/spreadsheet";

/**
 * The CSV half of the bulk-onboarding reader. The `.xlsx` half needs a real
 * ZIP and `DecompressionStream`, so it is exercised in the browser rather than
 * here; this covers the parsing decisions that actually bite on a roster
 * someone exported.
 */
describe("parseCsv", () => {
  it("keeps a comma inside a quoted name in one field", () => {
    // The failure this exists for: split(",") turns "Reyes, Marcus" into two
    // columns and shifts the role and business unit one place left, so every
    // row after the first quoted name imports as something else entirely.
    const rows = parseCsv('Email,Name\njane@x.com,"Reyes, Marcus"');
    expect(rows[1]).toEqual(["jane@x.com", "Reyes, Marcus"]);
  });

  it("reads a doubled quote as one literal quote", () => {
    expect(parseCsv('Name\n"She said ""hi"""')[1]).toEqual(['She said "hi"']);
  });

  it("strips the BOM Excel writes, so the first header still matches", () => {
    const rows = parseCsv("﻿Email,Role\njane@x.com,Contributor");
    expect(rows[0]![0]).toBe("Email");
  });

  it("treats CRLF as one row break", () => {
    expect(parseCsv("a,b\r\nc,d\r\n")).toEqual([
      ["a", "b"],
      ["c", "d"],
    ]);
  });

  it("drops blank rows rather than importing empty people", () => {
    expect(parseCsv("Email\njane@x.com\n\n\nsam@x.com\n")).toEqual([
      ["Email"],
      ["jane@x.com"],
      ["sam@x.com"],
    ]);
  });

  it("keeps a newline inside a quoted field", () => {
    const rows = parseCsv('Name,Note\n"Jane","line one\nline two"');
    expect(rows).toHaveLength(2);
    expect(rows[1]![1]).toBe("line one\nline two");
  });
});
