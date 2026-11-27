// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { CodeBlock } from "./CodeBlock";

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const code = Array.from({ length: 40 }, (_, i) => `echo ${i + 1}`).join("\n");
const outerCode = () => container.querySelector("pre > code") as HTMLElement;
const toggle = () =>
  Array.from(container.querySelectorAll("button")).find((b) => /more lines|Collapse/.test(b.textContent ?? "")) as HTMLButtonElement;

describe("CodeBlock folding", () => {
  // Chat Markdown passes highlighted <code> as children; folding used to only
  // flip the button label while every line stayed on screen.
  it("folds and unfolds highlighted children, not only plain code", () => {
    act(() => {
      root.render(
        <CodeBlock language="bash" code={code}>
          <code className="hljs language-bash">{code}</code>
        </CodeBlock>,
      );
    });
    expect(toggle().textContent).toContain("Show 20 more lines");
    const folded = outerCode().style.maxHeight;
    expect(folded).toContain("28px"); // 20 lines + padding (jsdom rewrites the calc)
    expect(outerCode().style.overflowY).toBe("hidden");

    act(() => toggle().click());
    expect(toggle().textContent).toContain("Collapse");
    expect(outerCode().style.maxHeight).toBe("");

    act(() => toggle().click());
    expect(outerCode().style.maxHeight).toBe(folded);
  });

  it("leaves short blocks alone", () => {
    act(() => root.render(<CodeBlock language="bash" code={"echo 1\necho 2"} />));
    expect(outerCode().style.maxHeight).toBe("");
    expect(container.textContent).not.toContain("more lines");
  });
});
