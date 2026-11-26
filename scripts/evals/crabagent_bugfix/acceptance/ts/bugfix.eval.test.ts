import { describe, expect, it } from "vitest";
import { parseCompaction } from "../compaction";
import { planLabel } from "../planContinuation";

describe("crabagent_bugfix acceptance (frontend)", () => {
  it("keeps a title of exactly the limit whole and shortens longer ones", () => {
    const forty = "t".repeat(40);
    expect(planLabel({ path: "docs/a-plan.md", title: forty, mtime_ms: 0 })).toBe(forty);
    const fortyOne = "t".repeat(41);
    expect(planLabel({ path: "docs/a-plan.md", title: fortyOne, mtime_ms: 0 })).toBe(`${"t".repeat(39)}…`);
    expect(planLabel({ path: "docs/a-plan.md", title: "abcde", mtime_ms: 0 }, 5)).toBe("abcde");
  });

  it("reads a k suffix as thousands", () => {
    expect(parseCompaction("200k")).toBe(200000);
    expect(parseCompaction("64K")).toBe(64000);
    expect(parseCompaction("20k")).toBe(20000);
    expect(() => parseCompaction("19k")).toThrow();
  });
});
