import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

/**
 * Transcripts live on the server now (Phase 5B). The store keeps only UI state —
 * which chat is selected, and the unsaved draft — so there is no second copy of a
 * conversation to drift from the first.
 *
 * localStorage sessions are NOT migrated. They are per-browser, hold no run id for
 * turns that were never sent, and cannot be attributed to a user server-side.
 * Migrating a shape that can no longer be produced is the mistake ruling R13 already
 * recorded, when stale gate messages were fixed by stripping the reader rather than
 * rewriting data nothing could write again.
 */
describe("the orchestrator store", () => {
  const src = readFileSync("stores/orchestrator-store.ts", "utf8");

  it("does not persist message transcripts", () => {
    expect(src).not.toContain("appendMessage");
    expect(src).not.toContain("patchMessage");
  });

  it("does not persist anything to localStorage", () => {
    expect(src).not.toContain("persist(");
    expect(src).not.toContain("zustand/middleware");
  });

  it("still tracks the draft and the selection, which are UI state", () => {
    // The other half: stripping too much would leave the rail unable to show an
    // unsaved chat at all, and "New" would appear to do nothing.
    expect(src).toContain("activeSessionId");
    expect(src).toContain("createSession");
  });
});
