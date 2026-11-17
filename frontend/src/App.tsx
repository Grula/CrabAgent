import { useState, useEffect, useCallback } from "react";
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
export type WorkTab = "documents" | "browser" | "meeting" | "calendar";

const pageIds: readonly PageId[] = ["chat", "work", "agents", "memory", "usage", "settings"];
const workTabs: readonly WorkTab[] = ["documents", "browser", "meeting", "calendar"];

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
    setPage(target);
  }, []);
  useEffect(() => {
    const handleHashChange = () => {
      const route = routeFromHash();
      if (!route) return;
      setPage(route.page);
      if (route.tab) setWorkTab(route.tab);
    };
    window.addEventListener("hashchange", handleHashChange);
    return () => window.removeEventListener("hashchange", handleHashChange);
  }, []);

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
            onViewChange={setPage}
            workTab={workTab}
            onWorkTabChange={setWorkTab}
            onActiveSessionChange={setActiveSessionId}
            onActiveBranchChange={setActiveBranch}
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
