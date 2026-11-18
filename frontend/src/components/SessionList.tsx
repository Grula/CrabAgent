import { useState, useEffect, useRef, useMemo } from "react";
import {
  Plus,
  Trash2,
  Eye,
  EyeOff,
  ChevronLeft,
  Search,
  Plug,
  Clock,
  Settings as SettingsIcon,
  GitBranch,
  MessageSquare,
  X as XIcon,
  ListTodo,
  Mail,
  Zap,
  BarChart3,
  Bell,
  Inbox,
  Sparkles,
} from "lucide-react";
import { Session, SearchResult, searchSessions } from "../api/sessions";
import { AgentMonitorInfo } from "../api/monitor";
import { formatDate } from "../api/time";
import { EmptyState, ConfirmDialog } from "./ui";
import FloatingMenu from "./ui/FloatingMenu";
import { cn } from "../lib/cn";
import { displayTitle } from "../lib/userMessageText";

interface Props {
  sessions: Session[];
  activeId: string | null;
  onSelect: (session: Session) => void;
  onNew: () => void;
  onDelete: (sessionId: string) => void;
  onToggleHistorySearchable: (session: Session) => void;
  onOpenProviders: () => void;
  onOpenMcpServers: () => void;
  onOpenScheduledTasks: () => void;
  onOpenTasks: () => void;
  onOpenEmail: () => void;
  onOpenSkills: () => void;
  onQuickAction?: (action: "digest" | "remind" | "inbox") => void;
  mobileOpen?: boolean;
  onMobileClose?: () => void;
  workspace?: string;
  activeMonitors?: AgentMonitorInfo[];
}

export default function SessionList({
  sessions,
  activeId,
  onSelect,
  onNew,
  onDelete,
  onToggleHistorySearchable,
  onOpenProviders,
  onOpenMcpServers,
  onOpenScheduledTasks,
  onOpenTasks,
  onOpenEmail,
  onOpenSkills,
  onQuickAction,
  mobileOpen = false,
  onMobileClose,
  workspace,
  activeMonitors = [],
}: Props) {
  const [collapsed, setCollapsed] = useState(false);
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SearchResult[] | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Session | null>(null);
  const [quickMenuOpen, setQuickMenuOpen] = useState(false);
  const quickMenuRef = useRef<HTMLDivElement>(null);
  const quickMenuPanelRef = useRef<HTMLDivElement>(null);

  // Set of session_ids that are currently running
  const runningIds = useMemo(() => {
    const ids = new Set<string>();
    for (const m of activeMonitors) {
      if (m.status === "running") ids.add(m.session_id);
    }
    return ids;
  }, [activeMonitors]);

  // Auto-close mobile drawer when a session is selected
  useEffect(() => {
    if (mobileOpen && onMobileClose) {
      const handler = (e: KeyboardEvent) => {
        if (e.key === "Escape") onMobileClose();
      };
      window.addEventListener("keydown", handler);
      return () => window.removeEventListener("keydown", handler);
    }
  }, [mobileOpen, onMobileClose]);

  // Close quick menu when the mobile drawer hides this list (portal menu would
  // otherwise keep floating above the page like the old clipped absolute menu)
  useEffect(() => {
    if (!mobileOpen) setQuickMenuOpen(false);
  }, [mobileOpen]);

  // Close quick menu on outside click
  useEffect(() => {
    if (!quickMenuOpen) return;
    const handler = (e: MouseEvent) => {
      if (quickMenuRef.current && !quickMenuRef.current.contains(e.target as Node)
        && quickMenuPanelRef.current && !quickMenuPanelRef.current.contains(e.target as Node)) {
        setQuickMenuOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setQuickMenuOpen(false);
    };
    document.addEventListener("mousedown", handler);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", handler);
      document.removeEventListener("keydown", onKey);
    };
  }, [quickMenuOpen]);

  // ── Debounced full-text search via API ────────────────
  useEffect(() => {
    if (!query.trim()) {
      setSearchResults(null);
      return;
    }
    const timer = setTimeout(async () => {
      try {
        const results = await searchSessions(query.trim(), workspace);
        setSearchResults(results);
      } catch {
        setSearchResults([]);
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [query]);

  const filtered = useMemo(() => query.trim()
    ? sessions.filter((s) =>
        (s.title || "").toLowerCase().includes(query.toLowerCase()),
      )
    : sessions, [sessions, query]);

  const handleSelect = (s: Session) => {
    onSelect(s);
    if (onMobileClose) onMobileClose();
  };

  const handleNew = () => {
    onNew();
    if (onMobileClose) onMobileClose();
  };

  const handleDelete = () => {
    if (deleteTarget) onDelete(deleteTarget.session_id);
    setDeleteTarget(null);
  };

  if (collapsed) {
    const ToolButton = ({
      onClick,
      icon,
      title,
    }: {
      onClick: () => void;
      icon: React.ReactNode;
      title: string;
    }) => (
      <button
        onClick={onClick}
        title={title}
        className={cn(
          "p-2 rounded-lg transition-colors",
          "text-[var(--text-tertiary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)]",
        )}
      >
        {icon}
      </button>
    );
    return (
      <div
        className="hidden md:flex flex-col items-center py-3 gap-1 border-r border-[var(--border-subtle)] bg-[var(--bg-sidebar)] shrink-0"
        style={{ width: 56 }}
      >
        <button
          onClick={() => setCollapsed(false)}
          title={"Expand sidebar"}
          className="p-2 rounded-lg text-[var(--text-tertiary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
        >
          <MessageSquare size={16} />
        </button>
        <button
          onClick={handleNew}
          title={`${"New Chat"} (⌘K)`}
          className="my-1 flex h-9 w-9 items-center justify-center rounded-lg hover:bg-[var(--bg-tertiary)] transition-colors"
        >
          <span className="flex h-7 w-7 items-center justify-center rounded-full bg-[var(--brand)] text-[var(--text-on-brand)]">
            <Plus size={15} strokeWidth={2.5} />
          </span>
        </button>
        <div className="flex-1" />
        <ToolButton onClick={onOpenSkills} icon={<Sparkles size={15} />} title="Skills" />
        <ToolButton onClick={onOpenMcpServers} icon={<Plug size={15} />} title={"MCP Servers"} />
        <ToolButton onClick={onOpenScheduledTasks} icon={<Clock size={15} />} title={"Scheduled Tasks"} />
        <ToolButton onClick={onOpenProviders} icon={<SettingsIcon size={15} />} title={"LLM Providers"} />
      </div>
    );
  }

  const sidebar = (
    <div
      className={cn(
        "flex flex-col h-full bg-[var(--bg-sidebar)] w-64 lg:w-72",
        "md:border-r md:border-[var(--border-subtle)] md:shrink-0",
      )}
    >
      {/* Mobile header */}
      <div className="md:hidden px-3 py-2.5 flex items-center gap-2">
        <span className="font-display text-[17px] text-[var(--text-primary)] flex-1">
          {"Chats"}
        </span>
        <button
          onClick={handleNew}
          title={`${"New Chat"} (⌘K)`}
          aria-label={"New Chat"}
          className="flex h-8 w-8 items-center justify-center rounded-full bg-[var(--brand)] text-[var(--text-on-brand)] hover:bg-[var(--brand-hover)] transition-colors"
        >
          <Plus size={15} strokeWidth={2.5} />
        </button>
        <button
          onClick={onMobileClose}
          title={"Close"}
          aria-label={"Close"}
          className="p-1.5 rounded-lg text-[var(--text-tertiary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
        >
          <XIcon size={14} />
        </button>
      </div>
      {/* Desktop header */}
      <div className="hidden md:flex px-2 pt-2 pb-1 items-center gap-1">
        <button
          onClick={handleNew}
          title={`${"New Chat"} (⌘K)`}
          className="group/new flex flex-1 min-w-0 items-center gap-2.5 h-9 px-1.5 rounded-lg text-sm font-medium text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
        >
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-[var(--brand)] text-[var(--text-on-brand)] transition-transform group-hover/new:scale-105">
            <Plus size={14} strokeWidth={2.5} />
          </span>
          {"New chat"}
        </button>
        <button
          onClick={() => setCollapsed(true)}
          title={"Collapse sidebar"}
          className="p-1.5 rounded-lg text-[var(--text-tertiary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
        >
          <ChevronLeft size={14} />
        </button>
      </div>

      {/* Search */}
      {sessions.length > 2 && (
        <div className="px-2 pb-1">
          <div className="relative">
            <Search
              size={13}
              className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--text-tertiary)]"
            />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={"Search sessions…"}
              className="w-full h-8 pl-7 pr-2 text-[13px] rounded-lg bg-transparent border border-transparent text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)] hover:bg-[var(--bg-tertiary)] focus:bg-[var(--bg-secondary)] focus:outline-none focus:border-[var(--border)] focus:ring-2 focus:ring-[var(--brand)]/30"
            />
          </div>
        </div>
      )}

      {/* List */}
      <div className="flex-1 overflow-y-auto pb-2">
        {searchResults !== null ? (
          /* ── Search results ── */
          searchResults.length === 0 ? (
            <div className="p-4 text-xs text-center text-[var(--text-tertiary)]">
              {"No results"}
            </div>
          ) : (
            searchResults.map((r) => {
              const isActive = activeId === r.session_id;
              return (
                <div
                  key={r.session_id}
                  onClick={() => {
                    const session = sessions.find((s) => s.session_id === r.session_id);
                    if (session) {
                      handleSelect(session);
                    } else {
                      // Session not in current list — construct minimal object
                      handleSelect({
                        session_id: r.session_id,
                        title: r.title || "",
                        workspace: workspace || "",
                        model: "",
                        provider: "",
                        agent: "default",
                        active_branch: "main",
                        history_searchable: true,
                        created_at: null,
                        updated_at: r.updated_at,
                      });
                    }
                  }}
                  className={cn(
                    "group relative mx-2 px-2.5 py-2 rounded-lg cursor-pointer transition-colors",
                    isActive
                      ? "bg-[var(--bg-tertiary)]"
                      : "hover:bg-[var(--bg-tertiary)]/70",
                  )}
                >
                  <div className="min-w-0">
                    <div className="truncate text-sm text-[var(--text-primary)] flex items-center gap-1.5">
                      <span className="truncate">{r.title ? displayTitle(r.title) : `(${"untitled"})`}</span>
                      <span className={cn(
                        "inline-flex items-center px-1 py-px rounded text-[9px] font-medium shrink-0",
                        r.role === "user" ? "bg-[var(--brand-bg)] text-[var(--brand)]" : "bg-[var(--accent-bg)] text-[var(--accent)]",
                      )}>
                        {r.role === "user" ? "Q" : "A"}
                      </span>
                    </div>
                    {r.snippet && (
                      <div className="text-[11px] mt-0.5 text-[var(--text-tertiary)] leading-relaxed line-clamp-2">
                        {r.snippet}
                      </div>
                    )}
                    <div className="text-[10px] mt-0.5 text-[var(--text-tertiary)] font-mono">
                      {r.updated_at ? formatDate(r.updated_at) : ""}
                    </div>
                  </div>
                </div>
              );
            })
          )
        ) : sessions.length === 0 ? (
          <EmptyState
            compact
            icon={<MessageSquare size={24} />}
            title={"No chats yet"}
            description=""
          />
        ) : filtered.length === 0 ? (
          <div className="p-4 text-xs text-center text-[var(--text-tertiary)]">
            {"No results"}
          </div>
        ) : (
          <>
          <div className="px-4 pt-3 pb-1.5 text-[11px] font-medium text-[var(--text-tertiary)]">
            {"Recents"}
          </div>
          {filtered.map((s) => {
            const isActive = activeId === s.session_id;
            return (
              <div
                key={s.session_id}
                onClick={() => handleSelect(s)}
                title={s.updated_at ? formatDate(s.updated_at) : undefined}
                className={cn(
                  "group relative mx-2 pl-2.5 pr-1.5 py-1.5 rounded-lg cursor-pointer transition-colors",
                  isActive
                    ? "bg-[var(--bg-tertiary)]"
                    : "hover:bg-[var(--bg-tertiary)]/70",
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <div
                      className={cn(
                        "truncate text-[13px] flex items-center gap-1.5",
                        isActive
                          ? "text-[var(--text-primary)]"
                          : "text-[var(--text-secondary)] group-hover:text-[var(--text-primary)]",
                      )}
                    >
                      {runningIds.has(s.session_id) && (
                        <span
                          className="inline-block w-1.5 h-1.5 rounded-full shrink-0 animate-pulse"
                          style={{ background: "var(--success)" }}
                          title="Running"
                        />
                      )}
                      <span className="truncate">
                        {s.title ? displayTitle(s.title) : `(${"untitled"})`}
                      </span>
                      {s.active_branch && s.active_branch !== "main" && (
                        <span className="inline-flex items-center gap-0.5 text-[9px] px-1 py-px rounded font-mono bg-[var(--warning-bg)] text-[var(--warning)] shrink-0">
                          <GitBranch size={9} />
                          {s.active_branch}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-0.5">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        onToggleHistorySearchable(s);
                      }}
                      title={s.history_searchable ? "Allow history search" : "History search disabled"}
                      className={cn(
                        "shrink-0 p-1 rounded transition-all opacity-0 group-hover:opacity-100",
                        s.history_searchable
                          ? "text-[var(--text-tertiary)] hover:text-[var(--brand)] hover:bg-[var(--bg-tertiary)]"
                          : "text-[var(--warning)] opacity-100 hover:bg-[var(--warning-bg)]",
                      )}
                    >
                      {s.history_searchable ? <Eye size={12} /> : <EyeOff size={12} />}
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setDeleteTarget(s);
                      }}
                      title={"Delete session"}
                      className={cn(
                      "shrink-0 p-1 rounded transition-all",
                      "opacity-0 group-hover:opacity-100",
                      "text-[var(--text-tertiary)] hover:text-[var(--danger)] hover:bg-[var(--danger-bg)]",
                    )}
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
          </>
        )}
      </div>

      {/* Footer toolbar */}
      <div className="px-2 py-2 border-t border-[var(--border-subtle)] flex items-center relative">
        {/* Quick action trigger */}
        {onQuickAction && (
          <div ref={quickMenuRef} className="relative">
            <button
              onClick={() => setQuickMenuOpen((v) => !v)}
              className={cn(
                "p-1.5 rounded-lg transition-colors",
                quickMenuOpen
                  ? "text-[var(--brand)] bg-[var(--brand-bg)]"
                  : "text-[var(--text-tertiary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)]",
              )}
              title={"Quick actions"}
            >
              <Zap size={15} />
            </button>
            {quickMenuOpen && (
              <FloatingMenu
                open={quickMenuOpen}
                anchorRef={quickMenuRef}
                menuRef={quickMenuPanelRef}
                placement="top-start"
                gap={4}
                className="w-40 rounded-lg border border-[var(--border)] bg-[var(--bg-elevated)] shadow-[var(--shadow-lg)] py-1 animate-scale-in"
              >
                <button
                  onClick={() => { onQuickAction("digest"); setQuickMenuOpen(false); }}
                  className="w-full flex items-center gap-2 px-3 py-2 text-xs text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] hover:text-[var(--text-primary)] transition-colors"
                >
                  <BarChart3 size={13} className="text-[var(--brand)]" />
                  {"Digest"}
                </button>
                <button
                  onClick={() => { onQuickAction("remind"); setQuickMenuOpen(false); }}
                  className="w-full flex items-center gap-2 px-3 py-2 text-xs text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] hover:text-[var(--text-primary)] transition-colors"
                >
                  <Bell size={13} className="text-[var(--warning)]" />
                  {"Remind"}
                </button>
                <button
                  onClick={() => { onQuickAction("inbox"); setQuickMenuOpen(false); }}
                  className="w-full flex items-center gap-2 px-3 py-2 text-xs text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] hover:text-[var(--text-primary)] transition-colors"
                >
                  <Inbox size={13} className="text-[var(--accent)]" />
                  {"Inbox"}
                </button>
              </FloatingMenu>
            )}
          </div>
        )}

        {/* Spacer */}
        <div className="flex-1" />

        {/* Tool icons — unified hover style */}
        <div className="flex items-center gap-0.5">
          <button
            onClick={onOpenTasks}
            title={"Tasks"}
            className="p-1.5 rounded-lg text-[var(--text-tertiary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
          >
            <ListTodo size={15} />
          </button>
          <button
            onClick={onOpenEmail}
            title={"Email Configuration"}
            className="p-1.5 rounded-lg text-[var(--text-tertiary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
          >
            <Mail size={15} />
          </button>
          <button
            onClick={onOpenMcpServers}
            title={"MCP Servers"}
            className="p-1.5 rounded-lg text-[var(--text-tertiary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
          >
            <Plug size={15} />
          </button>
          <button
            onClick={onOpenSkills}
            title="Skills"
            className="p-1.5 rounded-lg text-[var(--text-tertiary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
          >
            <Sparkles size={15} />
          </button>
          <button
            onClick={onOpenScheduledTasks}
            title={"Scheduled Tasks"}
            className="p-1.5 rounded-lg text-[var(--text-tertiary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
          >
            <Clock size={15} />
          </button>
          <button
            onClick={onOpenProviders}
            title={"LLM Providers"}
            className="p-1.5 rounded-lg text-[var(--text-tertiary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
          >
            <SettingsIcon size={15} />
          </button>
        </div>
      </div>

      <ConfirmDialog
        open={!!deleteTarget}
        onOpenChange={(o) => !o && setDeleteTarget(null)}
        title={`Delete conversation "${deleteTarget?.title || "untitled"}"?`}
        description=""
        confirmText={"Delete"}
        tone="danger"
        onConfirm={handleDelete}
      />
    </div>
  );

  return (
    <>
      {!mobileOpen && (
        <div className="hidden md:contents">{sidebar}</div>
      )}
      {mobileOpen && (
        <>
          <div
            className="md:hidden fixed inset-0 z-40 bg-[var(--bg-overlay)] backdrop-blur-sm animate-fade-in"
            onClick={onMobileClose}
          />
          <div className="md:hidden fixed top-0 left-0 bottom-0 z-50 w-72 max-w-[85vw] animate-slide-up shadow-[var(--shadow-lg)]">
            {sidebar}
          </div>
        </>
      )}
    </>
  );
}
