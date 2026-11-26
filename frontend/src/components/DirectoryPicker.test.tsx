// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import DirectoryPicker from "./DirectoryPicker";
import type { TreeListing } from "../api/files";

const { getTreeWithRootMock } = vi.hoisted(() => ({
  getTreeWithRootMock: vi.fn(),
}));

vi.mock("../api/files", async () => {
  const actual = await vi.importActual<typeof import("../api/files")>("../api/files");
  return { ...actual, getTreeWithRoot: getTreeWithRootMock };
});

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let host: HTMLElement;

const HOME = "/home/tester";
const DOCS = `${HOME}/Documents`;

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  // The server lists home for the empty path and names what it listed.
  getTreeWithRootMock.mockImplementation(async (path: string = ""): Promise<TreeListing> =>
    path === DOCS
      ? { path: DOCS, entries: [{ name: "notes", path: `${DOCS}/notes`, type: "directory" }] }
      : {
          path: HOME,
          entries: [{ name: "Documents", path: DOCS, type: "directory" }],
        },
  );
});

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = "";
  vi.clearAllMocks();
});

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

function lastRequestedPath(): string | undefined {
  const calls = getTreeWithRootMock.mock.calls;
  return calls[calls.length - 1]?.[0] as string | undefined;
}

function upButton(): HTMLButtonElement {
  const button = document.querySelector<HTMLButtonElement>('button[title="Parent directory"]');
  if (!button) throw new Error("no up button");
  return button;
}

async function renderPicker(onSelect = vi.fn()) {
  act(() => {
    root!.render(<DirectoryPicker open onClose={() => {}} onSelect={onSelect} />);
  });
  await flush();
  return onSelect;
}

describe("DirectoryPicker", () => {
  it("asks for the server's home root instead of listing /", async () => {
    await renderPicker();

    // An empty path is the "home" default; an explicit "/" is forbidden by the API.
    expect(getTreeWithRootMock.mock.calls[0][0]).toBe("");
    expect(getTreeWithRootMock.mock.calls.map((call) => call[0])).not.toContain("/");
    expect(document.body.textContent).toContain("Documents");
  });

  it("disables going up while at the home root", async () => {
    await renderPicker();

    expect(upButton().disabled).toBe(true);
  });

  it("walks into a child, up to home, and stops there", async () => {
    const onSelect = await renderPicker();

    const docsRow = [...document.querySelectorAll("button")].find((button) =>
      button.textContent?.includes("Documents"),
    );
    expect(docsRow).toBeTruthy();
    act(() => {
      docsRow!.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
    });
    await flush();
    expect(lastRequestedPath()).toBe(DOCS);
    expect(upButton().disabled).toBe(false);

    act(() => upButton().dispatchEvent(new MouseEvent("click", { bubbles: true })));
    await flush();
    expect(lastRequestedPath()).toBe(HOME);
    expect(upButton().disabled).toBe(true);

    // One more click at the root must not request the parent of home.
    const callsBefore = getTreeWithRootMock.mock.calls.length;
    act(() => upButton().dispatchEvent(new MouseEvent("click", { bubbles: true })));
    await flush();
    expect(getTreeWithRootMock.mock.calls.length).toBe(callsBefore);

    // Confirming at the root selects the home path the server reported.
    const confirm = [...document.querySelectorAll("button")].find((button) =>
      button.textContent?.includes("Select"),
    );
    expect(confirm).toBeTruthy();
    act(() => confirm!.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    expect(onSelect).toHaveBeenCalledWith(HOME);
  });
});
