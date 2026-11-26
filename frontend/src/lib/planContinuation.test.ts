import { describe, expect, it } from "vitest";
import { planContinuationPrompt, planLabel } from "./planContinuation";

describe("planContinuation", () => {
  it("points the architect at the plan file", () => {
    const text = planContinuationPrompt({ path: "docs/copilot-parity-plan.md" });
    expect(text).toContain("docs/copilot-parity-plan.md");
    expect(text).toContain("Read it first");
    expect(text).toContain("next open batch");
  });

  it("labels a plan by title, else by file name, and shortens long ones", () => {
    expect(planLabel({ path: "docs/a-plan.md", title: "CrabAgent elite plan", mtime_ms: 0 })).toBe("CrabAgent elite plan");
    expect(planLabel({ path: "docs/copilot-parity-plan.md", title: "", mtime_ms: 0 })).toBe("copilot-parity-plan");
    const long = planLabel({ path: "docs/x-plan.md", title: "x".repeat(60), mtime_ms: 0 }, 10);
    expect(long).toHaveLength(10);
    expect(long.endsWith("…")).toBe(true);
  });
});
