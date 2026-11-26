import { describe, expect, it } from "vitest";
import { planLabel } from "../planContinuation";

describe("plan progress label (eval acceptance)", () => {
  it("appends done/total when there is a checklist", () => {
    expect(planLabel({ path: "docs/a-plan.md", title: "Elite plan", mtime_ms: 0, done: 3, total: 7 })).toBe("Elite plan (3/7)");
    expect(planLabel({ path: "docs/a-plan.md", title: "", mtime_ms: 0, done: 0, total: 2 })).toBe("a-plan (0/2)");
  });

  it("leaves labels without a total alone", () => {
    expect(planLabel({ path: "docs/a-plan.md", title: "Elite plan", mtime_ms: 0 })).toBe("Elite plan");
    expect(planLabel({ path: "docs/a-plan.md", title: "Elite plan", mtime_ms: 0, done: 0, total: 0 })).toBe("Elite plan");
  });

  it("applies max to the name only", () => {
    expect(planLabel({ path: "docs/x.md", title: "x".repeat(60), mtime_ms: 0, done: 1, total: 2 }, 10)).toBe(
      `${"x".repeat(9)}… (1/2)`,
    );
  });
});
