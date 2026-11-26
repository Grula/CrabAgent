// @vitest-environment jsdom
// Chat and Work are separate top-level pages; Browser, Meeting, Calendar and
// documents are tabs of Work. Old #/browser and #/calendar links must still land there.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { applyHashRoute, routeFromHash, type LeaveWorkGuard, type WorkTab } from "./App";
import { NavBar } from "./components/NavBar";
import { needsDiscardConfirmForWorkTabSwitch } from "./lib/workspaceFiles";

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

describe("hash navigation guard", () => {
  /** What Back did to the address bar: the view has not moved yet. */
  function backTo(hash: string, from: string) {
    window.location.hash = from;
    const shownHash = window.location.hash;
    window.location.hash = hash;
    return shownHash;
  }

  it("keeps Work and the old hash when the guard cancels a Back", () => {
    const shownHash = backTo("#/chat", "#/work/documents");
    const apply = vi.fn();
    let deferred: (() => void) | null = null;
    const guard: LeaveWorkGuard = (run) => { deferred = run; return false; };
    applyHashRoute({ route: { page: "chat" }, currentPage: "work", shownHash, guard, apply });
    // The view did not move and the address bar was put back on the page still shown.
    expect(apply).not.toHaveBeenCalled();
    expect(window.location.hash).toBe("#/work/documents");
    // Discard in the page's own dialog runs the held move, which then navigates.
    expect(deferred).not.toBeNull();
    (deferred as unknown as () => void)();
    expect(apply).toHaveBeenCalledWith({ page: "chat" });
  });

  it("moves and keeps the new hash when the guard accepts", () => {
    const shownHash = backTo("#/chat", "#/work/documents");
    const apply = vi.fn();
    const guard: LeaveWorkGuard = (run) => { run(); return true; };
    applyHashRoute({ route: { page: "chat" }, currentPage: "work", shownHash, guard, apply });
    expect(apply).toHaveBeenCalledWith({ page: "chat" });
    // The address bar already shows the page that is now displayed.
    expect(window.location.hash).toBe("#/chat");
  });

  it("does not ask the Work guard for a Back inside Work", () => {
    const shownHash = backTo("#/work/timeline", "#/work/documents");
    const apply = vi.fn();
    const guard = vi.fn<LeaveWorkGuard>(() => false);
    applyHashRoute({
      route: { page: "work", tab: "timeline" }, currentPage: "work", shownHash, guard, apply,
    });
    expect(guard).not.toHaveBeenCalled();
    expect(apply).toHaveBeenCalledWith({ page: "work", tab: "timeline" });
    expect(window.location.hash).toBe("#/work/timeline");
  });
});

describe("hash navigation guard for Work tabs", () => {
  /**
   * ChatPage's Work-tab guard, at the level it is made of: the real predicate
   * decides, and a yes runs the switch the way the tab bar would.
   */
  function workTabGuard(dirty: { codeDirty: boolean; markdownDirty: boolean }) {
    const asked: string[] = [];
    const switched: string[] = [];
    const guard = (tab: WorkTab): boolean => {
      if (needsDiscardConfirmForWorkTabSwitch({
        currentTab: "documents", nextTab: tab, ...dirty,
      })) {
        asked.push(tab); // the dialog is pending; the tab stays
        return false;
      }
      switched.push(tab);
      return true;
    };
    return { asked, switched, guard };
  }

  /** A typed URL: the address bar already shows the new tab, the view has not moved. */
  function typed(hash: string, from: string) {
    window.location.hash = from;
    const shownHash = window.location.hash;
    window.location.hash = hash;
    return shownHash;
  }

  it("asks before a typed #/work/timeline unmounts a dirty document", () => {
    const shownHash = typed("#/work/timeline", "#/work/documents");
    const apply = vi.fn();
    const { asked, switched, guard } = workTabGuard({ codeDirty: false, markdownDirty: true });
    applyHashRoute({
      route: { page: "work", tab: "timeline" },
      currentPage: "work",
      currentWorkTab: "documents",
      shownHash,
      guard: null,
      workTabGuard: guard,
      apply,
    });
    // The panels are still mounted and the address bar shows the tab still shown.
    expect(apply).not.toHaveBeenCalled();
    expect(asked).toEqual(["timeline"]);
    expect(switched).toEqual([]);
    expect(window.location.hash).toBe("#/work/documents");
  });

  it("navigates a typed Work tab straight away when nothing is dirty", () => {
    const shownHash = typed("#/work/timeline", "#/work/documents");
    const apply = vi.fn();
    const { asked, switched, guard } = workTabGuard({ codeDirty: false, markdownDirty: false });
    applyHashRoute({
      route: { page: "work", tab: "timeline" },
      currentPage: "work",
      currentWorkTab: "documents",
      shownHash,
      guard: null,
      workTabGuard: guard,
      apply,
    });
    expect(asked).toEqual([]);
    expect(switched).toEqual(["timeline"]);
    expect(apply).toHaveBeenCalledWith({ page: "work", tab: "timeline" });
    expect(window.location.hash).toBe("#/work/timeline");
  });

  it("does not ask the work-tab guard for a route to the tab already shown", () => {
    const shownHash = typed("#/work/documents", "#/work/documents");
    const apply = vi.fn();
    const guard = vi.fn<(tab: WorkTab) => boolean>(() => false);
    applyHashRoute({
      route: { page: "work", tab: "documents" },
      currentPage: "work",
      currentWorkTab: "documents",
      shownHash,
      guard: null,
      workTabGuard: guard,
      apply,
    });
    expect(guard).not.toHaveBeenCalled();
    expect(apply).toHaveBeenCalledWith({ page: "work", tab: "documents" });
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
