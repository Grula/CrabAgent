// @vitest-environment jsdom
// The tool confirmation card must show the change the user is approving:
// the backend attaches `diff_preview` to the `tool_confirm_request` payload
// (core/agent/tools/diff_preview.py). When the key is absent the card must
// look exactly as it did before.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { MessageItem, type ChatMessage } from "./MessageItem";
import { parseUnifiedDiff } from "../lib/unifiedDiff";

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

function renderConfirm(extra: Partial<ChatMessage> = {}): void {
  const msg: ChatMessage = {
    id: "cf-1",
    role: "tool_confirm",
    content: "",
    confirm_id: "c-1",
    tool_name: "edit",
    args_summary: "path: src/app.py",
    ...extra,
  };
  act(() => {
    root.render(<MessageItem item={msg} onToolConfirm={() => {}} />);
  });
}

describe("tool confirm diff preview", () => {
  it("renders the path and added/removed counts", () => {
    renderConfirm({
      diff_preview: {
        path: "src/app.py",
        unified: UNIFIED,
        added: 12,
        removed: 3,
        truncated: false,
        new_file: false,
      },
    });
    const section = container.querySelector('[data-testid="tool-confirm-diff"]');
    expect(section).not.toBeNull();
    expect(section!.textContent).toContain("src/app.py");
    expect(container.querySelector('[data-testid="diff-added"]')!.textContent).toBe("+12");
    expect(container.querySelector('[data-testid="diff-removed"]')!.textContent).toBe("-3");
    expect(container.querySelector('[data-testid="diff-new-file"]')).toBeNull();
    expect(container.querySelector('[data-testid="diff-truncated"]')).toBeNull();
  });

  it("labels a new file", () => {
    renderConfirm({
      diff_preview: {
        path: "src/new.py",
        unified: ["--- /dev/null", "+++ b/src/new.py", "@@ -0,0 +1 @@", "+print(1)"].join("\n"),
        added: 1,
        removed: 0,
        new_file: true,
      },
    });
    expect(container.querySelector('[data-testid="diff-new-file"]')!.textContent).toBe("new file");
  });

  it("notes a truncated diff", () => {
    renderConfirm({
      diff_preview: { path: "big.log", unified: UNIFIED, added: 1, removed: 1, truncated: true },
    });
    expect(container.querySelector('[data-testid="diff-truncated"]')!.textContent).toBe("truncated");
  });

  it("renders no diff section when diff_preview is absent, keeping the old dialog", () => {
    renderConfirm();
    expect(container.querySelector('[data-testid="tool-confirm-diff"]')).toBeNull();
    expect(container.textContent).toContain("edit requires permission");
    expect(container.textContent).toContain("path: src/app.py");
    const buttons = Array.from(container.querySelectorAll("button")).map((b) => b.textContent);
    expect(buttons.some((text) => text?.includes("Allow"))).toBe(true);
    expect(buttons.some((text) => text?.includes("Deny"))).toBe(true);
  });

  it("tags added and removed lines with their styling hook", () => {
    renderConfirm({
      diff_preview: { path: "src/app.py", unified: UNIFIED, added: 1, removed: 1 },
    });
    const added = container.querySelector('[data-diff-kind="added"]')!;
    const removed = container.querySelector('[data-diff-kind="removed"]')!;
    expect(added.textContent).toBe("+x = 2");
    expect(added.className).toContain("text-[var(--success)]");
    expect(removed.textContent).toBe("-x = 1");
    expect(removed.className).toContain("text-[var(--danger)]");
    // File headers are not mistaken for removed lines.
    expect(container.querySelectorAll('[data-diff-kind="file"]')).toHaveLength(2);
    expect(container.querySelector('[data-diff-kind="hunk"]')!.textContent).toContain("@@");
  });

  it("scrolls long diffs instead of expanding", () => {
    const long = ["--- a/a.py", "+++ b/a.py", "@@ -1 +1,500 @@"];
    for (let i = 0; i < 500; i += 1) long.push(`+line ${i}`);
    renderConfirm({
      diff_preview: { path: "a.py", unified: long.join("\n"), added: 500, removed: 0 },
    });
    const body = container.querySelector('[data-testid="diff-body"]')!;
    expect(body.className).toContain("max-h-[40vh]");
    expect(body.className).toContain("overflow-auto");
    expect(container.querySelectorAll("[data-diff-kind]")).toHaveLength(503);
  });
});

describe("parseUnifiedDiff", () => {
  it("classifies headers, hunks, changes and context", () => {
    expect(parseUnifiedDiff(UNIFIED).map((line) => line.kind)).toEqual([
      "file",
      "file",
      "hunk",
      "context",
      "removed",
      "added",
      "context",
    ]);
  });

  it("treats an empty body as no lines", () => {
    expect(parseUnifiedDiff("")).toEqual([]);
  });
});
