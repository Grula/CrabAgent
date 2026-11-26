// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ChatInput from "./ChatInput";

const props = { replaying: false, onSend: () => {}, onAbort: () => {}, onFileUpload: () => {}, onFilePaste: () => {}, onDelegateOpen: () => {} };

describe("chat composer card", () => {
  it("puts the text box and every control in one card", () => {
    const html = renderToStaticMarkup(
      <ChatInput {...props} sending={false} showDelegate onGoalOpen={() => {}} toolbar={<span data-picker="model" />} attachments={<span data-attachment="a.png" />} />,
    );
    const card = new DOMParser().parseFromString(html, "text/html").querySelector(".composer")!;
    expect(card).not.toBeNull();
    expect(card.querySelector("textarea.composer__input")).not.toBeNull();
    for (const label of ["Attach file", "Create Goal", "Delegate to agent team", "Send"]) {
      expect(card.querySelector(`[aria-label="${label}"]`)).not.toBeNull();
    }
    expect(card.querySelector('[data-picker="model"]')).not.toBeNull();
    // Attachments sit above the text box
    expect(card.firstElementChild!.getAttribute("data-attachment")).toBe("a.png");
  });

  it("exposes stop while sending", () => {
    const html = renderToStaticMarkup(<ChatInput {...props} sending />);
    expect(html).toContain('aria-label="Stop"');
  });
});

describe("chat composer behaviour", () => {
  let container: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });
  afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

  it("fills the box from a starter prompt and sends it", async () => {
    const send = vi.fn();
    await act(async () => root.render(<ChatInput {...props} sending={false} onSend={send} seed={{ text: "Brainstorm ideas for ", key: 1 }} />));
    const textarea = container.querySelector("textarea")!;
    expect(textarea.value).toBe("Brainstorm ideas for ");
    await act(async () => container.querySelector<HTMLButtonElement>('[aria-label="Send"]')!.click());
    expect(send).toHaveBeenCalledWith("Brainstorm ideas for");
  });

  it("keeps each chat's unsent text with that chat", async () => {
    const send = vi.fn();
    const render = (key: string, seed?: { text: string; key: number }) =>
      act(async () => root.render(<ChatInput {...props} sending={false} onSend={send} draftKey={key} seed={seed} />));
    const box = () => container.querySelector("textarea")!;

    await render("chat-a", { text: "half-written for A", key: 1 });
    await render("chat-b");
    expect(box().value).toBe("");  // A's draft didn't follow to B
    await render("chat-b", { text: "B's own", key: 2 });
    await render("chat-a");
    expect(box().value).toBe("half-written for A");
    await render("chat-b");
    expect(box().value).toBe("B's own");

    await act(async () => container.querySelector<HTMLButtonElement>('[aria-label="Send"]')!.click());
    expect(send).toHaveBeenCalledWith("B's own");
    await render("chat-a");
    await render("chat-b");
    expect(box().value).toBe("");  // sent: the draft is gone
  });

  it("allows sending attachments without text only when asked to", async () => {
    const send = vi.fn();
    await act(async () => root.render(<ChatInput {...props} sending={false} onSend={send} />));
    expect(container.querySelector<HTMLButtonElement>('[aria-label="Send"]')!.disabled).toBe(true);
    await act(async () => root.render(<ChatInput {...props} sending={false} onSend={send} canSendEmpty />));
    const button = container.querySelector<HTMLButtonElement>('[aria-label="Send"]')!;
    expect(button.disabled).toBe(false);
    await act(async () => button.click());
    expect(send).toHaveBeenCalledWith("");
  });
});
