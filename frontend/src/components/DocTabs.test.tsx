// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DocTabs, type DocTab } from "./DocTabs";

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

function renderTabs(tabs: DocTab[]): { onSelect: ReturnType<typeof vi.fn>; onClose: ReturnType<typeof vi.fn> } {
  const onSelect = vi.fn();
  const onClose = vi.fn();
  act(() => root!.render(<DocTabs tabs={tabs} onSelect={onSelect} onClose={onClose} />));
  return { onSelect, onClose };
}

function chips(): HTMLElement[] {
  return Array.from(host.querySelectorAll<HTMLElement>('[data-testid="doc-tab"]'));
}

const tabs: DocTab[] = [
  { path: "src/small.ts", name: "small.ts", kind: "code", active: false },
  { path: "docs/note.md", name: "note.md", kind: "doc", active: true },
];

describe("DocTabs", () => {
  it("renders one chip per tab, titled with the full path", () => {
    renderTabs(tabs);

    expect(host.querySelector('[data-testid="doc-tabs"]')).not.toBeNull();
    expect(chips().map((c) => c.textContent)).toEqual(["small.ts", "note.md"]);
    expect(chips().map((c) => c.getAttribute("title"))).toEqual(["src/small.ts", "docs/note.md"]);
  });

  it("gives the code chip no close button", () => {
    renderTabs(tabs);

    const [codeChip, docChip] = chips();
    expect(codeChip.querySelector("button")).toBeNull();
    expect(docChip.querySelector("button")).not.toBeNull();
  });

  it("marks the active chip", () => {
    renderTabs(tabs);

    const [codeChip, docChip] = chips();
    expect(codeChip.getAttribute("data-active")).toBe("false");
    expect(docChip.getAttribute("data-active")).toBe("true");
  });

  it("selects the clicked chip's path", () => {
    const { onSelect, onClose } = renderTabs(tabs);

    act(() => { chips()[0].click(); });
    expect(onSelect).toHaveBeenCalledWith("src/small.ts");
    expect(onClose).not.toHaveBeenCalled();
  });

  it("closes a document chip without also selecting it", () => {
    const { onSelect, onClose } = renderTabs(tabs);

    const close = chips()[1].querySelector("button")!;
    act(() => { close.click(); });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledWith("docs/note.md");
    expect(onSelect).not.toHaveBeenCalled();
  });
});
