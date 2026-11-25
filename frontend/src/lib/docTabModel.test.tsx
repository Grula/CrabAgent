// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DocTabs } from "../components/DocTabs";
import {
  buildDocTabItems,
  editorReportUpdate,
  fallbackAfterClose,
  fileBaseName,
  type OpenDocRef,
} from "./docTabModel";
import { workspaceFileKind } from "./workspaceFiles";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let host: HTMLElement;

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = "";
  vi.clearAllMocks();
});

/** What ChatPage's `codeActive` derives from: is the active path a code file? */
function isCodeActive(currentDocPath: string | null): boolean {
  return currentDocPath !== null && workspaceFileKind(fileBaseName(currentDocPath)) === "code";
}

/** The strip as ChatPage renders it: nothing at all when there is no item. */
function renderStrip(items: ReturnType<typeof buildDocTabItems>): void {
  act(() => root!.render(
    items.length > 0
      ? <DocTabs tabs={items} onSelect={() => {}} onClose={() => {}} />
      : <div data-testid="no-strip" />,
  ));
}

function chips(): HTMLElement[] {
  return Array.from(host.querySelectorAll<HTMLElement>('[data-testid="doc-tab"]'));
}

const docs: OpenDocRef[] = [
  { path: "docs/readme.md", name: "readme.md" },
  { path: "docs/guide.md", name: "guide.md" },
  { path: "site/index.html", name: "index.html" },
];

describe("Documents tab model: the code chip follows the editor's tabs", () => {
  it("drops the code chip once every code tab is closed", () => {
    // CodePanel reports `openPath: null`: no tab left behind the chip.
    const update = editorReportUpdate({ openPath: null }, { codeActive: true });

    expect(update.lastCodePath).toBeNull();
    expect(update.activePath).toBeNull();
    const items = buildDocTabItems({
      codeStarted: true,
      lastCodePath: update.lastCodePath ?? null,
      codeActive: isCodeActive(update.activePath ?? null),
      openDocs: [],
      currentDocPath: null,
    });
    expect(items).toEqual([]);
    renderStrip(items);
    expect(host.querySelector('[data-testid="doc-tabs"]')).toBeNull();
    expect(chips().some((chip) => chip.dataset.kind === "code")).toBe(false);
  });

  it("re-opens the chip from a file tree click after every tab was closed", () => {
    // Opening a code file always sets both, so a chip with no tab cannot come back.
    const items = buildDocTabItems({
      codeStarted: true,
      lastCodePath: "src/app.ts",
      codeActive: isCodeActive("src/app.ts"),
      openDocs: [],
      currentDocPath: "src/app.ts",
    });
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ kind: "code", name: "app.ts", active: true });
    renderStrip(items);
    expect(chips()).toHaveLength(1);
  });

  it("keeps the chip while its tab only hides behind an active document", () => {
    // The editor is not the visible panel: the report must not steal the path.
    const update = editorReportUpdate({ openPath: "src/app.ts" }, { codeActive: false });
    expect(update.activePath).toBeUndefined();

    const items = buildDocTabItems({
      codeStarted: true,
      lastCodePath: "src/app.ts",
      codeActive: isCodeActive("docs/readme.md"),
      openDocs: [docs[0]],
      currentDocPath: "docs/readme.md",
    });
    renderStrip(items);
    const code = chips().filter((chip) => chip.dataset.kind === "code");
    expect(code).toHaveLength(1);
    expect(code[0].dataset.active).toBe("false");
    expect(chips().find((chip) => chip.dataset.kind === "doc")?.dataset.active).toBe("true");
  });

  it("clears the chip without touching the document when the last tab closes behind it", () => {
    const update = editorReportUpdate({ openPath: null }, { codeActive: false });
    expect(update.lastCodePath).toBeNull();
    expect(update.activePath).toBeUndefined();

    const items = buildDocTabItems({
      codeStarted: true,
      lastCodePath: update.lastCodePath ?? null,
      codeActive: isCodeActive("docs/readme.md"),
      openDocs: [docs[0]],
      currentDocPath: "docs/readme.md",
    });
    expect(items.map((item) => item.kind)).toEqual(["doc"]);
    expect(items[0].active).toBe(true);
  });
});

describe("Documents tab model: closing the visible document falls back", () => {
  it("hands the area to the code panel while it still has a tab", () => {
    const fallback = fallbackAfterClose({
      openDocs: [docs[0], docs[1]],
      closedPath: "docs/readme.md",
      codePath: "src/app.ts",
    });

    expect(fallback).toEqual({ kind: "code", path: "src/app.ts" });
    // ChatPage sets its path: that is what makes the code panel visible.
    const currentDocPath = fallback?.kind === "code" ? fallback.path : null;
    expect(isCodeActive(currentDocPath)).toBe(true);
    const items = buildDocTabItems({
      codeStarted: true,
      lastCodePath: "src/app.ts",
      codeActive: isCodeActive(currentDocPath),
      openDocs: [docs[1]],
      currentDocPath,
    });
    expect(items.find((item) => item.kind === "code")?.active).toBe(true);
    expect(items.find((item) => item.kind === "doc")).toMatchObject({ name: "guide.md", active: false });
  });

  it("falls back to the neighbouring document when the code panel has no tab", () => {
    const middle = fallbackAfterClose({
      openDocs: docs,
      closedPath: "docs/guide.md",
      codePath: null,
    });
    expect(middle).toEqual({ kind: "doc", path: "site/index.html", name: "index.html" });

    // The last tab has no right neighbour: the one before it takes over.
    const last = fallbackAfterClose({ openDocs: docs, closedPath: "site/index.html", codePath: null });
    expect(last).toEqual({ kind: "doc", path: "docs/guide.md", name: "guide.md" });

    // A path that is not in the list never yields an undefined chip.
    expect(fallbackAfterClose({ openDocs: docs, closedPath: "docs/gone.md", codePath: null }))
      .toEqual({ kind: "doc", path: "docs/readme.md", name: "readme.md" });
  });

  it("does not hand over to the code chip it just closed", () => {
    expect(fallbackAfterClose({
      openDocs: [docs[0]],
      closedPath: "src/app.ts",
      codePath: "src/app.ts",
    })).toEqual({ kind: "doc", path: "docs/readme.md", name: "readme.md" });
  });

  it("leaves the empty state when the last document and the last code tab close", () => {
    expect(fallbackAfterClose({ openDocs: [docs[0]], closedPath: "docs/readme.md", codePath: null }))
      .toBeNull();
    // closeActiveDoc with nothing open and nothing cached: no crash, nothing shown.
    expect(fallbackAfterClose({ openDocs: [], closedPath: null, codePath: null })).toBeNull();

    renderStrip(buildDocTabItems({
      codeStarted: true,
      lastCodePath: null,
      codeActive: false,
      openDocs: [],
      currentDocPath: null,
    }));
    expect(host.querySelector('[data-testid="doc-tabs"]')).toBeNull();
    expect(chips()).toHaveLength(0);
  });
});
