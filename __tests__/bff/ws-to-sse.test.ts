/**
 * Unit tests for the WS→SSE event mapper.
 *
 * Each test asserts that mapWsToSseEvent returns either:
 *   - A StreamEvent that passes StreamEvent.safeParse() (non-null mappings), or
 *   - null for message types that have no UI value and must be discarded.
 *
 * These tests were written before the implementation (TDD RED phase).
 */
import { describe, it, expect } from "vitest";
import { StreamEvent } from "@/lib/schemas/stream";
import { mapWsToSseEvent } from "@/lib/bff/ws-to-sse";

const RUN_ID = "run_test_001";

describe("mapWsToSseEvent", () => {
  // ── stream_chunk → step.output.delta ──────────────────────────────────────
  describe("stream_chunk", () => {
    it("maps stream_chunk to step.output.delta and passes StreamEvent.safeParse", () => {
      const wsMsg = {
        type: "stream_chunk",
        content: "Hello, world!",
        session_id: "sess_abc",
      };
      const result = mapWsToSseEvent(wsMsg, RUN_ID);
      expect(result).not.toBeNull();
      const parsed = StreamEvent.safeParse(result);
      expect(parsed.success).toBe(true);
      if (parsed.success) {
        expect(parsed.data.type).toBe("step.output.delta");
        // Narrow to the delta event
        const evt = parsed.data as Extract<typeof parsed.data, { type: "step.output.delta" }>;
        expect(evt.delta).toBe("Hello, world!");
        expect(evt.runId).toBe(RUN_ID);
        expect(typeof evt.stepId).toBe("string");
        expect(evt.stepId.length).toBeGreaterThan(0);
        expect(typeof evt.at).toBe("string");
      }
    });

    it("uses a deterministic synthetic stepId derived from runId", () => {
      const wsMsg = { type: "stream_chunk", content: "A", session_id: "s1" };
      const r1 = mapWsToSseEvent(wsMsg, "run_x");
      const r2 = mapWsToSseEvent(wsMsg, "run_x");
      expect(r1).not.toBeNull();
      expect(r2).not.toBeNull();
      if (r1 && r2 && r1.type === "step.output.delta" && r2.type === "step.output.delta") {
        expect(r1.stepId).toBe(r2.stepId);
      }
    });
  });

  // ── stream_end → run.completed (approved) ─────────────────────────────────
  describe("stream_end", () => {
    it("maps stream_end to run.completed with status approved and passes StreamEvent.safeParse", () => {
      const wsMsg = { type: "stream_end", session_id: "sess_abc" };
      const result = mapWsToSseEvent(wsMsg, RUN_ID);
      expect(result).not.toBeNull();
      const parsed = StreamEvent.safeParse(result);
      expect(parsed.success).toBe(true);
      if (parsed.success) {
        expect(parsed.data.type).toBe("run.completed");
        const evt = parsed.data as Extract<typeof parsed.data, { type: "run.completed" }>;
        expect(evt.status).toBe("approved");
        expect(evt.runId).toBe(RUN_ID);
      }
    });
  });

  // ── file_generated → artifact.updated ─────────────────────────────────────
  describe("file_generated", () => {
    it("maps file_generated to artifact.updated with status approved and passes StreamEvent.safeParse", () => {
      const wsMsg = {
        type: "file_generated",
        filename: "requirements.docx",
        url: "/generated/requirements.docx",
        session_id: "sess_abc",
        file_size: 2048,
        agent_name: "requirements",
      };
      const result = mapWsToSseEvent(wsMsg, RUN_ID);
      expect(result).not.toBeNull();
      const parsed = StreamEvent.safeParse(result);
      expect(parsed.success).toBe(true);
      if (parsed.success) {
        expect(parsed.data.type).toBe("artifact.updated");
        const evt = parsed.data as Extract<typeof parsed.data, { type: "artifact.updated" }>;
        expect(evt.status).toBe("approved");
        expect(evt.runId).toBe(RUN_ID);
        expect(typeof evt.artifactId).toBe("string");
        expect(evt.artifactId.length).toBeGreaterThan(0);
      }
    });

    it("generates a deterministic artifactId from filename", () => {
      const wsMsg = {
        type: "file_generated",
        filename: "design.docx",
        url: "/generated/design.docx",
        session_id: "s1",
      };
      const r1 = mapWsToSseEvent(wsMsg, RUN_ID);
      const r2 = mapWsToSseEvent(wsMsg, RUN_ID);
      expect(r1).not.toBeNull();
      expect(r2).not.toBeNull();
      if (r1 && r2 && r1.type === "artifact.updated" && r2.type === "artifact.updated") {
        expect(r1.artifactId).toBe(r2.artifactId);
      }
    });
  });

  // ── agent_completed (failure) → run.completed (failed) ────────────────────
  describe("agent_completed", () => {
    it("maps agent_completed with success=false to run.completed with status failed", () => {
      const wsMsg = {
        type: "agent_completed",
        agent_id: "agent_req_001",
        agent_name: "requirements",
        stage: "requirements",
        success: false,
        timestamp: "2026-05-31T12:00:00Z",
      };
      const result = mapWsToSseEvent(wsMsg, RUN_ID);
      expect(result).not.toBeNull();
      const parsed = StreamEvent.safeParse(result);
      expect(parsed.success).toBe(true);
      if (parsed.success) {
        expect(parsed.data.type).toBe("run.completed");
        const evt = parsed.data as Extract<typeof parsed.data, { type: "run.completed" }>;
        expect(evt.status).toBe("failed");
        expect(evt.runId).toBe(RUN_ID);
      }
    });

    it("maps agent_completed with success=true to null (not a run.completed terminal signal)", () => {
      const wsMsg = {
        type: "agent_completed",
        agent_id: "agent_req_001",
        agent_name: "requirements",
        stage: "requirements",
        success: true,
        timestamp: "2026-05-31T12:00:00Z",
      };
      // success=true agent_completed is not a terminal failure signal — discard
      const result = mapWsToSseEvent(wsMsg, RUN_ID);
      expect(result).toBeNull();
    });
  });

  // ── Discarded message types ────────────────────────────────────────────────
  describe("discarded types", () => {
    it("returns null for message_received (ack only, no UI value)", () => {
      const wsMsg = {
        type: "message_received",
        session_id: "sess_abc",
        message: "User message acknowledged",
      };
      expect(mapWsToSseEvent(wsMsg, RUN_ID)).toBeNull();
    });

    it("returns null for echo (unrecognized type echo)", () => {
      const wsMsg = {
        type: "echo",
        message: "some unrecognized payload",
      };
      expect(mapWsToSseEvent(wsMsg, RUN_ID)).toBeNull();
    });

    it("returns null for completely unknown types", () => {
      const wsMsg = { type: "some_future_event", data: "x" };
      expect(mapWsToSseEvent(wsMsg, RUN_ID)).toBeNull();
    });

    it("returns null for non-object input", () => {
      expect(mapWsToSseEvent("raw string", RUN_ID)).toBeNull();
      expect(mapWsToSseEvent(null, RUN_ID)).toBeNull();
      expect(mapWsToSseEvent(42, RUN_ID)).toBeNull();
    });
  });

  // ── activity_update ────────────────────────────────────────────────────────
  describe("activity_update", () => {
    it("discards activity_update with non-complete type", () => {
      const wsMsg = {
        type: "activity_update",
        activity: { id: "a1", message: "Thinking...", type: "in_progress", time: "now", sessionId: "s1" },
      };
      expect(mapWsToSseEvent(wsMsg, RUN_ID)).toBeNull();
    });

    it("maps activity_update with activity.type=complete to run.completed (approved)", () => {
      const wsMsg = {
        type: "activity_update",
        activity: { id: "a2", message: "Done", type: "complete", time: "now", sessionId: "s1" },
      };
      const result = mapWsToSseEvent(wsMsg, RUN_ID);
      expect(result).not.toBeNull();
      const parsed = StreamEvent.safeParse(result);
      expect(parsed.success).toBe(true);
      if (parsed.success) {
        expect(parsed.data.type).toBe("run.completed");
        const evt = parsed.data as Extract<typeof parsed.data, { type: "run.completed" }>;
        expect(evt.status).toBe("approved");
      }
    });
  });
});
