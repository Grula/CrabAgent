import { describe, expect, it } from "vitest";
import { autoPrompt, displayTitle, displayUserText } from "./userMessageText";

describe("displayUserText", () => {
  it("shows only what the user typed after a mention", () => {
    const sent = '[delegate_task] agent_name="architect" task="nicely done!"\n\nOriginal request: @architect nicely done!';
    expect(displayUserText(sent)).toBe("@architect nicely done!");
  });

  it("keeps multi-line originals intact", () => {
    const sent = '[delegate_parallel] tasks=[{"agent_name":"coder","task":"x"}]\n\nOriginal request: @coder @tester fix it\nplease';
    expect(displayUserText(sent)).toBe("@coder @tester fix it\nplease");
  });

  it("rebuilds instructions sent without the original text", () => {
    expect(displayUserText('[delegate_task] agent_name="tester" task="run the suite"')).toBe("@tester run the suite");
    expect(
      displayUserText('[delegate_parallel] tasks=[{"agent_name":"coder","task":"go"},{"agent_name":"tester","task":"go"}]'),
    ).toBe("@coder @tester go");
  });

  it("leaves ordinary messages alone", () => {
    expect(displayUserText("hello [delegate_task] later")).toBe("hello [delegate_task] later");
  });
});

describe("displayTitle", () => {
  it("cleans up titles saved as cut-off instructions", () => {
    expect(displayTitle('[delegate_task] agent_name="optimizer" task="Pleas...')).toBe("@optimizer Pleas...");
    expect(displayTitle('[delegate_parallel] tasks=[{"agent_name":"implemen...')).toBe("@implemen...");
    expect(displayTitle('[delegate_parallel] tasks=[{"')).toBe("Delegated task");
  });

  it("uses the original request when the title has it", () => {
    expect(displayTitle('[delegate_task] agent_name="a" task="b"\n\nOriginal request: @a b\nmore')).toBe("@a b");
  });

  it("leaves normal titles alone", () => {
    expect(displayTitle("Fix collab browser navigation")).toBe("Fix collab browser navigation");
  });
});

describe("autoPrompt", () => {
  it("folds a handover brief under who handed it over", () => {
    const sent = "[Handover] general handed this request over to you:\n\nAnalyze llama.cpp.\n\n1) research";
    expect(autoPrompt(sent)).toEqual({ title: "general handed this over", body: "Analyze llama.cpp.\n\n1) research" });
  });

  it("folds the goal-mode nudge", () => {
    const sent =
      "Continue the active goal from the latest checkpoint. Work on the next concrete unfinished step, then record progress or verified completion.";
    expect(autoPrompt(sent)).toEqual({ title: "Goal mode started the next round", body: sent });
  });

  it("leaves what the user typed alone", () => {
    expect(autoPrompt("why does [Handover] show up?")).toBeNull();
    expect(autoPrompt("[Handover] notes from yesterday")).toBeNull();
    expect(autoPrompt("Continue the active goal please")).toBeNull();
  });
});
