import { describe, expect, it } from "vitest";
import { parseCompaction } from "./compaction";

describe("parseCompaction", () => {
  it("treats empty as the global setting", () => {
    expect(parseCompaction("")).toBeNull();
    expect(parseCompaction("   ")).toBeNull();
  });

  it("accepts plain numbers, separators and a k suffix", () => {
    expect(parseCompaction("200000")).toBe(200000);
    expect(parseCompaction("200_000")).toBe(200000);
    expect(parseCompaction("200,000")).toBe(200000);
    expect(parseCompaction("200k")).toBe(200000);
  });

  it("rejects values below the minimum and non-numbers", () => {
    expect(() => parseCompaction("5000")).toThrow(/at least 20000/);
    expect(() => parseCompaction("lots")).toThrow();
    expect(() => parseCompaction("1.5k")).toThrow();
  });
});
