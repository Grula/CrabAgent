// @vitest-environment jsdom
// Regression test: the "Picked up" task card must show the deadline as the
// local wall clock the user typed. The `task_created` payload carries the raw
// local text (core/task/tools.py builds it from the tool argument), not a UTC
// instant, so running it through the wire parser — which reads offset-less
// input as UTC — shifted it by the host offset (14:30 rendered as 11:30 on a
// UTC-3 host).
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { MessageItem, type ChatMessage } from "./MessageItem";

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function renderCard(deadline: string): string {
  const msg: ChatMessage = {
    id: "task-1",
    role: "task_card",
    content: "Write report",
    task_card: { task_id: 1, title: "Write report", deadline },
  };
  act(() => {
    root.render(<MessageItem item={msg} />);
  });
  return container.textContent ?? "";
}

describe("task card deadline", () => {
  it("shows an offset-less deadline as the wall clock the user typed", () => {
    expect(renderCard("2026-11-20 14:30")).toContain("📅 2026-11-20 14:30");
  });

  it("shows an ISO T-form deadline verbatim", () => {
    expect(renderCard("2026-11-20T14:30")).toContain("📅 2026-11-20T14:30");
  });

  it("keeps a date-only deadline on its own day", () => {
    expect(renderCard("2026-11-20")).toContain("📅 2026-11-20");
  });
});
