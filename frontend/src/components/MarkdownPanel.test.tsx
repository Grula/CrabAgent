// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MarkdownPanel } from "./MarkdownPanel";
import { ApiError } from "../api/client";

const { readFileMock, saveFileMock } = vi.hoisted(() => ({
  readFileMock: vi.fn(),
  saveFileMock: vi.fn(),
}));

// Keep the real module (isImageFile etc. are pure) and stub only the network call.
vi.mock("../api/files", async () => {
  const actual = await vi.importActual<typeof import("../api/files")>("../api/files");
  return { ...actual, readFile: readFileMock, saveFile: saveFileMock };
});

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let host: HTMLElement;

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  readFileMock.mockResolvedValue({
    path: "notes.md",
    content: "# Hello\n\nbody",
    truncated: false,
  });
});

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = "";
  vi.clearAllMocks();
});

async function flush() {
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
}

async function render(props: {
  filePath: string;
  sessionId?: string;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  act(() => { root!.render(<MarkdownPanel {...props} />); });
  await flush();
}

/** The only readFile call of this render. */
function call() {
  expect(readFileMock).toHaveBeenCalledTimes(1);
  return readFileMock.mock.calls[0];
}

describe("MarkdownPanel file reads", () => {
  it("reads an absolute path with absolute=true and the session id", async () => {
    await render({ filePath: "/tmp/x/notes.md", sessionId: "42" });
    const [path, absolute, options] = call();
    expect(path).toBe("/tmp/x/notes.md");
    expect(absolute).toBe(true);
    expect(options).toEqual({ sessionId: "42" });
    // The body really landed in the editor, not just the request.
    expect((host.querySelector("textarea") as HTMLTextAreaElement).value).toContain("# Hello");
  });

  it("reads a relative path with absolute=false and the session id", async () => {
    await render({ filePath: "notes.md", sessionId: "42" });
    const [path, absolute, options] = call();
    expect(path).toBe("notes.md");
    expect(absolute).toBe(false);
    expect(options).toEqual({ sessionId: "42" });
  });

  it("omits the session id when the caller passes none", async () => {
    await render({ filePath: "notes.md" });
    const [path, absolute, options] = call();
    expect(path).toBe("notes.md");
    expect(absolute).toBe(false);
    // Nothing that would serialize as session_id=undefined.
    expect(options?.sessionId).toBeUndefined();
    expect(Object.values(options ?? {})).not.toContain("undefined");
    expect((host.querySelector("textarea") as HTMLTextAreaElement).value).toContain("# Hello");
  });

  it("shows the error state when readFile rejects", async () => {
    readFileMock.mockRejectedValue(new Error("File not found"));
    await render({ filePath: "/tmp/x/missing.md", sessionId: "42" });
    // A 404 is not swallowed: the existing status bar surfaces it, the spinner is gone
    // and the editor stays empty instead of holding stale content.
    expect(host.textContent).toContain("File not found");
    expect((host.querySelector("textarea") as HTMLTextAreaElement).value).toBe("");
  });
});

/** Type into the textarea the way a user would, so React's onChange fires. */
async function type(value: string) {
  const textarea = host.querySelector("textarea") as HTMLTextAreaElement;
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
  await act(async () => {
    setter?.call(textarea, value);
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

/** The Save button only exists while the buffer is dirty. */
function saveButton() {
  const btn = Array.from(host.querySelectorAll("button")).find(
    (b) => (b.textContent || "").trim() === "Save",
  );
  expect(btn).toBeTruthy();
  return btn as HTMLButtonElement;
}

async function save() {
  await act(async () => { saveButton().click(); });
  await flush();
}

/** The only saveFile call of this render. */
function saveCall() {
  expect(saveFileMock).toHaveBeenCalledTimes(1);
  return saveFileMock.mock.calls[0];
}

describe("MarkdownPanel file saves", () => {
  beforeEach(() => {
    saveFileMock.mockResolvedValue({ status: "ok", path: "notes.md", size: 10 });
  });

  it("saves an absolute path with absolute=true and the session id", async () => {
    await render({ filePath: "/tmp/x/notes.md", sessionId: "42" });
    await type("# Hello\n\nbody edited");
    await save();
    const [path, content, absolute, options] = saveCall();
    expect(path).toBe("/tmp/x/notes.md");
    expect(content).toBe("# Hello\n\nbody edited");
    expect(absolute).toBe(true);
    expect(options).toEqual({ sessionId: "42" });
    // The status bar leaves the modified state once the save lands.
    expect(host.textContent).not.toContain("Modified");
  });

  it("saves a relative path with absolute=false and the session id", async () => {
    await render({ filePath: "notes.md", sessionId: "42" });
    await type("# Hello\n\nbody edited");
    await save();
    const [path, , absolute, options] = saveCall();
    expect(path).toBe("notes.md");
    expect(absolute).toBe(false);
    expect(options).toEqual({ sessionId: "42" });
  });

  it("omits the session id when the caller passes none", async () => {
    await render({ filePath: "notes.md" });
    await type("# Hello\n\nbody edited");
    await save();
    const [, , absolute, options] = saveCall();
    expect(absolute).toBe(false);
    // Nothing that would serialize as session_id=undefined.
    expect(options?.sessionId).toBeUndefined();
    expect(Object.values(options ?? {})).not.toContain("undefined");
  });

  it("shows the error state when saveFile rejects", async () => {
    saveFileMock.mockRejectedValue(new Error("Disk full"));
    await render({ filePath: "notes.md", sessionId: "42" });
    await type("# Hello\n\nbody edited");
    await save();
    expect(host.textContent).toContain("Disk full");
    // Still dirty, so the user can retry.
    expect(host.textContent).toContain("Modified");
  });
});

/** The Save button, when one is rendered at all. */
function findSaveButton() {
  return Array.from(host.querySelectorAll("button")).find(
    (b) => (b.textContent || "").trim() === "Save",
  ) as HTMLButtonElement | undefined;
}

async function pressSaveShortcut() {
  await act(async () => {
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "s", ctrlKey: true }));
  });
  await flush();
}

describe("MarkdownPanel read-only files", () => {
  it("never edits or saves a truncated file", async () => {
    readFileMock.mockResolvedValue({
      path: "big.md",
      content: "# huge\n...",
      truncated: true,
      binary: false,
      encoding: "utf-8",
      mtime_ms: 1000,
    });
    saveFileMock.mockResolvedValue({ status: "ok", path: "big.md", size: 10 });
    await render({ filePath: "big.md", sessionId: "42" });
    // The (partial) content is still shown, with a notice explaining why it cannot be saved.
    expect(host.querySelector('[data-testid="markdown-truncated-notice"]')).toBeTruthy();
    expect(host.textContent).toContain("read-only");
    const textarea = host.querySelector("textarea") as HTMLTextAreaElement;
    expect(textarea.value).toBe("# huge\n...");
    expect(textarea.readOnly).toBe(true);
    await type("# huge\n... overwritten");
    // No save control appears, and Cmd/Ctrl+S cannot write the truncated buffer.
    expect(findSaveButton()).toBeUndefined();
    await pressSaveShortcut();
    expect(saveFileMock).not.toHaveBeenCalled();
  });

  it("never edits or saves a binary file", async () => {
    readFileMock.mockResolvedValue({
      path: "blob.md",
      content: "",
      truncated: false,
      binary: true,
      encoding: "utf-8",
      mtime_ms: 5,
    });
    saveFileMock.mockResolvedValue({ status: "ok", path: "blob.md", size: 1 });
    await render({ filePath: "blob.md", sessionId: "42" });
    // A clear notice replaces the editor: there is nothing to type into or save.
    expect(host.querySelector('[data-testid="markdown-binary"]')).toBeTruthy();
    expect(host.textContent).toContain("read-only");
    expect(host.querySelector("textarea")).toBeNull();
    expect(findSaveButton()).toBeUndefined();
    await pressSaveShortcut();
    expect(saveFileMock).not.toHaveBeenCalled();
  });
});

describe("MarkdownPanel save metadata", () => {
  it("sends the mtime and encoding from the read on save", async () => {
    readFileMock.mockResolvedValue({
      path: "latin.md",
      content: "caf\u00e9",
      truncated: false,
      binary: false,
      encoding: "latin-1",
      mtime_ms: 1234,
    });
    saveFileMock.mockResolvedValue({ status: "ok", path: "latin.md", size: 4, mtime_ms: 5678 });
    await render({ filePath: "latin.md", sessionId: "42" });
    await type("caf\u00e9 edited");
    await save();
    const [, , , options] = saveCall();
    expect(options).toEqual({ sessionId: "42", expectedMtimeMs: 1234, encoding: "latin-1" });
    // The written file's mtime becomes the expectation of the next save.
    await type("caf\u00e9 edited twice");
    await act(async () => { saveButton().click(); });
    await flush();
    expect(saveFileMock).toHaveBeenCalledTimes(2);
    expect(saveFileMock.mock.calls[1][3]).toEqual({
      sessionId: "42",
      expectedMtimeMs: 5678,
      encoding: "latin-1",
    });
  });

  it("still loads, edits and saves a normal utf-8 file", async () => {
    readFileMock.mockResolvedValue({
      path: "notes.md",
      content: "# Hello",
      truncated: false,
      binary: false,
      encoding: "utf-8",
      mtime_ms: 100,
    });
    saveFileMock.mockResolvedValue({ status: "ok", path: "notes.md", size: 12, mtime_ms: 200 });
    await render({ filePath: "notes.md", sessionId: "42" });
    const textarea = host.querySelector("textarea") as HTMLTextAreaElement;
    expect(textarea.value).toBe("# Hello");
    expect(textarea.readOnly).toBe(false);
    await type("# Hello\n\nmore");
    await save();
    const [, content, , options] = saveCall();
    expect(content).toBe("# Hello\n\nmore");
    expect(options).toEqual({ sessionId: "42", expectedMtimeMs: 100, encoding: "utf-8" });
    expect(host.textContent).not.toContain("Modified");
  });
});

describe("MarkdownPanel stale save (409)", () => {
  it("shows the conflict UI and never overwrites the buffer", async () => {
    readFileMock
      .mockResolvedValueOnce({
        path: "notes.md",
        content: "# Hello\n\nbody",
        truncated: false,
        binary: false,
        encoding: "utf-8",
        mtime_ms: 111,
      })
      // The disk copy read while handling the 409.
      .mockResolvedValueOnce({
        path: "notes.md",
        content: "# Agent edit",
        truncated: false,
        binary: false,
        encoding: "utf-8",
        mtime_ms: 222,
      });
    saveFileMock.mockRejectedValue(new ApiError("File changed on disk", 409, {}));
    await render({ filePath: "notes.md", sessionId: "42" });
    await type("# Hello\n\nbody edited");
    await save();
    // The buffer is untouched and still dirty: no silent overwrite, no auto-merge.
    expect((host.querySelector("textarea") as HTMLTextAreaElement).value).toBe("# Hello\n\nbody edited");
    expect(host.textContent).toContain("Modified");
    expect(host.textContent).not.toContain("# Agent edit");
    const banner = host.querySelector('[data-testid="markdown-conflict"]');
    expect(banner).toBeTruthy();
    expect(banner!.textContent).toContain("Reload");
    expect(banner!.textContent).toContain("Keep mine");
    expect(readFileMock).toHaveBeenCalledTimes(2);
    // Reload adopts the disk copy and clears the dirty flag.
    const reload = Array.from(banner!.querySelectorAll("button")).find(
      (b) => (b.textContent || "").trim() === "Reload",
    ) as HTMLButtonElement;
    await act(async () => { reload.click(); });
    expect((host.querySelector("textarea") as HTMLTextAreaElement).value).toBe("# Agent edit");
    expect(host.textContent).not.toContain("Modified");
    expect(host.querySelector('[data-testid="markdown-conflict"]')).toBeNull();
  });
});

describe("MarkdownPanel save race", () => {
  it("keeps text typed during an in-flight save dirty and on screen", async () => {
    // The write stays pending until we resolve it, so an edit can land mid-save.
    let resolveSave: (value: unknown) => void = () => {};
    saveFileMock.mockImplementation(() => new Promise((resolve) => { resolveSave = resolve; }));
    const onDirtyChange = vi.fn();
    await render({ filePath: "notes.md", sessionId: "42", onDirtyChange });
    await type("# Hello\n\nfirst edit");
    // Click Save without letting the promise settle.
    await act(async () => { saveButton().click(); });
    expect(host.textContent).toContain("Saving...");
    // Keep typing while the write is in flight.
    await type("# Hello\n\nfirst edit and more");
    await act(async () => {
      resolveSave({ status: "ok", path: "notes.md", size: 20, mtime_ms: 500 });
      await new Promise((r) => setTimeout(r, 0));
    });
    // The newer text is still there and still dirty: the save wrote the older one.
    expect((host.querySelector("textarea") as HTMLTextAreaElement).value).toBe("# Hello\n\nfirst edit and more");
    expect(host.textContent).toContain("Modified");
    expect(onDirtyChange).toHaveBeenLastCalledWith(true);
    // Dirty really means dirty: the next Ctrl/Cmd+S writes the newer text again.
    saveFileMock.mockResolvedValue({ status: "ok", path: "notes.md", size: 30, mtime_ms: 600 });
    await pressSaveShortcut();
    expect(saveFileMock).toHaveBeenCalledTimes(2);
    expect(saveFileMock.mock.calls[1][1]).toBe("# Hello\n\nfirst edit and more");
  });

  it("clears the dirty flag after a save nobody typed over", async () => {
    saveFileMock.mockResolvedValue({ status: "ok", path: "notes.md", size: 10, mtime_ms: 700 });
    await render({ filePath: "notes.md", sessionId: "42" });
    await type("# Hello\n\nbody edited");
    await save();
    expect((host.querySelector("textarea") as HTMLTextAreaElement).value).toBe("# Hello\n\nbody edited");
    expect(host.textContent).not.toContain("Modified");
  });
});

describe("MarkdownPanel dirty reporting", () => {
  it("reports dirty on the first edit and clean after a successful save", async () => {
    saveFileMock.mockResolvedValue({ status: "ok", path: "notes.md", size: 10, mtime_ms: 999 });
    const onDirtyChange = vi.fn();
    await render({ filePath: "notes.md", sessionId: "42", onDirtyChange });
    expect(onDirtyChange).toHaveBeenLastCalledWith(false);
    await type("# Hello\n\nbody edited");
    expect(onDirtyChange).toHaveBeenLastCalledWith(true);
    await save();
    expect(onDirtyChange).toHaveBeenLastCalledWith(false);
    expect(onDirtyChange.mock.calls.map((c) => c[0])).toEqual([false, true, false]);
  });
});
