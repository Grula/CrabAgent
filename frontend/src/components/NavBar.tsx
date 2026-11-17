import { MessageSquare, Users, Brain, Settings, Sun, Moon, LogOut, Coins, LayoutPanelLeft } from "lucide-react";
import { useTheme } from "../lib/theme";
import { cn } from "../lib/cn";
import { useState } from "react";
import { Modal, Button } from "./ui";
import type { PageId } from "../App";

interface NavItem {
  id: PageId;
  label: string;
  icon: React.ReactNode;
}

const items: NavItem[] = [
  { id: "chat", label: "Chat", icon: <MessageSquare size={15} /> },
  // Browser, Meeting, Calendar, documents and the agent run board live in Work.
  { id: "work", label: "Work", icon: <LayoutPanelLeft size={15} /> },
  { id: "agents", label: "Agents", icon: <Users size={15} /> },
  { id: "memory", label: "Memory", icon: <Brain size={15} /> },
  { id: "usage", label: "Usage", icon: <Coins size={15} /> },
  { id: "settings", label: "Settings", icon: <Settings size={15} /> },
];

function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();
  return (
    <button
      onClick={toggleTheme}
      aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
      title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
      className={cn(
        "p-1.5 rounded-lg transition-colors",
        "text-[var(--text-tertiary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)]",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--brand)]",
      )}
    >
      {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
    </button>
  );
}

export function NavBar({
  currentPage,
  onNavigate,
  onLogout,
  sessionId,
}: {
  currentPage: PageId;
  onNavigate: (page: PageId) => void;
  onLogout?: () => void;
  sessionId?: string | null;
}) {
  const [showLogoutConfirm, setShowLogoutConfirm] = useState(false);

  return (
    <header
      className={cn(
        "sticky top-0 z-30 flex items-center gap-1 px-2 sm:px-4 h-12 min-w-0",
        "bg-[var(--bg-sidebar)]",
        "border-b border-[var(--border-subtle)]",
      )}
      style={{ paddingTop: "env(safe-area-inset-top, 0px)" }}
    >
      <button
        onClick={() => onNavigate("chat")}
        aria-label="CrabAgent home"
        className="flex shrink-0 items-center gap-2 mr-1 lg:mr-4 px-1.5 h-8 rounded-lg hover:bg-[var(--bg-tertiary)] transition-colors"
      >
        <span aria-hidden="true" className="text-lg leading-none">🦀</span>
        <span className="font-display hidden lg:inline text-[17px] text-[var(--text-primary)]">
          CrabAgent
        </span>
      </button>

      <nav aria-label="Primary navigation" className="flex h-full min-w-0 flex-1 items-center gap-0.5 overflow-x-auto overflow-y-hidden overscroll-x-contain [scrollbar-width:none]">
        {items.map((item) => {
          const isActive = currentPage === item.id;
          return (
            <button
              key={item.id}
              onClick={() => onNavigate(item.id)}
              aria-label={item.label}
              aria-current={isActive ? "page" : undefined}
              className={cn(
                "flex shrink-0 items-center gap-1.5 px-2 lg:px-2.5 h-8 rounded-lg text-[13px] transition-colors",
                isActive
                  ? "text-[var(--text-primary)] bg-[var(--bg-tertiary)] font-medium"
                  : "text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)]",
              )}
            >
              {item.icon}
              <span className="hidden lg:inline">{item.label}</span>
            </button>
          );
        })}
      </nav>

      <div className="ml-auto flex shrink-0 items-center gap-1">
        {onLogout && (
          <button
            onClick={() => setShowLogoutConfirm(true)}
            aria-label={"Logout"}
            title={"Logout"}
            className={cn(
              "p-1.5 rounded-lg transition-colors",
              "text-[var(--text-tertiary)] hover:text-[var(--danger)] hover:bg-[var(--danger-bg)]",
            )}
          >
            <LogOut size={15} />
          </button>
        )}
        <ThemeToggle />
      </div>

      {onLogout && (
        <Modal
          open={showLogoutConfirm}
          onOpenChange={setShowLogoutConfirm}
          title={"Sign out?"}
          description={"You'll need to log in again to continue."}
          size="sm"
          footer={
            <>
              <Button variant="ghost" onClick={() => setShowLogoutConfirm(false)}>
                {"Cancel"}
              </Button>
              <Button variant="danger" onClick={onLogout}>
                {"Sign Out"}
              </Button>
            </>
          }
        >
          <div className="text-center py-2">
            <LogOut size={32} className="mx-auto text-[var(--danger)] mb-2" />
          </div>
        </Modal>
      )}
    </header>
  );
}
