// @vitest-environment jsdom
// Reload path: `GET /sessions/{id}/messages` returns `diff_preview` on a
// tool_confirm message (serve/services/message.py). A confirmation reloaded
// from the database must render the same diff card the live SSE path shows.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { MessageItem } from "../components/MessageItem";
import type { Message } from "../api/sessions";
import { dbMessagesToChat } from "./messageTransforms";

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

const UNIFIED = [
  "--- a/src/app.py",
  "+++ b/src/app.py",
  "@@ -1,3 +1,3 @@",
  " import os",
  "-x = 1",
  "+x = 2",
  " print(x)",
].join("\n");

function dbConfirm(extra: Partial<Message> = {}): Message {
  return {
    id: 7,
    sequence: 1,
    role: "tool_confirm",
    content: JSON.stringify({ args_summary: "path: src/app.py", status: "pending" }),
    tool_call_id: "c-1",
    name: "edit",
    created_at: null,
    ...extra,
  };
}

function renderReloaded(extra: Partial<Message> = {}): void {
  const [msg] = dbMessagesToChat([dbConfirm(extra)]);
  act(() => {
    root.render(<MessageItem item={msg} onToolConfirm={() => {}} />);
  });
}

describe("reloaded tool confirm diff preview", () => {
  it("renders the stored path and added/removed counts", () => {
    renderReloaded({
      diff_preview: {
        path: "src/app.py",
        unified: UNIFIED,
        added: 12,
        removed: 3,
        truncated: false,
        new_file: false,
      },
    });

    const card = container.querySelector('[data-testid="tool-confirm-diff"]');
    expect(card).not.toBeNull();
    expect(container.textContent).toContain("src/app.py");
    expect(container.querySelector('[data-testid="diff-added"]')?.textContent).toBe("+12");
    expect(container.querySelector('[data-testid="diff-removed"]')?.textContent).toBe("-3");
    expect(container.textContent).toContain("import os");
  });

  it("keeps the key off the message when the backend sent none", () => {
    const [msg] = dbMessagesToChat([dbConfirm()]);

    expect("diff_preview" in msg).toBe(false);
    renderReloaded();
    expect(container.querySelector('[data-testid="tool-confirm-diff"]')).toBeNull();
  });

  // A partial payload (missing `unified`) is dropped rather than half-rendered:
  // the confirmation keeps its pre-preview look and nothing throws.
  it("skips a malformed diff preview instead of throwing", () => {
    const [msg] = dbMessagesToChat([
      dbConfirm({ diff_preview: { path: "src/app.py", added: 1, removed: 1 } as never }),
    ]);

    expect("diff_preview" in msg).toBe(false);
    renderReloaded({
      diff_preview: { path: "src/app.py", added: 1, removed: 1 } as never,
    });
    expect(container.querySelector('[data-testid="tool-confirm-diff"]')).toBeNull();
    expect(container.textContent).toContain("requires permission");
  });

  // The diff belongs to the confirmation card only; a plain tool result that
  // somehow carries the field must not grow one.
  it("ignores diff_preview on messages that are not tool_confirm", () => {
    const [msg] = dbMessagesToChat([
      dbConfirm({ role: "tool", diff_preview: { path: "a.py", unified: UNIFIED, added: 1, removed: 0 } }),
    ]);

    expect("diff_preview" in msg).toBe(false);
  });
});
