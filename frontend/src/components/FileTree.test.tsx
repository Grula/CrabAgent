// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import FileTree from "./FileTree";
import type { FileEntry } from "../api/files";

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
});

function entry(name: string, path: string): FileEntry {
  return { name, path, type: "file" };
}

function renderTree(entries: FileEntry[], onOpenDoc = vi.fn()) {
  act(() => root!.render(
    <FileTree entries={entries} onSelect={() => {}} selectedPath={null} onOpenDoc={onOpenDoc} />,
  ));
  return onOpenDoc;
}

function openAction(path: string): HTMLButtonElement | null {
  const row = host.querySelector(`[title="${path}"]`);
  return (row?.querySelector("button[title='Open in workspace']") as HTMLButtonElement | null) ?? null;
}

function doubleClickRow(path: string) {
  const row = host.querySelector(`[title="${path}"]`);
  act(() => { row!.dispatchEvent(new MouseEvent("dblclick", { bubbles: true })); });
}

describe("FileTree open-in-workspace action", () => {
  it("offers the action for code files and hands the path to the caller", () => {
    const onOpenDoc = renderTree([
      entry("small.ts", "src/small.ts"),
      entry("app.py", "src/app.py"),
      entry("package.json", "package.json"),
    ]);

    const action = openAction("src/small.ts");
    expect(action).not.toBeNull();
    expect(openAction("src/app.py")).not.toBeNull();
    expect(openAction("package.json")).not.toBeNull();

    act(() => { action!.click(); });
    expect(onOpenDoc).toHaveBeenCalledWith("src/small.ts", "small.ts");
  });

  it("keeps offering the action for markdown and office files", () => {
    renderTree([
      entry("note.md", "note.md"),
      entry("report.docx", "report.docx"),
    ]);
    expect(openAction("note.md")).not.toBeNull();
    expect(openAction("report.docx")).not.toBeNull();
  });

  it("does not offer the action for files no panel can open", () => {
    renderTree([
      entry("logo.png", "logo.png"),
      entry("archive.zip", "archive.zip"),
    ]);
    expect(openAction("logo.png")).toBeNull();
    expect(openAction("archive.zip")).toBeNull();
  });

  it("opens a code file when its row is double-clicked", () => {
    const onOpenDoc = renderTree([
      entry("small.ts", "src/small.ts"),
      entry("app.py", "src/app.py"),
    ]);

    doubleClickRow("src/small.ts");
    expect(onOpenDoc).toHaveBeenCalledTimes(1);
    expect(onOpenDoc).toHaveBeenCalledWith("src/small.ts", "small.ts");
  });

  it("does not open a file no panel can open on double-click", () => {
    const onOpenDoc = renderTree([entry("logo.png", "logo.png")]);

    doubleClickRow("logo.png");
    expect(onOpenDoc).not.toHaveBeenCalled();
  });

  it("opens the file exactly once from the hover action", () => {
    const onOpenDoc = renderTree([entry("small.ts", "src/small.ts")]);

    act(() => { openAction("src/small.ts")!.click(); });
    expect(onOpenDoc).toHaveBeenCalledTimes(1);
    expect(onOpenDoc).toHaveBeenCalledWith("src/small.ts", "small.ts");
  });
});
