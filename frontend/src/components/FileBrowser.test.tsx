// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import FileBrowser from "./FileBrowser";
import type { FileEntry } from "../api/files";

const { getTreeMock, readFileMock, searchFilesMock } = vi.hoisted(() => ({
  getTreeMock: vi.fn(),
  readFileMock: vi.fn(),
  searchFilesMock: vi.fn(),
}));

// Keep the real module (isImageFile etc. are pure) and stub only the network calls.
vi.mock("../api/files", async () => {
  const actual = await vi.importActual<typeof import("../api/files")>("../api/files");
  return { ...actual, getTree: getTreeMock, readFile: readFileMock, searchFiles: searchFilesMock };
});

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let host: HTMLElement;

const ENTRIES: FileEntry[] = [
  { name: "CHANGELOG.md", path: "CHANGELOG.md", type: "file" },
  { name: "small.ts", path: "src/small.ts", type: "file" },
];

const SELECTED_HINT = "1 items; drag into a folder to move";
const IDLE_HINT = "Use Cmd/Ctrl or Shift to multi-select; drag into a folder to move";

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  getTreeMock.mockResolvedValue(ENTRIES);
  readFileMock.mockResolvedValue({ path: "CHANGELOG.md", content: "# Changelog\n\nbody", truncated: false });
  searchFilesMock.mockResolvedValue([]);
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

async function renderBrowser(onOpenDoc = vi.fn()) {
  act(() => {
    root!.render(
      <FileBrowser collapsed={false} onToggle={() => {}} sessionId={null} onOpenDoc={onOpenDoc} />,
    );
  });
  await flush();
  return onOpenDoc;
}

function row(path: string): HTMLElement {
  const element = host.querySelector(`[title="${path}"]`);
  expect(element).not.toBeNull();
  return element as HTMLElement;
}

function clickRow(path: string) {
  const element = row(path);
  act(() => { element.click(); });
}

function doubleClickRow(path: string) {
  const element = row(path);
  act(() => { element.dispatchEvent(new MouseEvent("dblclick", { bubbles: true })); });
}

describe("FileBrowser single-click selection", () => {
  it("highlights the clicked file without reading it or showing a preview", async () => {
    await renderBrowser();

    clickRow("CHANGELOG.md");
    await flush();

    // No preview panel: no copy button, no file body, no read.
    expect(host.querySelector('button[title="Copy all content"]')).toBeNull();
    expect(host.textContent).not.toContain("Copy all");
    expect(host.querySelector("pre")).toBeNull();
    expect(readFileMock).not.toHaveBeenCalled();

    // Still selected: the row uses the brand highlight and the header counts it.
    expect(row("CHANGELOG.md").className).toContain("bg-[var(--brand-bg)]");
    expect(row("CHANGELOG.md").className).toContain("text-[var(--brand)]");
    expect(row("src/small.ts").className).not.toContain("bg-[var(--brand-bg)]");
    expect(host.textContent).toContain(SELECTED_HINT);
  });

  it("keeps no preview after switching the selection to another file", async () => {
    await renderBrowser();

    clickRow("CHANGELOG.md");
    await flush();
    clickRow("src/small.ts");
    await flush();

    expect(row("src/small.ts").className).toContain("bg-[var(--brand-bg)]");
    expect(row("CHANGELOG.md").className).not.toContain("bg-[var(--brand-bg)]");
    expect(host.querySelector("pre")).toBeNull();
    expect(host.textContent).not.toContain("Copy all");
    expect(readFileMock).not.toHaveBeenCalled();
  });

  it("clears the selection when the selected row is clicked again", async () => {
    await renderBrowser();

    clickRow("CHANGELOG.md");
    await flush();
    expect(host.textContent).toContain(SELECTED_HINT);

    clickRow("CHANGELOG.md");
    await flush();

    expect(row("CHANGELOG.md").className).not.toContain("bg-[var(--brand-bg)]");
    expect(host.textContent).toContain(IDLE_HINT);
    expect(readFileMock).not.toHaveBeenCalled();
  });
});

describe("FileBrowser keeps opening files", () => {
  it("opens a file on double-click instead of previewing it", async () => {
    const onOpenDoc = await renderBrowser();

    doubleClickRow("src/small.ts");
    await flush();

    expect(onOpenDoc).toHaveBeenCalledTimes(1);
    expect(onOpenDoc).toHaveBeenCalledWith("src/small.ts", "small.ts");
    expect(readFileMock).not.toHaveBeenCalled();
    expect(host.querySelector("pre")).toBeNull();
  });

  it("opens a file from the hover Open in workspace action", async () => {
    const onOpenDoc = await renderBrowser();

    const action = row("src/small.ts").querySelector("button[title='Open in workspace']");
    expect(action).not.toBeNull();
    act(() => { (action as HTMLButtonElement).click(); });
    await flush();

    expect(onOpenDoc).toHaveBeenCalledTimes(1);
    expect(onOpenDoc).toHaveBeenCalledWith("src/small.ts", "small.ts");
    expect(readFileMock).not.toHaveBeenCalled();
  });
});
