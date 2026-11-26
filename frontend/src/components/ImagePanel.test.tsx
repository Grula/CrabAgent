// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ImagePanel } from "./ImagePanel";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let host: HTMLElement;

beforeEach(() => {
  localStorage.setItem("crab_token", "tok-123");
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = "";
  localStorage.clear();
  vi.restoreAllMocks();
});

function renderPanel(props: { path: string; name: string; absolute?: boolean }) {
  act(() => root!.render(<ImagePanel {...props} />));
}

function image(): HTMLImageElement {
  const img = host.querySelector("img");
  expect(img).not.toBeNull();
  return img as HTMLImageElement;
}

/** Parse the panel's src the way the browser would, without mistaking `+` for a space. */
function srcParams(img: HTMLImageElement): URLSearchParams {
  const src = img.getAttribute("src") || "";
  expect(src.startsWith("/api/files/image?")).toBe(true);
  return new URLSearchParams(src.slice(src.indexOf("?") + 1));
}

describe("ImagePanel", () => {
  it("points the <img> at the files image endpoint with the path encoded", () => {
    renderPanel({ path: "images/my logo.png", name: "my logo.png", absolute: true });

    const img = image();
    const params = srcParams(img);
    expect(params.get("path")).toBe("images/my logo.png");
    expect(params.get("absolute")).toBe("true");
    expect(params.get("token")).toBe("tok-123");
    expect(img.getAttribute("alt")).toBe("my logo.png");
  });

  it("shows the loading state until the image loads", () => {
    renderPanel({ path: "logo.png", name: "logo.png" });

    expect(host.textContent).toContain("Loading image");

    act(() => { image().dispatchEvent(new Event("load", { bubbles: true })); });

    expect(host.textContent).not.toContain("Loading image");
    expect(host.querySelector("img")).not.toBeNull();
  });

  it("shows a short message instead of a broken image when the load fails", () => {
    renderPanel({ path: "gone.png", name: "gone.png" });

    act(() => { image().dispatchEvent(new Event("error", { bubbles: true })); });

    expect(host.textContent).toContain("Could not load this image.");
    expect(host.querySelector("img")).toBeNull();
  });

  it("opens the same URL full size in a new tab on click", () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    renderPanel({ path: "photos/cat.jpg", name: "cat.jpg" });

    const img = image();
    const src = img.getAttribute("src");
    act(() => { img.dispatchEvent(new MouseEvent("click", { bubbles: true })); });

    expect(open).toHaveBeenCalledTimes(1);
    expect(open.mock.calls[0][0]).toBe(src);
    expect(open.mock.calls[0][1]).toBe("_blank");
  });
});
