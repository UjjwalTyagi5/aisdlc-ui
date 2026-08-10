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
