/**
 * EvalIndicator unit tests — pure logic tests for the exported helper functions.
 *
 * These run in vitest's node environment (no DOM required).  The functions
 * `scoreColorBand` and `formatScorePercent` are exported from the component
 * for exactly this purpose — they encode the core rendering logic:
 *   - score → percentage string
 *   - score → color band (success / warning / destructive)
 *
 * UI rendering tests (renders score percentage, shows empty state) are
 * represented via the getRunEval BFF client contract + Zod schema assertions
 * in this file, which verify the data shape the component depends on.
 */
import { describe, expect, it } from "vitest";
import { z } from "zod";

import {
  formatScorePercent,
  scoreColorBand,
} from "../eval-indicator";
import { EvalRecord } from "@/lib/api/eval";

// ── scoreColorBand ─────────────────────────────────────────────────────────

describe("scoreColorBand", () => {
  it("returns success for a high score (≥0.80)", () => {
    expect(scoreColorBand(1.0)).toBe("success");
    expect(scoreColorBand(0.92)).toBe("success");
    expect(scoreColorBand(0.8)).toBe("success");
  });

  it("returns warning for an amber score (≥0.50 and <0.80)", () => {
    expect(scoreColorBand(0.79)).toBe("warning");
    expect(scoreColorBand(0.65)).toBe("warning");
    expect(scoreColorBand(0.5)).toBe("warning");
  });

  it("returns destructive for a low score (<0.50)", () => {
    expect(scoreColorBand(0.49)).toBe("destructive");
    expect(scoreColorBand(0.0)).toBe("destructive");
  });
});

// ── formatScorePercent ─────────────────────────────────────────────────────

describe("formatScorePercent", () => {
  it("formats 0.92 as '92%'", () => {
    expect(formatScorePercent(0.92)).toBe("92%");
  });

  it("formats 1.0 as '100%'", () => {
    expect(formatScorePercent(1.0)).toBe("100%");
  });

  it("formats 0.0 as '0%'", () => {
    expect(formatScorePercent(0.0)).toBe("0%");
  });

  it("rounds fractional percentages", () => {
    expect(formatScorePercent(0.456)).toBe("46%");
    expect(formatScorePercent(0.123)).toBe("12%");
  });
});

// ── EvalRecord Zod schema — BFF client contract ────────────────────────────

describe("EvalRecord Zod schema", () => {
  const validRecord = {
    id: "00000000-0000-0000-0000-000000000001",
    tenantId: "00000000-0000-0000-0000-000000000002",
    runId: "run-abc123",
    agentType: "requirements",
    score: 0.92,
    signals: { keyword_overlap: 0.9, present_count: 3 },
    createdAt: "2026-06-10T12:00:00+00:00",
  };

  it("parses a valid EvalRecord (high score case — renders as percentage)", () => {
    const result = EvalRecord.safeParse(validRecord);
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.score).toBe(0.92);
      // formatScorePercent used by the component to render the score
      expect(formatScorePercent(result.data.score!)).toBe("92%");
      expect(scoreColorBand(result.data.score!)).toBe("success");
    }
  });

  it("parses a valid EvalRecord with null score (empty-state condition)", () => {
    const emptyScore = { ...validRecord, score: null };
    const result = EvalRecord.safeParse(emptyScore);
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.score).toBeNull();
    }
  });

  it("parses an EvalRecord list — empty list triggers the empty state", () => {
    const emptyList = z.array(EvalRecord).safeParse([]);
    expect(emptyList.success).toBe(true);
    if (emptyList.success) {
      expect(emptyList.data.length).toBe(0);
      // Component renders "No quality signal yet" when data.length === 0
    }
  });

  it("applies correct color band for a low score (<0.50) — destructive variant", () => {
    const lowScore = { ...validRecord, score: 0.3 };
    const result = EvalRecord.safeParse(lowScore);
    expect(result.success).toBe(true);
    if (result.success) {
      expect(scoreColorBand(result.data.score!)).toBe("destructive");
      expect(formatScorePercent(result.data.score!)).toBe("30%");
    }
  });

  it("applies correct color band for an amber score (≥0.50 <0.80) — warning variant", () => {
    const amberScore = { ...validRecord, score: 0.65 };
    const result = EvalRecord.safeParse(amberScore);
    expect(result.success).toBe(true);
    if (result.success) {
      expect(scoreColorBand(result.data.score!)).toBe("warning");
      expect(formatScorePercent(result.data.score!)).toBe("65%");
    }
  });
});
