import { describe, expect, it } from "vitest";
import { SSEEvent } from "../api/events";
import { ChatMessage } from "../types/ChatMessage";
import { Message } from "../api/sessions";
import { dbMessagesToChat, sseEventToMessages } from "./messageTransforms";

function resultCard(runId: number): ChatMessage {
  return {
    id: `result-${runId}`,
    role: "task_result",
    content: "Generate report",
    task_result: {
      task_id: 9,
      title: "Generate report",
      status: "done",
      run_id: runId,
    },
  };
}

function taskEvent(runId: number, summary: string): SSEEvent {
  return {
    type: "task_updated",
    timestamp: Date.now(),
    data: {
      task_id: 9,
      title: "Generate report",
      status: "done",
      run_id: runId,
      result_summary: summary,
    },
  } as SSEEvent;
}

describe("task result cards", () => {
  it("adds one card for each completion run", () => {
    const messages = [resultCard(101)];
    const updated = sseEventToMessages(taskEvent(202, "V2"), messages);

    expect(updated).toHaveLength(2);
    expect(updated[1].task_result?.run_id).toBe(202);
  });

  it("updates the matching run instead of duplicating it", () => {
    const messages = [resultCard(101), resultCard(202)];
    const updated = sseEventToMessages(taskEvent(202, "V2 verified"), messages);

    expect(updated).toHaveLength(2);
    expect(updated[1].task_result?.result_summary).toBe("V2 verified");
  });
});

describe("bash stream correlation", () => {
  const calls: ChatMessage[] = ["one", "two"].map((id) => ({
    id: `tc-${id}`, role: "tool_call", tool_call_id: id,
    content: JSON.stringify({ name: "bash", arguments: {} }),
  }));
  it("routes chunks to the matching call instead of the most recent bash", () => {
    const updated = sseEventToMessages({ type: "bash_output", timestamp: 0, data: { text: "first", tool_call_id: "one" } }, calls);
    expect(updated[0].bashStream).toBe("first");
    expect(updated[1].bashStream).toBeUndefined();
    expect(calls[0].bashStream).toBeUndefined();
  });
  it("does not route an unknown call to a different terminal", () => {
    const updated = sseEventToMessages({ type: "bash_output", timestamp: 0, data: { text: "lost", tool_call_id: "missing" } }, calls);
    expect(updated.every((call) => !call.bashStream)).toBe(true);
  });
});

describe("dbMessagesToChat handovers", () => {
  const row = (id: number, role: string, content: string): Message => ({ id, sequence: id, role, content, created_at: null });

  it("turns a saved handover brief into a folded note, not a user bubble", () => {
    const chat = dbMessagesToChat([
      row(1, "user", "speed up llama.cpp"),
      row(2, "assistant", "Software engineer is taking over."),
      row(3, "user", "[Handover] general handed this request over to you:\n\nAnalyze and speed up inference."),
      row(4, "assistant", "Let me investigate."),
    ]);
    expect(chat.map((m) => m.role)).toEqual(["user", "assistant", "auto_prompt", "assistant"]);
    expect(chat[2]).toMatchObject({ auto_prompt_title: "general handed this over", content: "Analyze and speed up inference." });
  });
});
