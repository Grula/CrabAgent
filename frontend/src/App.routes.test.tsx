// @vitest-environment jsdom
// Chat and Work are separate top-level pages; Browser, Meeting, Calendar and
// documents are tabs of Work. Old #/browser and #/calendar links must still land there.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { routeFromHash } from "./App";
import { NavBar } from "./components/NavBar";

vi.mock("./lib/theme", () => ({
  useTheme: () => ({ theme: "dark", toggleTheme: () => {} }),
}));

function at(hash: string) {
  window.location.hash = hash;
  return routeFromHash();
}

describe("routeFromHash", () => {
  it("opens top-level pages", () => {
    expect(at("#/chat")).toEqual({ page: "chat", tab: undefined });
    expect(at("#/work")).toEqual({ page: "work", tab: undefined });
    expect(at("#/settings")).toEqual({ page: "settings", tab: undefined });
  });

  it("opens a Work tab", () => {
    expect(at("#/work/meeting")).toEqual({ page: "work", tab: "meeting" });
    expect(at("#/work/nonsense")).toEqual({ page: "work", tab: undefined });
  });

  it("sends the old browser and calendar pages to their Work tabs", () => {
    expect(at("#/browser")).toEqual({ page: "work", tab: "browser" });
    expect(at("#/calendar")).toEqual({ page: "work", tab: "calendar" });
  });

  it("ignores unknown routes", () => {
    expect(at("#/nope")).toBeNull();
    expect(at("")).toBeNull();
  });
});

describe("NavBar", () => {
  let container: HTMLDivElement;
  let root: Root;

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it("lists Chat and Work, and no longer Browser or Calendar", () => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => root.render(<NavBar currentPage="work" onNavigate={() => {}} />));

    const labels = [...container.querySelectorAll("nav button")].map((b) => b.getAttribute("aria-label"));
    expect(labels).toEqual(["Chat", "Work", "Agents", "Memory", "Usage", "Settings"]);
    expect(container.querySelector('nav button[aria-current="page"]')?.getAttribute("aria-label")).toBe("Work");
  });
});
