// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MarkdownPanel } from "./MarkdownPanel";

const { readFileMock } = vi.hoisted(() => ({ readFileMock: vi.fn() }));

// Keep the real module (isImageFile etc. are pure) and stub only the network call.
vi.mock("../api/files", async () => {
  const actual = await vi.importActual<typeof import("../api/files")>("../api/files");
  return { ...actual, readFile: readFileMock, saveFile: vi.fn() };
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

async function render(props: { filePath: string; sessionId?: string }) {
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
