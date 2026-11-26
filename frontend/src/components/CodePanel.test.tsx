// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CodeEditorHandle } from "./code-editor";
import { CodePanel, promptFileContext, type CodePanelState, type ExternalFileChange } from "./CodePanel";
import { ApiError, api } from "../api/client";
import * as sessionsApi from "../api/sessions";

const { readFileMock, saveFileMock } = vi.hoisted(() => ({
  readFileMock: vi.fn(),
  saveFileMock: vi.fn(),
}));

// Keep the real module (fileContextFields is pure) and stub only the network calls.
vi.mock("../api/files", async () => {
  const actual = await vi.importActual<typeof import("../api/files")>("../api/files");
  return { ...actual, readFile: readFileMock, saveFile: saveFileMock };
});

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

// jsdom has no layout: CodeMirror's selection layer asks Range for client rects.
if (typeof Range.prototype.getClientRects !== "function") {
  Range.prototype.getClientRects = () => [] as unknown as DOMRectList;
}
if (typeof Range.prototype.getBoundingClientRect !== "function") {
  Range.prototype.getBoundingClientRect = () => ({
    x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, toJSON: () => ({}),
  }) as DOMRect;
}

let root: Root | null = null;

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = "";
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

beforeEach(() => {
  saveFileMock.mockResolvedValue({ status: "ok", path: "/w/src/a.ts", size: 1 });
});

async function flush() {
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
}

/** Wait until the (dynamically imported) editor or one of the fallback states is rendered. */
async function waitForSettled(host: HTMLElement) {
  const settled = () => Boolean(
    host.querySelector(".cm-editor")
    || host.querySelector('[data-testid="code-binary"]')
    || host.querySelector('[data-testid="code-load-error"]'),
  );
  const deadline = Date.now() + 5000;
  while (!settled() && Date.now() < deadline) {
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)); });
  }
  if (!settled()) throw new Error("CodePanel did not settle within the timeout");
}

async function renderPanel(props: Partial<React.ComponentProps<typeof CodePanel>> = {}) {
  const handles: CodeEditorHandle[] = [];
  const states: CodePanelState[] = [];
  const host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  const render = (next: Partial<React.ComponentProps<typeof CodePanel>> = {}) => {
    act(() => root!.render(
      <CodePanel
        initialPath="/w/src/a.ts"
        initialContent=""
        onEditorReady={(handle) => { if (handle) handles.push(handle); }}
        onStateChange={(state) => states.push(state)}
        {...props}
        {...next}
      />,
    ));
  };
  render();
  await waitForSettled(host);
  const handle = handles.length ? handles[handles.length - 1] : null;
  return { host, handle, handles, states, render };
}

function pressSave() {
  const event = new KeyboardEvent("keydown", { key: "s", ctrlKey: true, bubbles: true, cancelable: true });
  act(() => { window.dispatchEvent(event); });
  return event;
}

describe("CodePanel", () => {
  it("renders a syntax-highlighted editor for a .ts file", async () => {
    readFileMock.mockResolvedValue({ path: "/w/src/a.ts", content: "const answer = 42;\n", truncated: false });
    const { host, handle, states } = await renderPanel();

    expect(host.querySelector('[data-testid="code-editor-host"] .cm-editor')).not.toBeNull();
    expect(host.querySelector(".cm-content")).not.toBeNull();
    expect(host.querySelector('[data-testid="code-binary"]')).toBeNull();
    expect(host.querySelector('[data-testid="code-truncated-notice"]')).toBeNull();

    expect(readFileMock).toHaveBeenCalledWith("/w/src/a.ts", true, { sessionId: undefined });
    expect(handle).not.toBeNull();
    expect(handle!.getContent()).toBe("const answer = 42;\n");
    expect(handle!.view.state.readOnly).toBe(false);

    // The parent can read the open path and dirty flag.
    const last = states[states.length - 1];
    expect(last.openPath).toBe("/w/src/a.ts");
    expect(last.dirty).toBe(false);
    expect(last.binary).toBe(false);
  });

  it("shows the read-only state for a binary payload", async () => {
    readFileMock.mockResolvedValue({ path: "/w/src/logo.png", content: "", truncated: false, binary: true });
    const { host, handle } = await renderPanel({ initialPath: "/w/src/logo.png" });

    const binary = host.querySelector('[data-testid="code-binary"]');
    expect(binary).not.toBeNull();
    expect(binary!.textContent).toContain("cannot be edited as text");
    expect(host.querySelector(".cm-editor")).toBeNull();
    expect(handle).toBeNull();

    // The save shortcut must stay inert: no editor, nothing to write.
    pressSave();
    expect(saveFileMock).not.toHaveBeenCalled();
  });

  it("opens truncated files read-only with a visible notice", async () => {
    readFileMock.mockResolvedValue({ path: "/w/src/a.ts", content: "const a = 1;\n", truncated: true });
    const { host, handle, states } = await renderPanel();

    const notice = host.querySelector('[data-testid="code-truncated-notice"]');
    expect(notice).not.toBeNull();
    expect(notice!.textContent).toContain("truncated");
    expect(host.querySelector(".cm-editor")).not.toBeNull();
    expect(handle!.view.state.readOnly).toBe(true);
    expect(states[states.length - 1].readOnly).toBe(true);

    // Even after an edit attempt, the save shortcut is suppressed.
    act(() => {
      handle!.view.dispatch({ changes: { from: 0, insert: "// edited\n" } });
    });
    await flush();
    pressSave();
    expect(saveFileMock).not.toHaveBeenCalled();
  });

  it("saves once on Ctrl/Cmd+S without triggering the browser save dialog", async () => {
    readFileMock.mockResolvedValue({ path: "/w/src/a.ts", content: "const a = 1;\n", truncated: false });
    const { host, handle } = await renderPanel();

    act(() => {
      handle!.view.dispatch({ changes: { from: 0, insert: "// edited\n" } });
    });
    await flush();

    const event = pressSave();
    await flush();

    expect(event.defaultPrevented).toBe(true);
    expect(saveFileMock).toHaveBeenCalledTimes(1);
    expect(saveFileMock).toHaveBeenCalledWith(
      "/w/src/a.ts", "// edited\nconst a = 1;\n", true, { sessionId: undefined },
    );
    // No navigation/reload happened — the panel is still mounted.
    expect(document.body.contains(host)).toBe(true);
    expect(host.querySelector(".cm-editor")).not.toBeNull();
  });

  it("keeps the buffer when the parent hides and shows the panel again", async () => {
    readFileMock.mockResolvedValue({ path: "/w/src/a.ts", content: "const a = 1;\n", truncated: false });
    const { host, handle, states, render } = await renderPanel({ visible: true });

    act(() => {
      handle!.view.dispatch({ changes: { from: 0, insert: "// kept\n" } });
    });
    await flush();
    expect(states[states.length - 1].dirty).toBe(true);

    const measure = vi.spyOn(handle!.view, "requestMeasure");

    // Hidden behind another document: still mounted, nothing is dropped.
    render({ visible: false });
    await flush();
    expect(host.querySelector(".cm-editor")).not.toBeNull();
    expect(handle!.getContent()).toBe("// kept\nconst a = 1;\n");

    // Shown again: the same view (not a new one) re-measures for the new size.
    render({ visible: true });
    await flush();
    expect(measure).toHaveBeenCalled();
    expect(handle!.getContent()).toBe("// kept\nconst a = 1;\n");
    expect(states[states.length - 1].dirty).toBe(true);
    expect(saveFileMock).not.toHaveBeenCalled();
  });

  it("shows an inline error with a retry button when loading fails", async () => {
    readFileMock
      .mockRejectedValueOnce(new Error("read failed"))
      .mockResolvedValueOnce({ path: "/w/src/a.ts", content: "let ok = true;\n", truncated: false });
    const { host } = await renderPanel();

    const failure = host.querySelector('[data-testid="code-load-error"]');
    expect(failure).not.toBeNull();
    expect(failure!.textContent).toContain("read failed");

    const retry = Array.from(failure!.querySelectorAll("button")).find((b) => b.textContent?.includes("Retry"));
    expect(retry).toBeTruthy();
    act(() => { retry!.click(); });
    await waitForSettled(host);

    expect(host.querySelector('[data-testid="code-load-error"]')).toBeNull();
    expect(host.querySelector(".cm-editor")).not.toBeNull();
  });
});

/** A `file_updated` event of the shape ChatPage forwards from the SSE switch. */
function externalChange(overrides: Partial<ExternalFileChange> = {}): ExternalFileChange {
  return { path: "/w/src/a.ts", tool: "write", seq: 1, ...overrides };
}

const readyState: CodePanelState = {
  openPath: "/w/src/a.ts",
  dirty: false,
  anyDirty: false,
  readOnly: false,
  binary: false,
  truncated: false,
  language: "typescript",
  status: "ready",
};

describe("CodePanel external changes", () => {
  it("keeps the caret on its text when a clean buffer is edited before it", async () => {
    readFileMock.mockResolvedValueOnce({ path: "/w/src/a.ts", content: "aaa bbb\n", truncated: false });
    const { handle, render } = await renderPanel();
    act(() => { handle!.view.dispatch({ selection: { anchor: 5 } }); });
    expect(handle!.view.state.selection.main.head).toBe(5);

    readFileMock.mockResolvedValueOnce({ path: "/w/src/a.ts", content: "Xaaa bbb\n", truncated: false });
    await act(async () => { render({ externalChange: externalChange({ seq: 1 }) }); });
    await flush();

    expect(readFileMock).toHaveBeenCalledTimes(2);
    expect(handle!.view.state.doc.toString()).toBe("Xaaa bbb\n");
    // The insertion shifted the text by one; the caret follows it instead of staying at 5.
    expect(handle!.view.state.selection.main.head).toBe(6);
  });

  it("leaves the caret in place when a clean buffer is edited after it", async () => {
    readFileMock.mockResolvedValueOnce({ path: "/w/src/a.ts", content: "aaa bbb\n", truncated: false });
    const { handle, render } = await renderPanel();
    act(() => { handle!.view.dispatch({ selection: { anchor: 5 } }); });

    readFileMock.mockResolvedValueOnce({ path: "/w/src/a.ts", content: "aaa bbbX\n", truncated: false });
    await act(async () => { render({ externalChange: externalChange({ seq: 1 }) }); });
    await flush();

    expect(handle!.view.state.doc.toString()).toBe("aaa bbbX\n");
    expect(handle!.view.state.selection.main.head).toBe(5);
  });

  it("maps a selection through an edit inside it", async () => {
    readFileMock.mockResolvedValueOnce({ path: "/w/src/a.ts", content: "aaa bbb ccc\n", truncated: false });
    const { handle, render } = await renderPanel();
    act(() => { handle!.view.dispatch({ selection: { anchor: 4, head: 7 } }); });
    expect(handle!.view.state.selection.main.from).toBe(4);
    expect(handle!.view.state.selection.main.to).toBe(7);

    readFileMock.mockResolvedValueOnce({ path: "/w/src/a.ts", content: "aaa bXbb ccc\n", truncated: false });
    await act(async () => { render({ externalChange: externalChange({ seq: 1 }) }); });
    await flush();

    expect(handle!.view.state.doc.toString()).toBe("aaa bXbb ccc\n");
    // The selection now spans the inserted character: 4..8 ("bXbb").
    expect(handle!.view.state.selection.main.from).toBe(4);
    expect(handle!.view.state.selection.main.to).toBe(8);
  });

  it("shows a banner and keeps the buffer when it is dirty", async () => {
    readFileMock.mockResolvedValueOnce({ path: "/w/src/a.ts", content: "const a = 1;\n", truncated: false });
    const { host, handle, render } = await renderPanel();
    act(() => { handle!.view.dispatch({ changes: { from: 0, insert: "// edited\n" } }); });
    await flush();

    readFileMock.mockResolvedValueOnce({ path: "/w/src/a.ts", content: "const a = 2;\n", truncated: false });
    await act(async () => { render({ externalChange: externalChange({ seq: 1 }) }); });
    await flush();

    const banner = host.querySelector('[data-testid="code-external-change"]');
    expect(banner).not.toBeNull();
    expect(banner!.textContent).toContain("Changed on disk by write");
    expect(Array.from(banner!.querySelectorAll("button")).map((b) => b.textContent)).toEqual([
      "Reload",
      "Show diff",
    ]);
    // Unsaved work is never overwritten behind the user's back.
    expect(handle!.getContent()).toBe("// edited\nconst a = 1;\n");
  });

  it("replaces the buffer with the disk copy when Reload is clicked", async () => {
    readFileMock.mockResolvedValueOnce({ path: "/w/src/a.ts", content: "const a = 1;\n", truncated: false });
    const { host, handle, render } = await renderPanel();
    act(() => { handle!.view.dispatch({ changes: { from: 0, insert: "// edited\n" } }); });
    await flush();

    readFileMock.mockResolvedValueOnce({ path: "/w/src/a.ts", content: "const a = 2;\n", truncated: false });
    await act(async () => { render({ externalChange: externalChange({ seq: 1 }) }); });
    await flush();

    const banner = host.querySelector('[data-testid="code-external-change"]')!;
    const reload = Array.from(banner.querySelectorAll("button")).find((b) => b.textContent === "Reload")!;
    await act(async () => { reload.click(); });
    await flush();

    expect(handle!.getContent()).toBe("const a = 2;\n");
    expect(host.querySelector('[data-testid="code-external-change"]')).toBeNull();
  });

  it("dispatching nothing when the disk content is identical", async () => {
    readFileMock.mockResolvedValueOnce({ path: "/w/src/a.ts", content: "const a = 1;\n", truncated: false });
    const { host, handle, render } = await renderPanel();
    const dispatch = vi.spyOn(handle!.view, "dispatch");

    readFileMock.mockResolvedValueOnce({ path: "/w/src/a.ts", content: "const a = 1;\n", truncated: false });
    await act(async () => { render({ externalChange: externalChange({ seq: 1 }) }); });
    await flush();

    // No transaction at all: the cursor cannot jump even if it sat at the end.
    expect(dispatch).not.toHaveBeenCalled();
    expect(host.querySelector('[data-testid="code-external-change"]')).toBeNull();
  });

  it("ignores a change for a path that is not open", async () => {
    readFileMock.mockResolvedValueOnce({ path: "/w/src/a.ts", content: "const a = 1;\n", truncated: false });
    const { host, handle, render } = await renderPanel();
    act(() => { handle!.view.dispatch({ changes: { from: 0, insert: "// edited\n" } }); });
    await flush();

    await act(async () => {
      render({ externalChange: externalChange({ path: "/w/src/other.ts", seq: 1 }) });
    });
    await flush();

    expect(readFileMock).toHaveBeenCalledTimes(1);
    expect(handle!.getContent()).toBe("// edited\nconst a = 1;\n");
    expect(host.querySelector('[data-testid="code-external-change"]')).toBeNull();
  });
});

/** Names of the tabs currently in the CodeTabs bar. */
function tabNames(host: HTMLElement): string[] {
  const bar = host.querySelector(".overflow-x-auto");
  if (!bar) return [];
  return Array.from(bar.querySelectorAll("span"))
    .map((span) => span.textContent || "")
    .filter(Boolean);
}

function tabCloseButton(host: HTMLElement, name: string): HTMLButtonElement {
  const span = Array.from(host.querySelectorAll("span")).find((el) => el.textContent === name);
  const button = span?.parentElement?.querySelector("button");
  if (!button) throw new Error(`tab ${name} has no close button`);
  return button as HTMLButtonElement;
}

/** Wait until the newest editor shows `expected` (tab switches build a new editor). */
async function waitForEditorContent(handles: CodeEditorHandle[], expected: string): Promise<CodeEditorHandle> {
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    const current = handles[handles.length - 1];
    if (current && current.getContent() === expected) return current;
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)); });
  }
  throw new Error(`editor never showed ${JSON.stringify(expected)}`);
}

describe("CodePanel tabs", () => {
  it("keeps a dirty buffer when another file opens and the first is reopened", async () => {
    readFileMock.mockImplementation((path: string) => Promise.resolve({
      path,
      content: path.endsWith("b.ts") ? "const b = 1;\n" : "const a = 1;\n",
      truncated: false,
    }));
    const { host, handle, handles, states, render } = await renderPanel();

    act(() => { handle!.view.dispatch({ changes: { from: 0, insert: "// typed in a\n" } }); });
    await flush();
    expect(states[states.length - 1].dirty).toBe(true);

    // The parent (file tree) hands over a second file; the panel stays mounted.
    await act(async () => { render({ initialPath: "/w/src/b.ts" }); });
    await waitForEditorContent(handles, "const b = 1;\n");
    expect(readFileMock).toHaveBeenCalledWith("/w/src/b.ts", true, { sessionId: undefined });
    expect(states[states.length - 1].openPath).toBe("/w/src/b.ts");
    expect(tabNames(host)).toEqual(["a.ts", "b.ts"]);

    // Reopening the first file restores exactly what was typed, still dirty.
    await act(async () => { render({ initialPath: "/w/src/a.ts" }); });
    await waitForEditorContent(handles, "// typed in a\nconst a = 1;\n");
    const last = states[states.length - 1];
    expect(last.openPath).toBe("/w/src/a.ts");
    expect(last.dirty).toBe(true);
    // Both files stay open and the reopened one is not re-read from disk.
    expect(tabNames(host)).toEqual(["a.ts", "b.ts"]);
    expect(readFileMock).toHaveBeenCalledTimes(2);
  });

  it("asks before closing a tab with unsaved edits", async () => {
    readFileMock.mockResolvedValue({ path: "/w/src/a.ts", content: "const a = 1;\n", truncated: false });
    const { host, handle, states } = await renderPanel();
    act(() => { handle!.view.dispatch({ changes: { from: 0, insert: "// typed\n" } }); });
    await flush();

    act(() => { tabCloseButton(host, "a.ts").click(); });
    await flush();

    expect(document.querySelector('[data-testid="code-discard-confirm"]')).not.toBeNull();
    expect(document.body.textContent).toContain("Discard changes to a.ts?");
    // Nothing is dropped yet.
    expect(tabNames(host)).toEqual(["a.ts"]);
    expect(states[states.length - 1].dirty).toBe(true);

    // Cancel keeps the dirty buffer.
    const cancel = Array.from(document.querySelectorAll("button")).find((b) => b.textContent === "Cancel");
    expect(cancel).toBeTruthy();
    act(() => { cancel!.click(); });
    await flush();
    expect(document.querySelector('[data-testid="code-discard-confirm"]')).toBeNull();
    expect(tabNames(host)).toEqual(["a.ts"]);
    expect(states[states.length - 1].dirty).toBe(true);

    // Confirming discards it.
    act(() => { tabCloseButton(host, "a.ts").click(); });
    await flush();
    act(() => { (document.querySelector('[data-testid="code-discard-confirm"]') as HTMLButtonElement).click(); });
    await flush();
    expect(tabNames(host)).toEqual([]);
  });

  it("closes a clean tab without asking", async () => {
    readFileMock.mockResolvedValue({ path: "/w/src/a.ts", content: "const a = 1;\n", truncated: false });
    const { host } = await renderPanel();

    act(() => { tabCloseButton(host, "a.ts").click(); });
    await flush();

    expect(document.querySelector('[data-testid="code-discard-confirm"]')).toBeNull();
    expect(tabNames(host)).toEqual([]);
  });
});

describe("CodePanel prompt context", () => {
  it("carries the buffer text only while the buffer is dirty", () => {
    expect(promptFileContext(readyState, "unused")).toEqual({ file_context: "/w/src/a.ts" });
    expect(promptFileContext({ ...readyState, dirty: true }, "const a = 2;\n")).toEqual({
      file_context: "/w/src/a.ts",
      file_context_dirty: true,
      file_context_content: "const a = 2;\n",
    });
    // No file open → nothing to attach.
    expect(promptFileContext(null, "")).toEqual({});
  });

  it("sends the dirty buffer fields and only the path when clean", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue({ status: "queued" });

    await sessionsApi.sendPrompt(
      "sess-1", "fix this", undefined, undefined, undefined, undefined, undefined,
      "/w/src/a.ts", undefined, true,
      promptFileContext({ ...readyState, dirty: true }, "const a = 2;\n"),
    );
    const dirtyBody = post.mock.calls[0][1] as Record<string, unknown>;
    expect(dirtyBody.file_context).toBe("/w/src/a.ts");
    expect(dirtyBody.file_context_dirty).toBe(true);
    expect(dirtyBody.file_context_content).toBe("const a = 2;\n");

    await sessionsApi.sendPrompt(
      "sess-1", "fix this", undefined, undefined, undefined, undefined, undefined,
      "/w/src/a.ts", undefined, true,
      promptFileContext(readyState, ""),
    );
    const cleanBody = post.mock.calls[1][1] as Record<string, unknown>;
    expect(cleanBody.file_context).toBe("/w/src/a.ts");
    expect(cleanBody.file_context_dirty).toBeUndefined();
    expect(cleanBody.file_context_content).toBeUndefined();
  });
});

describe("CodePanel stale-write guard", () => {
  it("sends the mtime of the copy it read as expected_mtime_ms", async () => {
    readFileMock.mockResolvedValue({
      path: "/w/src/a.ts", content: "const a = 1;\n", truncated: false, mtime_ms: 1111,
    });
    const { handle } = await renderPanel();
    act(() => { handle!.view.dispatch({ changes: { from: 0, insert: "// edited\n" } }); });
    await flush();

    pressSave();
    await flush();

    expect(saveFileMock).toHaveBeenCalledWith(
      "/w/src/a.ts", "// edited\nconst a = 1;\n", true,
      { sessionId: undefined, expectedMtimeMs: 1111 },
    );
  });

  it("shows the conflict banner on HTTP 409 and never overwrites the disk copy", async () => {
    readFileMock.mockResolvedValue({
      path: "/w/src/a.ts", content: "const a = 1;\n", truncated: false, mtime_ms: 1111,
    });
    const { host, handle } = await renderPanel();
    act(() => { handle!.view.dispatch({ changes: { from: 0, insert: "// mine\n" } }); });
    await flush();

    saveFileMock.mockRejectedValueOnce(new ApiError("File changed on disk since it was opened", 409, {}));
    readFileMock.mockResolvedValueOnce({
      path: "/w/src/a.ts", content: "// outside\nconst a = 1;\n", truncated: false, mtime_ms: 2222,
    });

    pressSave();
    await flush();

    const banner = host.querySelector('[data-testid="code-external-change"]');
    expect(banner).not.toBeNull();
    expect(banner!.textContent).toContain("Changed on disk by another process");
    expect(Array.from(banner!.querySelectorAll("button")).map((b) => b.textContent)).toEqual([
      "Reload",
      "Show diff",
    ]);
    // The buffer and the caret stay exactly as they were.
    expect(handle!.getContent()).toBe("// mine\nconst a = 1;\n");

    // The stored mtime is not refreshed from the 409, so a plain retry conflicts again.
    pressSave();
    await flush();
    expect((saveFileMock.mock.calls[1][3] as { expectedMtimeMs?: number }).expectedMtimeMs).toBe(1111);
  });

  it("sends the new mtime after a successful save", async () => {
    readFileMock.mockResolvedValue({
      path: "/w/src/a.ts", content: "const a = 1;\n", truncated: false, mtime_ms: 1111,
    });
    const { handle } = await renderPanel();
    act(() => { handle!.view.dispatch({ changes: { from: 0, insert: "// one\n" } }); });
    await flush();

    saveFileMock.mockResolvedValueOnce({ status: "ok", path: "/w/src/a.ts", size: 20, mtime_ms: 3000 });
    pressSave();
    await flush();
    expect((saveFileMock.mock.calls[0][3] as { expectedMtimeMs?: number }).expectedMtimeMs).toBe(1111);

    act(() => { handle!.view.dispatch({ changes: { from: 0, insert: "// two\n" } }); });
    await flush();
    pressSave();
    await flush();

    expect(saveFileMock).toHaveBeenCalledTimes(2);
    expect((saveFileMock.mock.calls[1][3] as { expectedMtimeMs?: number }).expectedMtimeMs).toBe(3000);
  });

  it("does not reload the file for its own save echo", async () => {
    readFileMock.mockResolvedValue({
      path: "/w/src/a.ts", content: "const a = 1;\n", truncated: false, mtime_ms: 1111,
    });
    const { host, handle, render } = await renderPanel();
    act(() => { handle!.view.dispatch({ changes: { from: 0, insert: "// mine\n" } }); });
    await flush();

    saveFileMock.mockResolvedValueOnce({ status: "ok", path: "/w/src/a.ts", size: 20, mtime_ms: 5000 });
    pressSave();
    await flush();
    const readsAfterSave = readFileMock.mock.calls.length;

    await act(async () => {
      render({ externalChange: externalChange({ seq: 1, origin: "ui", mtimeMs: 5000 }) });
    });
    await flush();

    expect(readFileMock).toHaveBeenCalledTimes(readsAfterSave);
    expect(host.querySelector('[data-testid="code-external-change"]')).toBeNull();
    expect(handle!.getContent()).toBe("// mine\nconst a = 1;\n");
  });

  it("closes the previous chat's documents when the session changes", async () => {
    readFileMock.mockResolvedValue({
      path: "/w/src/a.ts", content: "const a = 1;\n", truncated: false, mtime_ms: 1111,
    });
    const { host, states, render } = await renderPanel({ sessionId: "chat-a" });

    await act(async () => { render({ sessionId: "chat-b" }); });
    await flush();

    // No tab from the old workspace survives, so a save cannot reach the new one.
    expect(states[states.length - 1].openPath).toBeNull();
    expect(host.querySelector('[data-testid="code-editor-host"]')).toBeNull();
    pressSave();
    await flush();
    expect(saveFileMock).not.toHaveBeenCalled();
  });
});

describe("CodePanel save race", () => {
  it("keeps typing that lands during a save dirty and keeps the newer text", async () => {
    readFileMock.mockResolvedValue({
      path: "/w/src/a.ts", content: "const a = 1;\n", truncated: false, mtime_ms: 1111,
    });
    const { host, handle, render, states } = await renderPanel();
    act(() => { handle!.view.dispatch({ changes: { from: 0, insert: "// one\n" } }); });
    await flush();

    let resolveSave!: (value: { status: string; path: string; size: number; mtime_ms: number }) => void;
    saveFileMock.mockImplementationOnce(() => new Promise((resolve) => { resolveSave = resolve; }));

    pressSave();
    await flush();
    expect(saveFileMock).toHaveBeenCalledTimes(1);
    expect(states[states.length - 1].dirty).toBe(true);

    // More typing while the request is still in flight.
    act(() => { handle!.view.dispatch({ changes: { from: 0, insert: "// two\n" } }); });
    await flush();

    await act(async () => {
      resolveSave({ status: "ok", path: "/w/src/a.ts", size: 30, mtime_ms: 4000 });
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await flush();

    // Only "// one\n..." reached the disk; the newer buffer is still unsaved.
    expect(handle!.getContent()).toBe("// two\n// one\nconst a = 1;\n");
    expect(states[states.length - 1].dirty).toBe(true);

    // So a file_updated for the same path must not replace that newer text.
    readFileMock.mockResolvedValueOnce({
      path: "/w/src/a.ts", content: "// disk\n", truncated: false, mtime_ms: 5000,
    });
    await act(async () => { render({ externalChange: externalChange({ seq: 1 }) }); });
    await flush();
    expect(host.querySelector('[data-testid="code-external-change"]')).not.toBeNull();
    expect(handle!.getContent()).toBe("// two\n// one\nconst a = 1;\n");
  });
});

describe("CodePanel encoding", () => {
  it("saves a latin-1 file with its own encoding and shows it", async () => {
    readFileMock.mockResolvedValue({
      path: "/w/src/a.ts", content: "café\n", truncated: false, encoding: "latin-1", mtime_ms: 1111,
    });
    const { host, handle } = await renderPanel();
    expect(host.querySelector('[data-testid="code-encoding"]')!.textContent).toBe("latin-1");

    act(() => { handle!.view.dispatch({ changes: { from: 0, insert: "// ü\n" } }); });
    await flush();
    pressSave();
    await flush();

    expect(saveFileMock.mock.calls[0][1]).toBe("// ü\ncafé\n");
    expect((saveFileMock.mock.calls[0][3] as { encoding?: string }).encoding).toBe("latin-1");
  });

  it("still saves a utf-8 file as utf-8", async () => {
    readFileMock.mockResolvedValue({
      path: "/w/src/a.ts", content: "café\n", truncated: false, encoding: "utf-8", mtime_ms: 1111,
    });
    const { host, handle } = await renderPanel();
    expect(host.querySelector('[data-testid="code-encoding"]')!.textContent).toBe("utf-8");

    act(() => { handle!.view.dispatch({ changes: { from: 0, insert: "// ü\n" } }); });
    await flush();
    pressSave();
    await flush();

    expect((saveFileMock.mock.calls[0][3] as { encoding?: string }).encoding).toBe("utf-8");
  });
});

describe("CodePanel conflict banner binding", () => {
  it("is hidden for a tab other than the one that conflicted", async () => {
    readFileMock.mockImplementation((path: string) => Promise.resolve({
      path,
      content: path.endsWith("b.ts") ? "const b = 1;\n" : "const a = 1;\n",
      truncated: false,
      mtime_ms: path.endsWith("b.ts") ? 2222 : 1111,
    }));
    const { host, handle, handles, render } = await renderPanel();
    act(() => { handle!.view.dispatch({ changes: { from: 0, insert: "// mine\n" } }); });
    await flush();

    // Another writer changes a.ts while its buffer is dirty: banner for a.ts.
    readFileMock.mockResolvedValueOnce({
      path: "/w/src/a.ts", content: "// outside\nconst a = 1;\n", truncated: false, mtime_ms: 3000,
    });
    await act(async () => { render({ externalChange: externalChange({ seq: 1 }) }); });
    await flush();
    expect(host.querySelector('[data-testid="code-external-change"]')).not.toBeNull();

    // Switching to the clean b.ts must not carry a.ts's banner over it.
    await act(async () => { render({ initialPath: "/w/src/b.ts" }); });
    await waitForEditorContent(handles, "const b = 1;\n");
    expect(host.querySelector('[data-testid="code-external-change"]')).toBeNull();

    // Back on a.ts the banner is still there and its diff shows a.ts's buffer.
    await act(async () => { render({ initialPath: "/w/src/a.ts" }); });
    const editorA = await waitForEditorContent(handles, "// mine\nconst a = 1;\n");
    const banner = host.querySelector('[data-testid="code-external-change"]');
    expect(banner).not.toBeNull();
    const showDiff = Array.from(banner!.querySelectorAll("button")).find((b) => b.textContent === "Show diff")!;
    act(() => { showDiff.click(); });
    await flush();
    expect(document.body.textContent).toContain("Changes in a.ts");
    expect(document.querySelector('[data-testid="code-diff-body"]')).not.toBeNull();
    const keep = Array.from(document.querySelectorAll("button")).find((b) => b.textContent === "Keep my buffer")!;
    act(() => { keep.click(); });
    await flush();

    const reload = Array.from(banner!.querySelectorAll("button")).find((b) => b.textContent === "Reload")!;
    await act(async () => { reload.click(); });
    await flush();

    // Only a.ts adopted the disk copy; b.ts was never touched.
    expect(editorA.getContent()).toBe("// outside\nconst a = 1;\n");
    expect(host.querySelector('[data-testid="code-external-change"]')).toBeNull();
  });

  it("clears the dirty flag after a save that races nothing", async () => {
    readFileMock.mockResolvedValue({
      path: "/w/src/a.ts", content: "const a = 1;\n", truncated: false, mtime_ms: 1111,
    });
    const { handle, states } = await renderPanel();
    act(() => { handle!.view.dispatch({ changes: { from: 0, insert: "// one\n" } }); });
    await flush();
    expect(states[states.length - 1].dirty).toBe(true);

    pressSave();
    await flush();

    expect(states[states.length - 1].dirty).toBe(false);
    expect(states[states.length - 1].openPath).toBe("/w/src/a.ts");
    expect(handle!.getContent()).toBe("// one\nconst a = 1;\n");
  });
});
