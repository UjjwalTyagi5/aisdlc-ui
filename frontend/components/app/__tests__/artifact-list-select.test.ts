import { describe, it, expect } from "vitest";
import { toggleSelection } from "@/components/app/artifact-list";

describe("toggleSelection", () => {
  it("adds an id that is absent", () => {
    const next = toggleSelection(new Set<string>(), "a");
    expect([...next]).toEqual(["a"]);
  });

  it("removes an id that is present", () => {
    const next = toggleSelection(new Set(["a", "b"]), "a");
    expect([...next].sort()).toEqual(["b"]);
  });

  it("does not mutate the input set", () => {
    const input = new Set(["a"]);
    toggleSelection(input, "b");
    expect([...input]).toEqual(["a"]);
  });
});

// ── which rows may offer a delete ────────────────────────────────────────────

import { isStoredArtifact } from "@/components/app/artifact-list";

describe("isStoredArtifact", () => {
  it("accepts a stored artifact's UUID", () => {
    expect(isStoredArtifact("9cd3d736-1ba7-4e06-98c1-ae9e1ea6afa1")).toBe(true);
  });

  it("rejects a synthesised story id", () => {
    // story_artifacts_from_run builds these from a run's requirements_payload; they
    // name no row, so every write route answers 404 and a delete button is a lie.
    expect(isStoredArtifact("2c1ee894-d371-4e53-8e71-efb825a83dac:story:SCRUM-16")).toBe(
      false,
    );
  });

  it("rejects anything else that is not a UUID", () => {
    for (const id of ["", "not-a-uuid", "../../etc/passwd", "1234"]) {
      expect(isStoredArtifact(id)).toBe(false);
    }
  });

  it("is case-insensitive", () => {
    expect(isStoredArtifact("9CD3D736-1BA7-4E06-98C1-AE9E1EA6AFA1")).toBe(true);
  });
});
