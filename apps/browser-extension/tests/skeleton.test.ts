import { describe, expect, it } from "vitest";
import { MAX_SKELETON_DEPTH, toSkeleton, parseBodyToSkeleton } from "../src/shared/skeleton";

/** nothing that carries data may survive skeletonization */
function assertNoValues(node: unknown): void {
  if (node === null || node === undefined) return;
  if (typeof node === "string") {
    expect(node).toBe(""); // the ONLY allowed string is the empty type tag
    return;
  }
  if (typeof node === "number") {
    expect([0, 0.5]).toContain(node);
    return;
  }
  if (typeof node === "boolean") {
    expect(node).toBe(false);
    return;
  }
  if (Array.isArray(node)) {
    for (const element of node) assertNoValues(element);
    return;
  }
  if (typeof node === "object") {
    for (const value of Object.values(node)) assertNoValues(value);
  }
}

describe("toSkeleton", () => {
  it("drops every leaf value but keeps structure and types", () => {
    const sample = {
      id: "C-1024",
      token: "Bearer eyJhbGciOi.eyJzdWIi.sig",
      email: "user@example.com",
      active: true,
      score: 3.14,
      count: 42,
      tags: ["alpha", "beta"],
      items: [{ sku: "A-1", qty: 2 }],
      nothing: null,
    };
    const skeleton = toSkeleton(sample);
    expect(skeleton).toEqual({
      id: "",
      token: "",
      email: "",
      active: false,
      score: 0.5,
      count: 0,
      tags: [""],
      items: [{ sku: "", qty: 0 }],
      nothing: null,
    });
    assertNoValues(skeleton);
  });

  it("separates integer and float leaves", () => {
    expect(toSkeleton(7)).toBe(0);
    expect(toSkeleton(-2.5)).toBe(0.5);
  });

  it("keeps one representative element per array", () => {
    expect(toSkeleton([1, 2, 3])).toEqual([0]);
    expect(toSkeleton([])).toEqual([]);
  });

  it("caps depth without throwing", () => {
    let deep: unknown = { leaf: "value" };
    for (let i = 0; i < 30; i++) deep = { nested: deep };
    const skeleton = toSkeleton(deep);
    assertNoValues(skeleton); // must terminate and stay value-free
    let depth = 0;
    let node = skeleton;
    while (node && typeof node === "object" && !Array.isArray(node)) {
      depth++;
      node = Object.values(node)[0];
    }
    expect(depth).toBeLessThanOrEqual(MAX_SKELETON_DEPTH);
  });
});

describe("parseBodyToSkeleton", () => {
  it("parses object and array JSON bodies", () => {
    expect(parseBodyToSkeleton('{"a":"x"}', 1024)).toEqual({ a: "" });
    expect(parseBodyToSkeleton("[1,2]", 1024)).toEqual([0]);
  });

  it("rejects non-JSON and oversized bodies", () => {
    expect(parseBodyToSkeleton("plain text", 1024)).toBeNull();
    expect(parseBodyToSkeleton(null, 1024)).toBeNull();
    expect(parseBodyToSkeleton('{"a":"x"}', 2)).toBeNull();
  });
});
