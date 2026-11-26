import { useState, useEffect, useCallback, useRef } from "react";
import { api } from "./api/client";
import LoginPage from "./pages/LoginPage";
import ChatPage from "./pages/ChatPage";
import AgentsPage from "./pages/AgentsPage";
import MemoryPage from "./pages/MemoryPage";
import SettingsPage from "./pages/SettingsPage";
import UsagePage from "./pages/UsagePage";
import { NavBar } from "./components/NavBar";
import { cn } from "./lib/cn";
import { connectGlobalSSE } from "./api/monitor";
import { toast } from "./components/ui/Toast";
import { FtsStatusBar } from "./components/FtsStatusBar";
import { BackgroundTasks } from "./components/BackgroundTasks";
import { MobileConsole } from "./pages/mobile/MobileConsole";

export type PageId = "chat" | "work" | "agents" | "memory" | "usage" | "settings";

/** Tabs of the Work page. */
export type WorkTab = "documents" | "browser" | "meeting" | "calendar" | "timeline";

const pageIds: readonly PageId[] = ["chat", "work", "agents", "memory", "usage", "settings"];
const workTabs: readonly WorkTab[] = ["documents", "browser", "meeting", "calendar", "timeline"];

const PAGE_STORAGE_KEY = "crabagent_mode";
const WORK_TAB_STORAGE_KEY = "crabagent_work_tab";

/** Parse `#/page` or `#/work/<tab>`. The old `#/browser` and `#/calendar` pages are Work tabs now. */
export function routeFromHash(): { page: PageId; tab?: WorkTab } | null {
  const [first, second] = window.location.hash.replace(/^#\/?/, "").split("/");
  if (first === "browser" || first === "calendar") return { page: "work", tab: first };
  if (!pageIds.includes(first as PageId)) return null;
  const tab = first === "work" && workTabs.includes(second as WorkTab) ? second as WorkTab : undefined;
  return { page: first as PageId, tab };
}

/** A route the address bar or the nav bar asks the app to show. */
export interface AppRoute {
  page: PageId;
  tab?: WorkTab;
}

/**
 * ChatPage's answer to "may I leave Work?". It runs the move itself when nothing
 * is dirty and returns true; when a discard confirmation is pending it returns
 * false and keeps the run for its Discard button, so the caller leaves the view
 * (and the address bar) where it is until the user decides.
 */
export type LeaveWorkGuard = (run: () => void) => boolean;

/**
 * ChatPage's answer to "may I leave this Work tab?". It runs the switch itself
 * when nothing is dirty and returns true; with a discard confirmation pending it
 * returns false, so a hash route holds the tab (and the URL) where it is.
 */
export type WorkTabGuard = (tab: WorkTab) => boolean;

/**
 * Run a page move through the leave-work guard when it leaves Work, and say
 * whether the move went through. Inside Work, or away from a clean editor, the
 * move just runs.
 */
export function runMoveThroughLeaveWorkGuard(args: {
  currentPage: PageId;
  nextPage: PageId;
  guard: LeaveWorkGuard | null;
  move: () => void;
}): boolean {
  if (args.currentPage === "work" && args.nextPage !== "work" && args.guard) {
    return args.guard(args.move);
  }
  args.move();
  return true;
}

/**
 * Apply a route the address bar asked for, asking the pages that would lose work
 * first — the same questions the in-app controls ask. Answers whether the route
 * was applied; a held route leaves the view and the hash where they are.
 */
export function applyRouteThroughGuards(args: {
  route: AppRoute;
  currentPage: PageId;
  /** Work tab shown right now. */
  currentWorkTab: WorkTab;
  guard: LeaveWorkGuard | null;
  /** ChatPage's Work-tab guard; null before that page has mounted. */
  workTabGuard: WorkTabGuard | null;
  apply: (route: AppRoute) => void;
}): boolean {
  // Leaving Work unmounts the editors: the same guard the nav bar uses decides.
  if (args.route.page !== "work" || args.currentPage !== "work") {
    return runMoveThroughLeaveWorkGuard({
      currentPage: args.currentPage,
      nextPage: args.route.page,
      guard: args.guard,
      move: () => args.apply(args.route),
    });
  }
  // A route inside Work may name another tab, which unmounts the Documents
  // panels just as clicking the tab does — so it asks the same guard.
  const tab = args.route.tab;
  if (tab !== undefined && tab !== args.currentWorkTab && args.workTabGuard) {
    if (!args.workTabGuard(tab)) return false;
  }
  args.apply(args.route);
  return true;
}

/**
 * Apply a route that came from the address bar (browser Back/Forward, a typed
 * URL). It passes the same guards as the nav bar and the tab strip, because a
 * route to another Work tab unmounts the editors just as surely as a click. A
 * cancelled move is undone in the address bar too: no URL may show a page the
 * app refused to open.
 */
export function applyHashRoute(args: {
  route: AppRoute;
  currentPage: PageId;
  /** Work tab shown right now; defaults to Documents, where nothing is mounted. */
  currentWorkTab?: WorkTab;
  /** The hash that matches what is on screen right now. */
  shownHash: string;
  guard: LeaveWorkGuard | null;
  /** ChatPage's Work-tab guard; omitted when no page hosts one. */
  workTabGuard?: WorkTabGuard | null;
  apply: (route: AppRoute) => void;
}): void {
  const accepted = applyRouteThroughGuards({
    route: args.route,
    currentPage: args.currentPage,
    currentWorkTab: args.currentWorkTab ?? "documents",
    guard: args.guard,
    workTabGuard: args.workTabGuard ?? null,
    apply: args.apply,
  });
  if (!accepted && window.location.hash !== args.shownHash) {
    window.history.replaceState(null, "", args.shownHash);
  }
}

function readStored<T extends string>(key: string, allowed: readonly T[]): T | null {
  try {
    const value = localStorage.getItem(key);
    return allowed.includes(value as T) ? value as T : null;
  } catch {
    return null;
  }
}

function writeStored(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Storage can be unavailable (private mode); the choice just isn't remembered.
  }
}

interface FtsRebuild {
  in_progress: boolean;
  total: number;
  done: number;
}

function AuthenticatedApp({ onLogout }: { onLogout: () => void }) {
  const [page, setPage] = useState<PageId>(() => {
    const route = routeFromHash();
    if (route) return route.page;
    return readStored(PAGE_STORAGE_KEY, ["chat", "work"] as const) ?? "chat";
  });
  const [workTab, setWorkTab] = useState<WorkTab>(
    () => routeFromHash()?.tab ?? readStored(WORK_TAB_STORAGE_KEY, workTabs) ?? "documents",
  );
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [activeBranch, setActiveBranch] = useState<string | undefined>();
  const [ftsStatus, setFtsStatus] = useState<FtsRebuild | null>(null);
  // ChatPage registers its leave-work guard here: the page stays mounted, but
  // the nav bar lives above it, so a move away from Work has to ask it first.
  const leaveWorkGuardRef = useRef<LeaveWorkGuard | null>(null);
  // ChatPage's Work-tab guard: a typed #/work/<other-tab> unmounts the same
  // panels a tab click does, so App holds the route until the page agrees.
  const workTabGuardRef = useRef<WorkTabGuard | null>(null);
  // The hash that matches what is on screen: a hashchange the guard cancels is
  // undone with it, so the address bar never disagrees with the view.
  const shownHashRef = useRef<string>("");

  // Chat and Work share one ChatPage (same conversation); remember which one was open.
  useEffect(() => {
    if (page === "chat" || page === "work") writeStored(PAGE_STORAGE_KEY, page);
  }, [page]);
  useEffect(() => {
    writeStored(WORK_TAB_STORAGE_KEY, workTab);
  }, [workTab]);
  // Keep the address in step with what is shown, so a reload (or a copied
  // link) reopens the same page and tab. replaceState fires no hashchange.
  useEffect(() => {
    const hash = page === "work" ? `#/work/${workTab}` : `#/${page}`;
    shownHashRef.current = hash;
    if (window.location.hash !== hash) window.history.replaceState(null, "", hash);
  }, [page, workTab]);

  // Lightweight health poll — only for FTS progress, never blocks the UI
  const checkHealth = useCallback(async () => {
    try {
      const res = await fetch("/health");
      if (res.ok) {
        const data = await res.json();
        const fts = data.fts_rebuild as FtsRebuild | undefined;
        setFtsStatus(fts ?? null);
      }
    } catch {
      // ignore — backend might still be starting
    }
  }, []);

  // Initial check + periodic poll while FTS is running
  useEffect(() => {
    checkHealth();
    const timer = setInterval(checkHealth, 3000);
    return () => clearInterval(timer);
  }, [checkHealth]);

  const handleNavigate = useCallback((target: PageId) => {
    // Leaving Work unmounts the editors and the document panels, so ask the
    // page through its guard: a dirty buffer (code or Markdown) confirms before
    // the move. Inside Work, or with nothing to lose, the guard just runs.
    runMoveThroughLeaveWorkGuard({
      currentPage: page,
      nextPage: target,
      guard: leaveWorkGuardRef.current,
      move: () => setPage(target),
    });
  }, [page]);

  /** Show a route without asking: the guard already said yes. */
  const applyRoute = useCallback((route: AppRoute) => {
    setPage(route.page);
    if (route.tab) setWorkTab(route.tab);
  }, []);

  useEffect(() => {
    const handleHashChange = () => {
      const route = routeFromHash();
      if (!route) return;
      // Back/Forward goes through the same guards as the nav bar and the tab
      // strip; if one holds the move, the address bar is put back on the page
      // and tab that are still shown.
      applyHashRoute({
        route,
        currentPage: page,
        currentWorkTab: workTab,
        shownHash: shownHashRef.current,
        guard: leaveWorkGuardRef.current,
        workTabGuard: workTabGuardRef.current,
        apply: applyRoute,
      });
    };
    window.addEventListener("hashchange", handleHashChange);
    return () => window.removeEventListener("hashchange", handleHashChange);
  }, [page, workTab, applyRoute]);

  // Notify when FTS completes
  const ftsInProgress = ftsStatus?.in_progress;
  useEffect(() => {
    if (ftsStatus && !ftsStatus.in_progress && ftsStatus.total > 0) {
      toast.success("Search index ready");
    }
  }, [ftsStatus?.in_progress]);

  // Connect to global SSE for system notifications
  useEffect(() => {
    const es = connectGlobalSSE((event) => {
      if (event.type === "notification") {
        const d = event.data as Record<string, string>;
        const text = d.text || "";
        toast.info(text);
      }
    });
    return () => es?.close();
  }, []);

  const isChatOrWork = page === "chat" || page === "work";

  return (
    <div className="flex flex-col h-dvh overflow-hidden">
      <NavBar currentPage={page} onNavigate={handleNavigate} onLogout={onLogout} sessionId={activeSessionId} />
      {isChatOrWork && activeSessionId && <BackgroundTasks key={`${activeSessionId}:${activeBranch}`} sessionId={activeSessionId} activeBranch={activeBranch} />}
      <div className="flex-1 relative overflow-hidden">
        {/* Chat and Work are two views of one ChatPage, so the conversation, the
            live event stream and open documents survive switching between them. */}
        <div className={cn("absolute inset-0", !isChatOrWork && "hidden")}>
          <ChatPage
            view={page === "work" ? "work" : "chat"}
            onViewChange={handleNavigate}
            workTab={workTab}
            onWorkTabChange={setWorkTab}
            onActiveSessionChange={setActiveSessionId}
            onActiveBranchChange={setActiveBranch}
            leaveWorkGuardRef={leaveWorkGuardRef}
            workTabGuardRef={workTabGuardRef}
          />
        </div>
        {page === "agents" && <div className="absolute inset-0"><AgentsPage /></div>}
        {page === "memory" && <div className="absolute inset-0"><MemoryPage /></div>}
        {page === "usage" && <div className="absolute inset-0"><UsagePage /></div>}
        {page === "settings" && <div className="absolute inset-0"><SettingsPage /></div>}
      </div>

      {/* Non-blocking FTS indexing progress — bottom-right floating pill */}
      {ftsInProgress && ftsStatus && ftsStatus.total > 0 && (
        <FtsStatusBar done={ftsStatus.done} total={ftsStatus.total} />
      )}
    </div>
  );
}

function MainApp() {
  const [isAuthenticated, setIsAuthenticated] = useState(!!api.getToken());

  const handleLogin = () => {
    setIsAuthenticated(true);
  };

  const handleLogout = () => {
    api.clearToken();
    setIsAuthenticated(false);
  };

  if (!isAuthenticated) {
    return <LoginPage onLogin={handleLogin} />;
  }

  return <AuthenticatedApp onLogout={handleLogout} />;
}

export default function App() {
  // Mobile console route: #/m* is handled entirely by MobileConsole (PWA standalone entry)
  if (window.location.hash.startsWith("#/m")) {
    // A lazy import would cause async flicker; the /m components are tiny, so import them synchronously (see the imports at the top of the file)
    return <MobileConsole />;
  }
  return <MainApp />;
}
