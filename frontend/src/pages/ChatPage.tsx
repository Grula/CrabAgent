import { useState, useEffect, useCallback, useRef } from "react";
import { toast } from "sonner";
import ChatInput from "../components/ChatInput";
import {
  Bot,
  PanelRightOpen,
  PanelRightClose,
  Sparkles,
  MessageSquare,
  Code,
  Compass,
  X,
  Menu,
  Loader2,
  ChevronDown,
  Check,
  LayoutPanelLeft,
  LayoutPanelTop,
  FileText,
  Archive,
  Globe,
  Mic,
  Calendar as CalendarIcon,
} from "lucide-react";
import type { WorkTab } from "../App";
import CalendarPage from "./CalendarPage";
import * as goalsApi from "../api/goals";
import { Goal as GoalData, GoalDraft } from "../types/Goal";
import { GoalCard } from "../components/GoalCard";
import { GoalComposer } from "../components/GoalComposer";
import { GoalTimeline } from "../components/GoalTimeline";
import * as sessionsApi from "../api/sessions";
import * as providersApi from "../api/providers";
import * as mcpServersApi from "../api/mcpServers";
import { McpServer, McpServerStatus } from "../api/mcpServers";
import { Session } from "../api/sessions";
import {
  AgentProfile as AgentProfileType,
  AGENTS_CHANGED_EVENT,
  DEFAULT_AGENT_SETTING,
  listAgentProfiles,
} from "../api/agents";
import { getSettings } from "../api/settings";
import SessionList from "../components/SessionList";
import ChatPanel from "../components/ChatPanel";
import ProviderPanel from "../components/ProviderPanel";
import McpServerPanel from "../components/McpServerPanel";
import McpStatusBar from "../components/McpStatusBar";
import BranchSelector from "../components/BranchSelector";
import FileBrowser from "../components/FileBrowser";
import TodoWidget from "../components/TodoWidget";
import NotificationBell from "../components/NotificationBell";
import FloatingMenu from "../components/ui/FloatingMenu";
import { ScheduledTaskPanel } from "../components/ScheduledTaskPanel";
import TaskPanel from "../components/TaskPanel";
import EmailPanel from "../components/EmailPanel";
import SkillsPanel from "../components/SkillsPanel";
import { AgentRunBoard } from "../components/AgentRunBoard";
import { SessionStatsBar } from "../components/SessionStatsBar";
import { DelegateModal } from "../components/DelegateModal";
import { ResultCompare } from "../components/ResultCompare";
import WorkspaceSwitcher from "../components/WorkspaceSwitcher";
import ModelSelector from "../components/ModelSelector";
import { Modal, Button } from "../components/ui";
import { useChatState } from "../hooks/useChatState";
import { useAgentRunBoard } from "../hooks/useAgentRunBoard";
import { useModelSelector } from "../hooks/useModelSelector";
import { cn } from "../lib/cn";
import { reasoningOptions, effectiveEffort } from "../lib/reasoningEffort";
import { dbMessagesToChat } from "../lib/messageTransforms";
import { DocumentPanel, DocState } from "../components/DocumentPanel";
import { DocOpEvent } from "../components/DocumentTimeline";
import { CodePanel } from "../components/CodePanel";
import { PrototypePanel } from "../components/PrototypePanel";
import { MeetingPanel } from "../components/MeetingPanel";
import { MarkdownPanel } from "../components/MarkdownPanel";
import BrowserPanel from "../components/BrowserPanel";
import { SSEEvent } from "../api/events";
import * as documentsApi from "../api/documents";

interface Props {
  /** Chat page (sessions + full chat) or Work page (side chat + work tabs). */
  view: "chat" | "work";
  onViewChange: (view: "chat" | "work") => void;
  workTab: WorkTab;
  onWorkTabChange: (tab: WorkTab) => void;
  onActiveSessionChange?: (sessionId: string | null) => void;
  onActiveBranchChange?: (branch: string) => void;
}

const WORK_TABS: { id: WorkTab; label: string; icon: React.ReactNode }[] = [
  { id: "documents", label: "Documents", icon: <FileText size={13} /> },
  { id: "browser", label: "Browser", icon: <Globe size={13} /> },
  { id: "meeting", label: "Meeting", icon: <Mic size={13} /> },
  { id: "calendar", label: "Calendar", icon: <CalendarIcon size={13} /> },
];

const STARTER_PROMPTS = [
  { icon: <Code size={14} />, label: "Debug an error", prompt: "Help me find the cause of this error:\n\n" },
  { icon: <Compass size={14} />, label: "Explain a concept", prompt: "Explain this in simple terms, with an example: " },
  { icon: <Sparkles size={14} />, label: "Brainstorm ideas", prompt: "Brainstorm ideas for " },
  { icon: <MessageSquare size={14} />, label: "Write a doc", prompt: "Write a document about " },
];

export default function ChatPage({ view, onViewChange, workTab, onWorkTabChange, onActiveSessionChange, onActiveBranchChange }: Props) {
  const mode = view;
  const [workspace, setWorkspace] = useState<string>("");
  const [goal, setGoal] = useState<GoalData | null>(null);
  const [goalComposerOpen, setGoalComposerOpen] = useState(false);
  const [goalTimelineOpen, setGoalTimelineOpen] = useState(false);
  const [goalSaving, setGoalSaving] = useState(false);
  const [selectedProvider, setSelectedProvider] = useState<string | null>(null);
  const [reasoningEffort, setReasoningEffort] = useState("medium");
  const [selectedAgent, setSelectedAgent] = useState("default");
  const [agentProfiles, setAgentProfiles] = useState<AgentProfileType[]>([]);
  const [defaultAgentName, setDefaultAgentName] = useState("");
  const enabledAgents = agentProfiles.filter((a) => a.enabled !== false);
  const defaultAgent = enabledAgents.find((a) => a.name === defaultAgentName);
  // With a default agent configured, the plain "default" assistant is not offered.
  const agentChoices: { name: string; icon?: string; display_name: string }[] = defaultAgent
    ? enabledAgents
    : [{ name: "default", icon: "🦀", display_name: "default" }, ...enabledAgents];
  // A chat's agent that was deleted or disabled falls back to the default one.
  const effectiveAgent = agentChoices.some((a) => a.name === selectedAgent)
    ? selectedAgent
    : (defaultAgent?.name ?? "default");
  const { agentRunBoardRuns, handleAgentRunBoardEvent, clearAgentRunBoard } =
    useAgentRunBoard();

  const {
    providers,
    catalog,
    models,
    providerModels,
    providersLoading,
    modelsLoading,
    modelsError,
    selectedModel,
    setSelectedModel,
    setProviders,
    setProvidersLoading,
    refreshProviders,
  } = useModelSelector();

  const effortOptions = reasoningOptions(providerModels, providers, selectedProvider, selectedModel);
  const activeEffort = effectiveEffort(reasoningEffort, effortOptions);

  const onAutoLoadSession = useCallback((session: Session) => {
    if (session.model) setSelectedModel(session.model);
    if (session.provider) setSelectedProvider(session.provider);
    if (session.workspace) setWorkspace(session.workspace);
    if (session.agent) setSelectedAgent(session.agent);
  }, [setSelectedModel]);

  // ── Document panel state (must be before useChatState) ─────────
  const [docState, setDocState] = useState<DocState | null>(null);
  const [currentDocPath, setCurrentDocPath] = useState<string | null>(null);

  // ── Document panel width (resizable + persistent) ──────────────
  // Layout: [SessionList 256px] [Chat flex-1] [DocPanel]
  // We must keep chat area >= 380px so the drag handle is always reachable.
  const DOC_PANEL_MIN = 360;
  const DOC_PANEL_DEFAULT = 520;
  const DOC_PANEL_STORAGE_KEY = "crabagent_doc_panel_width";
  const SESSION_LIST_W = 256; // w-64 = 16rem = 256px
  const CHAT_MIN_W = 380;

  /** Dynamic max: viewport minus sidebar minus minimum chat width */
  const docPanelMax = () => window.innerWidth - SESSION_LIST_W - CHAT_MIN_W;

  const [docPanelWidth, setDocPanelWidth] = useState<number>(() => {
    try {
      const saved = localStorage.getItem(DOC_PANEL_STORAGE_KEY);
      if (saved) {
        const w = parseInt(saved, 10);
        if (w >= DOC_PANEL_MIN && w <= docPanelMax()) return w;
      }
    } catch {}
    return Math.min(DOC_PANEL_DEFAULT, docPanelMax());
  });

  const [docPanelMaximized, setDocPanelMaximized] = useState(false);

  // ── Work page state ────────────────────────────────────────────
  const [mobileWorkChat, setMobileWorkChat] = useState(false);
  const [pendingHighlight, setPendingHighlight] = useState<{ path?: string; text?: string } | null>(null);
  // Kind of file open in the Documents tab.
  type WorkspaceType = "document" | "code" | "prototype" | "markdown";
  const [workspaceType, setWorkspaceType] = useState<WorkspaceType>("document");
  const [meetingActive, setMeetingActive] = useState(false);
  const [fileTreeRefreshKey, setFileTreeRefreshKey] = useState(0);
  // +1 after each finished iteration (LLM call done, token usage persisted) to drive the stats bar refresh
  const [statsTick, setStatsTick] = useState(0);

  /** Switch to the Work page, optionally on a given tab. */
  const openWork = useCallback((tab?: WorkTab) => {
    if (tab) onWorkTabChange(tab);
    onViewChange("work");
  }, [onViewChange, onWorkTabChange]);

  const handleStartMeeting = useCallback(() => {
    setMeetingActive(true);
    openWork("meeting");
  }, [openWork]);

  // What the agent is told about the Work page with each prompt: the open
  // file's kind on the Documents tab, otherwise the tab itself.
  const promptWorkspaceType = mode === "work" ? (workTab === "documents" ? workspaceType : workTab) : undefined;
  const promptWorkMode = mode === "work" && workTab === "documents";

  const docPanelWidthRef = useRef(docPanelWidth);
  docPanelWidthRef.current = docPanelWidth;

  const handleDocPanelResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = docPanelWidthRef.current;

    // Full-screen overlay to capture all mouse events (prevents iframe from stealing them)
    const overlay = document.createElement("div");
    overlay.style.cssText =
      "position:fixed;inset:0;z-index:9999;cursor:col-resize;";
    document.body.appendChild(overlay);

    const handleMouseMove = (ev: MouseEvent) => {
      const delta = startX - ev.clientX; // drag left → wider panel
      const maxW = docPanelMax();
      const newWidth = Math.min(maxW, Math.max(DOC_PANEL_MIN, startWidth + delta));
      setDocPanelWidth(newWidth);
    };

    const handleMouseUp = () => {
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
      overlay.remove();
      setDocPanelWidth((w) => {
        localStorage.setItem(DOC_PANEL_STORAGE_KEY, String(w));
        return w;
      });
    };

    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleMouseUp);
  }, []);

  const handleDocEvent = useCallback((event: SSEEvent) => {
    const { type, data } = event;
    if (type === "doc_op_start") {
      const file = (data.file as string) || "unknown";
      setDocState({
        fileName: file.split("/").pop() || file,
        filePath: file,
        busy: true,
        previewHtml: null,
        previewLoading: false,
        previewError: null,
        events: [{ message: `📄 ${data.operation as string} ${file}`, timestamp: Date.now(), status: "running" }],
      });
      // Show the Work page's Documents tab when the AI starts working on a document
      openWork("documents");
    } else if (type === "doc_op_delta") {
      setDocState((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          busy: true,
          events: [...prev.events, { message: data.message as string, timestamp: Date.now(), status: "running" }],
        };
      });
      // Track element_path for highlight
      if (data.element_path) {
        setPendingHighlight({ path: data.element_path as string });
      }
    } else if (type === "doc_op_preview") {
      setDocState((prev) => {
        if (!prev) return prev;
        return { ...prev, previewHtml: (data.html as string) || prev.previewHtml, previewLoading: false };
      });
    } else if (type === "doc_op_done") {
      setDocState((prev) => {
        if (!prev) return prev;
        const ok = data.status === "ok";
        // If we have a pending highlight from delta, keep it for the new preview
        return {
          ...prev,
          busy: false,
          previewLoading: false,
          events: [
            ...prev.events,
            { message: (data.message as string) || (ok ? "✅ Complete" : "❌ Failed"), timestamp: Date.now(), status: ok ? "done" : "error" },
          ],
        };
      });
      // Refresh file tree to show new/modified files
      setFileTreeRefreshKey((k) => k + 1);
    }
  }, [openWork]);

  const wrappedOnEvent = useCallback((event: SSEEvent) => {
    if (event.type === "iteration_end" || event.type === "sub_agent_end") {
      // That LLM call's token usage was just persisted — trigger a debounced stats bar refresh
      setStatsTick((k) => k + 1);
    }
    if (event.type.startsWith("goal_")) {
      const snapshot = event.data.goal as GoalData | undefined;
      // Completed or archived goals should not continue occupying the chat header.
      if (snapshot) setGoal(["complete", "cleared"].includes(snapshot.status) ? null : snapshot);
    }
    if (event.type.startsWith("doc_op_")) {
      handleDocEvent(event);
    } else {
      handleAgentRunBoardEvent(event);
    }
  }, [handleAgentRunBoardEvent, handleDocEvent]);

  const {
    sessions,
    setSessions,
    activeSession,
    setActiveSession,
    messages,
    setMessages,
    sending,
    setSending,
    connected,
    activeBranch,
    replaying,
    replayProgress,
    todoRefreshKey,
    activeMonitors,
    selectSession,
    newSession,
    selectSessionById,
    startReplay,
    handleSSEEvent,
    handleSwitchBranch,
    handleBranch,
    handleAbort,
    handleDeleteSession,
    getSubAgentContent,
    requestMessageImages,
  } = useChatState(wrappedOnEvent, workspace, onAutoLoadSession);

  // ── Open Office document from file tree ───────────────────────
  const handleOpenDoc = useCallback(async (path: string, name: string) => {
    const ws = activeSession?.workspace || workspace || "";
    // HTML and Markdown files go directly to their workspace mode, no doc preview
    if (name.toLowerCase().endsWith(".html") || name.toLowerCase().endsWith(".md")) {
      setCurrentDocPath(path);
      setDocState(null);
      openWork("documents");
      return;
    }
    setDocState({
      fileName: name,
      filePath: path,
      busy: true,
      previewHtml: null,
      previewLoading: true,
      previewError: null,
      events: [{ message: `📄 Opening ${name}…`, timestamp: Date.now(), status: "running" }],
      workspace: ws || undefined,
    });
    setCurrentDocPath(path);
    openWork("documents");
    let installTimer: ReturnType<typeof setTimeout> | null = null;
    try {
      const preview = await documentsApi.getPreview(path, ws, (status) => {
        // Show install progress in preview error area
        const pct = status.progress || 0;
        const msg = status.message || "Installing...";
        const progressMsg = `⏳ Installing OfficeCLI automatically... ${pct}%\n${msg}`;
        setDocState((prev) => prev ? { ...prev, previewError: progressMsg, previewLoading: true } : prev);
      });
      if (installTimer) clearTimeout(installTimer);
      setDocState((prev) => prev ? {
        ...prev,
        busy: false,
        previewLoading: false,
        previewHtml: preview.html,
        previewError: null,
        events: [...prev.events, { message: `✅ Opened ${name}`, timestamp: Date.now(), status: "done" }],
      } : prev);
    } catch (e: any) {
      if (installTimer) clearTimeout(installTimer);
      // If it was an install failure, show a more helpful message
      const isInstallFail = e?.message?.includes("OfficeCLI");
      setDocState((prev) => prev ? {
        ...prev,
        busy: false,
        previewLoading: false,
        previewError: isInstallFail
          ? "❌ OfficeCLI installation failed. Install it manually:\nwinget install HaiYing.OfficeCLI\nor visit https://github.com/iOfficeAI/OfficeCLI/releases"
          : (e?.message || "Failed to open document"),
        events: [...prev.events, { message: `❌ Failed: ${e?.message || "Unknown error"}`, timestamp: Date.now(), status: "error" }],
      } : prev);
    }
  }, [activeSession?.workspace, workspace, openWork]);

  // Sync active session ID to parent (for NavBar language switcher reset)
  useEffect(() => {
    onActiveBranchChange?.(activeBranch);
  }, [activeBranch, onActiveBranchChange]);

  useEffect(() => {
    onActiveSessionChange?.(activeSession?.session_id || null);
  }, [activeSession?.session_id, onActiveSessionChange]);

  useEffect(() => {
    let cancelled = false;
    if (!activeSession) {
      setGoal(null);
      return;
    }
    goalsApi.getGoal(activeSession.session_id).then(({ goal }) => {
      if (!cancelled) setGoal(goal);
    }).catch(() => {
      if (!cancelled) setGoal(null);
    });
    return () => { cancelled = true; };
  }, [activeSession?.session_id]);

  const saveGoal = useCallback(async (draft: GoalDraft) => {
    if (!activeSession) return;
    setGoalSaving(true);
    try {
      const selectedEntry = providerModels.find((item) =>
        item.provider.name === selectedProvider
        && item.models.some((model) => model.id === selectedModel),
      );
      const provider = selectedEntry?.provider.name ?? providerModels.find((item) =>
        item.models.some((model) => model.id === selectedModel),
      )?.provider.name;
      const execution = {
        execution_model: selectedModel,
        execution_provider: provider ?? "",
        execution_agent: effectiveAgent,
        reasoning_effort: activeEffort,
      };
      const result = goal
        ? await goalsApi.updateGoal(activeSession.session_id, draft)
        : await goalsApi.createGoal(activeSession.session_id, { ...draft, ...execution });
      setGoal(result.goal);
      if (!goal) {
        await sessionsApi.sendPrompt(
          activeSession.session_id,
          `I created the current session goal: ${result.goal.objective}\nReview the current state, outline a plan, then start making progress.`,
          execution.execution_model,
          undefined,
          execution.execution_agent,
          execution.reasoning_effort,
          execution.execution_provider || undefined,
        );
      }
    } finally {
      setGoalSaving(false);
    }
  }, [
    activeSession,
    goal,
    selectedModel,
    selectedProvider,
    providerModels,
    effectiveAgent,
    activeEffort,
  ]);

  const setGoalStatus = useCallback(async (status: "active" | "paused") => {
    if (!activeSession) return;
    setGoalSaving(true);
    try {
      const { goal } = await goalsApi.updateGoal(activeSession.session_id, { status, stop_reason: status === "paused" ? "Paused by user" : "" });
      setGoal(goal);
    } finally {
      setGoalSaving(false);
    }
  }, [activeSession]);

  // Sync user-driven model/provider/effort changes into the active goal.
  // Without this, prompt.py keeps overriding context.model with the goal's
  // frozen execution_model, so switching the selector had no effect while a
  // goal was running.
  const syncGoalExecution = useCallback(
    async (patch: { execution_model?: string; execution_provider?: string; reasoning_effort?: string }) => {
      if (!activeSession || !goal) return;
      if (goal.status && ["complete", "cleared"].includes(goal.status)) return;
      const unchanged =
        (patch.execution_model === undefined || patch.execution_model === goal.execution_model)
        && (patch.execution_provider === undefined || patch.execution_provider === goal.execution_provider)
        && (patch.reasoning_effort === undefined || patch.reasoning_effort === goal.reasoning_effort);
      if (unchanged) return;
      try {
        const { goal: updated } = await goalsApi.updateGoal(activeSession.session_id, patch);
        setGoal(updated);
      } catch {
        // Non-fatal: goal update is best-effort; the run still uses the selector value via req.model.
      }
    },
    [activeSession, goal],
  );

  const handleModelChange = useCallback(
    (modelId: string, providerName: string | null) => {
      setSelectedModel(modelId);
      setSelectedProvider(providerName);
      void syncGoalExecution({ execution_model: modelId, execution_provider: providerName ?? "", reasoning_effort: effectiveEffort(reasoningEffort, reasoningOptions(providerModels, providers, providerName, modelId)) });
    },
    [syncGoalExecution, reasoningEffort, providerModels, providers],
  );

  const handleModelInput = useCallback(
    (modelId: string) => {
      setSelectedModel(modelId);
      void syncGoalExecution({ execution_model: modelId, reasoning_effort: effectiveEffort(reasoningEffort, reasoningOptions(providerModels, providers, selectedProvider, modelId)) });
    },
    [syncGoalExecution, reasoningEffort, providerModels, providers, selectedProvider],
  );

  const handleEffortChange = useCallback(
    (effort: string) => {
      setReasoningEffort(effort);
      void syncGoalExecution({ reasoning_effort: effort });
    },
    [syncGoalExecution],
  );

  const [showProviders, setShowProviders] = useState(false);
  const [showCompression, setShowCompression] = useState(false);
  const [compressionModel, setCompressionModel] = useState("");
  const [compressionProvider, setCompressionProvider] = useState("");
  const [compressing, setCompressing] = useState(false);
  const [compressionError, setCompressionError] = useState("");
  const [showMcpServers, setShowMcpServers] = useState(false);
  const [showTasks, setShowTasks] = useState(false);
  const [showEmail, setShowEmail] = useState(false);

  useEffect(() => {
    if (!selectedModel || providerModels.length === 0) return;
    const providerMatchesModel = providerModels.some((item) =>
      item.provider.name === selectedProvider
      && item.models.some((model) => model.id === selectedModel),
    );
    if (providerMatchesModel) return;

    const matchingProvider = providerModels.find((item) =>
      item.models.some((model) => model.id === selectedModel),
    );
    setSelectedProvider(matchingProvider?.provider.name ?? null);
  }, [selectedModel, providerModels, selectedProvider]);
  const [showScheduledTasks, setShowScheduledTasks] = useState(false);
  const [showSkills, setShowSkills] = useState(false);
  const [viewingSubAgent, setViewingSubAgent] = useState<string | null>(null);
  const [showDelegate, setShowDelegate] = useState(false);
  const [showResultCompare, setShowResultCompare] = useState(false);
  const [showFiles, setShowFiles] = useState(false);
  const [showWorkFiles, setShowWorkFiles] = useState(false);
  const [pendingImages, setPendingImages] = useState<string[]>([]);
  const [pendingFiles, setPendingFiles] = useState<{ name: string; path: string; size: number }[]>([]);
  const [agentOpen, setAgentOpen] = useState(false);
  const [effortOpen, setEffortOpen] = useState(false);
  // The work-mode sidebar and the main chat area each render an Agent/Effort toolbar with its own anchor ref;
  // FloatingMenu renders into body via portal, so the portal menu ref is used for outside-click dismissal.
  const sidebarAgentRef = useRef<HTMLDivElement>(null);
  const mainAgentRef = useRef<HTMLDivElement>(null);
  const sidebarEffortRef = useRef<HTMLDivElement>(null);
  const mainEffortRef = useRef<HTMLDivElement>(null);
  const agentMenuRef = useRef<HTMLDivElement>(null);
  const effortMenuRef = useRef<HTMLDivElement>(null);
  const [mcpServers, setMcpServers] = useState<McpServer[]>([]);
  const [mcpStatus, setMcpStatus] = useState<McpServerStatus[]>([]);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);

  useEffect(() => {
    mcpServersApi.listMcpServers().then(setMcpServers);
    mcpServersApi.getMcpStatus().then(setMcpStatus);
  }, []);

  // Agents and the "default agent for new chats" setting; reloaded when the Agents page changes them.
  useEffect(() => {
    const loadAgents = () => {
      listAgentProfiles().then(setAgentProfiles).catch(() => {});
      getSettings()
        .then((values) => setDefaultAgentName((values[DEFAULT_AGENT_SETTING] || "").trim()))
        .catch(() => {});
    };
    loadAgents();
    window.addEventListener(AGENTS_CHANGED_EVENT, loadAgents);
    return () => window.removeEventListener(AGENTS_CHANGED_EVENT, loadAgents);
  }, []);

  useEffect(() => {
    const interval = setInterval(() => {
      mcpServersApi.getMcpStatus().then(setMcpStatus).catch(() => {});
    }, 60000);
    return () => clearInterval(interval);
  }, []);

  // Global keyboard shortcuts: / focus input, Cmd+K new session
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      const isTyping =
        target.tagName === "INPUT" ||
        target.tagName === "TEXTAREA" ||
        target.isContentEditable;
      if (e.key === "/" && !isTyping) {
        e.preventDefault();
        document.querySelector<HTMLTextAreaElement>('[placeholder="Type a message…"]')?.focus();
      }
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        onNewSession();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedModel, models]);

  // Click outside to close agent/effort dropdowns
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      const target = e.target as Node;
      const inAgent = [sidebarAgentRef.current, mainAgentRef.current, agentMenuRef.current]
        .some((el) => el?.contains(target));
      if (agentOpen && !inAgent) {
        setAgentOpen(false);
      }
      const inEffort = [sidebarEffortRef.current, mainEffortRef.current, effortMenuRef.current]
        .some((el) => el?.contains(target));
      if (effortOpen && !inEffort) {
        setEffortOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [agentOpen, effortOpen]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setAgentOpen(false);
        setEffortOpen(false);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const onSelectSession = useCallback(
    async (session: Session) => {
      const model = await selectSession(session, selectedModel, models);
      setSelectedModel(model);
      if (session.agent) {
        setSelectedAgent(session.agent);
      }
      if (session.provider) {
        setSelectedProvider(session.provider);
      } else if (model && providerModels.length > 0) {
        for (const pm of providerModels) {
          if (pm.models.some((m) => m.id === model)) {
            setSelectedProvider(pm.provider.name);
            break;
          }
        }
      }
      clearAgentRunBoard();
    },
    [selectSession, selectedModel, models, setSelectedModel, providerModels, clearAgentRunBoard],
  );

  const onNewSession = useCallback(async () => {
    const model = await newSession(selectedModel, models);
    setSelectedModel(model);
    setSelectedAgent("default");
    clearAgentRunBoard();
  }, [newSession, selectedModel, models, setSelectedModel, clearAgentRunBoard]);

  const handleToggleHistorySearchable = useCallback(async (session: Session) => {
    try {
      const updated = await sessionsApi.updateSession(session.session_id, {
        history_searchable: !session.history_searchable,
      });
      setSessions((current) => current.map((item) => item.session_id === updated.session_id ? updated : item));
      if (activeSession?.session_id === updated.session_id) setActiveSession(updated);
    } catch (error) {
      console.error("Failed to update historical search access", error);
    }
  }, [activeSession?.session_id, setActiveSession, setSessions]);

  const onSelectSessionById = useCallback(
    async (sessionId: string) => {
      const model = await selectSessionById(sessionId, selectedModel, models);
      setSelectedModel(model);
    },
    [selectSessionById, selectedModel, models, setSelectedModel],
  );

  const _OFFICE_EXTS = [".docx", ".xlsx", ".pptx"];
  const _TEXT_EXTS = [".pdf", ".csv", ".txt", ".md", ".json", ".xml", ".html", ".htm", ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".c", ".cpp", ".h", ".sh", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".sql", ".r", ".rb", ".php", ".css", ".scss", ".vue", ".svelte"];

  const processImageFile = (file: File) => {
    if (pendingImages.length >= 5) return;
    if (file.size > 5 * 1024 * 1024) return;
    if (!file.type.startsWith("image/")) return;
    const reader = new FileReader();
    reader.onload = (e) => {
      const dataUrl = e.target?.result as string;
      if (dataUrl) setPendingImages((prev) => [...prev, dataUrl]);
    };
    reader.readAsDataURL(file);
  };

  const processDocFile = async (file: File) => {
    const ext = "." + file.name.split(".").pop()?.toLowerCase();
    if (!_OFFICE_EXTS.includes(ext) && !_TEXT_EXTS.includes(ext)) return;

    try {
      const { uploadFile } = await import("../api/files");
      const result = await uploadFile(file);
      setPendingFiles((prev) => [
        ...prev,
        { name: file.name, path: result.path, size: result.size },
      ]);
    } catch (e) {
      console.error("File upload failed:", e);
    }
  };

  const processFile = (file: File) => {
    if (file.type.startsWith("image/")) {
      processImageFile(file);
    } else {
      processDocFile(file);
    }
  };

  const handleFilePaste = (e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    let hasFile = false;
    for (const item of Array.from(items)) {
      if (item.kind === "file") {
        const file = item.getAsFile();
        if (file) {
          hasFile = true;
          processFile(file);
        }
      }
    }
    if (hasFile) e.preventDefault();
  };

  const handleFileUpload = () => {
    const el = document.createElement("input");
    el.type = "file";
    el.accept = "image/*,.docx,.xlsx,.pptx,.pdf,.csv,.txt,.md,.json,.xml,.html,.htm";
    el.multiple = true;
    el.onchange = (e) => {
      const files = (e.target as HTMLInputElement).files;
      if (files) Array.from(files).forEach(processFile);
    };
    el.click();
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    const files = e.dataTransfer?.files;
    if (files) Array.from(files).forEach(processFile);
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
  };

  const parseAgentMentions = (text: string, profiles: AgentProfileType[]) => {
    const enabled = profiles.filter((p) => p.enabled);
    const matches: AgentProfileType[] = [];
    const tokens = text.split(/\s+/);
    for (const p of enabled) {
      if (tokens.includes(`@${p.name}`)) matches.push(p);
    }
    return matches;
  };

  /** Until the model-written title arrives, name a new chat after the start of its first message. */
  const showPlaceholderTitle = (text: string) => {
    const sid = activeSession?.session_id;
    if (!sid || activeSession?.title) return;
    const clean = text.replace(/(^|\s)@[\w-]+/g, " ").replace(/\s+/g, " ").trim();
    if (!clean) return;
    const title = clean.length > 50
      ? clean.slice(0, 50).replace(/\s+\S*$/, "").replace(/[\s,.;:]+$/, "") + "…"
      : clean;
    setSessions((prev) => prev.map((s) => (s.session_id === sid && !s.title ? { ...s, title } : s)));
  };

  const doSend = async (text: string, images: string[], files: { name: string; path: string; size: number }[] = []) => {
    if (!activeSession) return;
    const wasSending = sending;
    showPlaceholderTitle(text);

    // Append file references to the prompt text
    if (files.length > 0) {
      const fileList = files.map((f) => `- ${f.path} (${f.name}, ${(f.size / 1024).toFixed(0)}KB)`).join("\n");
      text = text + (text ? "\n\n" : "") + `📎 Attachments:\n${fileList}\n\nRead the attachments first, then handle my request.`;
    }

    setMessages((prev) => [
      ...prev,
      {
        id: `u-${Date.now()}`,
        role: "user",
        content: text,
        images: images.length > 0 ? images : undefined,
      },
    ]);
    setSending(true);
    try {
      const mentions = parseAgentMentions(text, agentProfiles);
      if (mentions.length > 0) {
        const mainText = text.replace(/@\w+/g, "").replace(/\s+/g, " ").trim();
        if (mentions.length === 1) {
          const taskText = mainText || "Please complete this task";
          text = `[delegate_task] agent_name="${mentions[0].name}" task="${taskText.replace(/"/g, "'")}"\n\nOriginal request: ${text}`;
        } else {
          const tasksJson = mentions.map((m) => ({
            agent_name: m.name,
            task: mainText || "Please complete this task",
          }));
          text = `[delegate_parallel] tasks=${JSON.stringify(tasksJson)}\n\nOriginal request: ${text}`;
        }
      }
      await sessionsApi.sendPrompt(
        activeSession.session_id,
        text || "Please analyze this image",
        selectedModel,
        images.length > 0 ? images : undefined,
        effectiveAgent,
        activeEffort,
        selectedProvider ?? undefined,
        currentDocPath || undefined,
        promptWorkspaceType,
        promptWorkMode,
      );
    } catch (error) {
      if (!wasSending) setSending(false);
      toast.error(error instanceof Error ? error.message : "Message could not be sent");
    }
  };

  const handleSend = async (text: string) => {
    if ((!text.trim() && pendingImages.length === 0 && pendingFiles.length === 0) || !activeSession)
      return;

    const goalCommand = text.trim().match(/^\/goal(?:\s+([\s\S]+))?$/);
    if (goalCommand) {
      if (goalCommand[1]) {
        await saveGoal({
          objective: goalCommand[1].trim(),
          success_criteria: [],
          constraints: [],
          auto_continue: false,
        });
      } else {
        setGoalComposerOpen(true);
      }
      return;
    }

    const images = [...pendingImages];
    const files = [...pendingFiles];
    setPendingImages([]);
    setPendingFiles([]);
    doSend(text, images, files);
  };

  const handleToolConfirm = async (confirmId: string, approved: boolean) => {
    if (!activeSession) return;
    setMessages((prev) =>
      prev.map((m) =>
        m.confirm_id === confirmId ? { ...m, confirmed: approved } : m,
      ),
    );
    try {
      await sessionsApi.confirmTool(activeSession.session_id, confirmId, approved);
    } catch {
      // ignore
    }
  };

  const handleUserInput = async (inputId: string, answer: string) => {
    if (!activeSession) return;
    setMessages((prev) =>
      prev.map((m) =>
        m.confirm_id === inputId ? { ...m, confirmed: true, input_answer: answer } : m,
      ),
    );
    try {
      await sessionsApi.submitInput(activeSession.session_id, inputId, answer);
    } catch {
      // ignore
    }
  };

  const handleDelegateFromModal = async (
    tasks: { agent_name: string; task: string }[],
  ) => {
    if (!activeSession || sending) return;
    setShowDelegate(false);
    const names = tasks.map((t) => `@${t.agent_name}`).join(" ");
    const taskDesc =
      tasks.length === 1
        ? tasks[0].task
        : tasks.map((t) => `${t.agent_name}: ${t.task}`).join("; ");
    showPlaceholderTitle(taskDesc);
    setMessages((prev) => [
      ...prev,
      {
        id: `u-${Date.now()}`,
        role: "user",
        content: `${names} ${taskDesc}`,
      },
    ]);
    setSending(true);
    try {
      let promptText: string;
      if (tasks.length === 1) {
        promptText = `[delegate_task] agent_name="${tasks[0].agent_name}" task="${tasks[0].task.replace(/"/g, "'")}"`;
      } else {
        promptText = `[delegate_parallel] tasks=${JSON.stringify(tasks)}`;
      }
      await sessionsApi.sendPrompt(activeSession.session_id, promptText, selectedModel, undefined, undefined, undefined, selectedProvider ?? undefined);
    } catch {
      setSending(false);
    }
  };

  const handleExportReport = async () => {
    if (!activeSession) return;
    const token = localStorage.getItem("crab_token") || "";
    const resp = await fetch(
      `/api/sessions/${encodeURIComponent(activeSession.session_id)}/report?token=${encodeURIComponent(token)}`,
    );
    const text = await resp.text();
    const blob = new Blob([text], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `crabagent-report-${activeSession.session_id.slice(0, 8)}.md`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const quickActionPrompts: Record<string, string> = {
    digest: "Generate and send my daily digest",
    remind: "Check my tasks and send reminders for anything due soon",
    inbox: "Check my email for new messages",
  };

  const handleQuickAction = useCallback(
    async (action: "digest" | "remind" | "inbox") => {
      const promptText = quickActionPrompts[action];
      if (!promptText) return;
      // Ensure we have an active session
      let sid = activeSession?.session_id;
      if (!sid) {
        const model = await newSession(selectedModel, models);
        setSelectedModel(model);
        sid = ""; // newSession sets activeSession via state; next render will have it
      }
      if (!activeSession && !sid) return;
      const targetSid = activeSession?.session_id || sid;
      if (!targetSid) return;
      setMessages((prev) => [
        ...prev,
        { id: `u-${Date.now()}`, role: "user", content: promptText },
      ]);
      setSending(true);
      try {
        await sessionsApi.sendPrompt(targetSid, promptText, selectedModel, undefined, undefined, undefined, selectedProvider ?? undefined);
      } catch {
        setSending(false);
      }
    },
    [activeSession, selectedModel, models, selectedProvider, newSession, setMessages, setSending, setSelectedModel, quickActionPrompts],
  );

  const handleNotificationAction = useCallback(
    (n: { title: string; body: string }) => {
      const textarea = document.querySelector<HTMLTextAreaElement>("textarea");
      if (textarea) {
        const text = `${"Please help me handle this email:"}\n\n${n.body}`;
        const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
          window.HTMLTextAreaElement.prototype, "value"
        )?.set;
        nativeInputValueSetter?.call(textarea, text);
        textarea.dispatchEvent(new Event("input", { bubbles: true }));
        textarea.focus();
      }
    },
    [],
  );

  // ── Workspace type detection ──────────────────────────────
  const CODE_EXTS = new Set(["py", "ts", "tsx", "js", "jsx", "json", "go", "rs", "java", "c", "cpp", "css", "scss", "vue", "svelte", "sh", "bash", "yaml", "yml", "toml", "sql"]);
  const detectWorkspaceType = (name: string): WorkspaceType => {
    const ext = name.split(".").pop()?.toLowerCase() || "";
    if (ext === "html") return "prototype";
    if (ext === "md") return "markdown";
    if (CODE_EXTS.has(ext)) return "code";
    return "document";
  };

  useEffect(() => {
    if (currentDocPath) {
      const type = detectWorkspaceType(currentDocPath);
      setWorkspaceType(type);
    }
  }, [currentDocPath]);

  const handleAIEdit = useCallback((instruction: string, selectedText: string) => {
    // Workspace context (file path, type) is already injected via workspace message.
    // Only need to send the selected text + instruction.
    if (!activeSession) return;
    const text = selectedText
      ? `Modify this content (original):\n\n> ${selectedText}\n\nChange request: ${instruction}`
      : instruction;
    if (text.trim() && !sending) {
      setMessages((prev) => [...prev, { id: `u-${Date.now()}`, role: "user", content: text }]);
      setSending(true);
      sessionsApi.sendPrompt(
        activeSession.session_id, text, selectedModel, undefined, effectiveAgent,
        activeEffort, selectedProvider ?? undefined,
        currentDocPath || undefined,
        promptWorkspaceType,
        promptWorkMode,
      )
        .catch(() => setSending(false));
    }
  }, [activeSession, selectedModel, effectiveAgent, activeEffort, selectedProvider, currentDocPath, promptWorkspaceType, promptWorkMode, sending, setMessages, setSending]);

  const completedTasks = agentRunBoardRuns.filter((t) => t.status === "done");

  const openCompression = () => {
    const matchedProvider = providerModels.find((item) =>
      item.models.some((model) => model.id === selectedModel),
    )?.provider.name || "";
    setCompressionModel(selectedModel);
    setCompressionProvider(selectedProvider || matchedProvider);
    setCompressionError("");
    setShowCompression(true);
  };

  const runCompression = async () => {
    if (!activeSession || !compressionModel || !compressionProvider) return;
    setCompressing(true);
    setCompressionError("");
    try {
      await sessionsApi.compressSession(activeSession.session_id, compressionModel, compressionProvider);
      const refreshed = await sessionsApi.getMessages(activeSession.session_id);
      setMessages(dbMessagesToChat(refreshed));
      setShowCompression(false);
    } catch (err) {
      setCompressionError(err instanceof Error ? err.message : "Compression failed");
    } finally {
      setCompressing(false);
    }
  };

  // ── Composer pieces shared by chat mode, work mode and the start screen ──

  const agentLabel = (() => {
    const p = agentChoices.find((a) => a.name === effectiveAgent);
    return `${p?.icon || "🤖"} ${p?.display_name || effectiveAgent}`;
  })();

  const renderComposerControls = (
    agentRef: React.RefObject<HTMLDivElement | null>,
    effortRef: React.RefObject<HTMLDivElement | null>,
  ) => (
    <>
      {agentChoices.length > 1 && (
        <div ref={agentRef} className="relative shrink-0">
          <button
            type="button"
            onClick={() => setAgentOpen((v) => !v)}
            aria-expanded={agentOpen}
            title={"Agent"}
            className="composer-chip"
          >
            <span className="truncate max-w-[120px]">{agentLabel}</span>
            <ChevronDown size={13} className={cn("shrink-0 opacity-60 transition-transform", agentOpen && "rotate-180")} />
          </button>
          {agentOpen && (
            <FloatingMenu
              open={agentOpen}
              anchorRef={agentRef}
              menuRef={agentMenuRef}
              placement="top-end"
              gap={6}
              className="min-w-[180px] rounded-xl border border-[var(--border)] bg-[var(--bg-secondary)] shadow-[var(--shadow-lg)] p-1"
            >
              {agentChoices.map((a) => {
                const isSelected = a.name === effectiveAgent;
                return (
                  <button
                    key={a.name}
                    onClick={async () => {
                      setSelectedAgent(a.name);
                      setAgentOpen(false);
                      if (activeSession) {
                        try { await sessionsApi.switchAgent(activeSession.session_id, a.name); } catch {}
                      }
                    }}
                    className={cn(
                      "w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-[13px] text-left transition-colors",
                      isSelected ? "bg-[var(--bg-tertiary)] text-[var(--text-primary)]" : "text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] hover:text-[var(--text-primary)]",
                    )}
                  >
                    <span>{a.icon || "🤖"}</span>
                    <span className="flex-1 truncate">{a.display_name}</span>
                    {isSelected && <Check size={14} className="shrink-0 text-[var(--brand)]" />}
                  </button>
                );
              })}
            </FloatingMenu>
          )}
        </div>
      )}
      {modelsLoading ? (
        <span className="composer-chip pointer-events-none shrink-0">
          <Loader2 size={12} className="animate-spin" />
          {"Loading models…"}
        </span>
      ) : providerModels.length > 0 ? (
        <ModelSelector
          variant="chip"
          className="shrink-0"
          providerModels={providerModels}
          selectedModel={selectedModel}
          selectedProvider={selectedProvider}
          onChange={handleModelChange}
          disabled={modelsLoading}
        />
      ) : (
        <span className="flex shrink-0 items-center gap-1">
          <input
            type="text"
            value={selectedModel}
            onChange={(e) => handleModelInput(e.target.value)}
            placeholder={"type model id…"}
            title={"Model"}
            aria-label={"Model"}
            className="h-8 w-28 sm:w-36 rounded-lg bg-transparent px-2 text-xs font-mono text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)] hover:bg-[var(--bg-tertiary)] focus:bg-[var(--bg-tertiary)] focus:outline-none"
          />
          {modelsError && (
            <button
              type="button"
              className="text-[11px] text-[var(--danger)] hover:underline"
              title={modelsError}
              onClick={() => window.location.reload()}
            >
              {"Retry"}
            </button>
          )}
        </span>
      )}
      {effortOptions.length > 0 && (
        <div ref={effortRef} className="relative shrink-0">
          <button
            type="button"
            onClick={() => setEffortOpen((v) => !v)}
            aria-expanded={effortOpen}
            title={"Reasoning effort"}
            className="composer-chip"
          >
            <span>{activeEffort || "effort"}</span>
            <ChevronDown size={13} className={cn("shrink-0 opacity-60 transition-transform", effortOpen && "rotate-180")} />
          </button>
          {effortOpen && (
            <FloatingMenu
              open={effortOpen}
              anchorRef={effortRef}
              menuRef={effortMenuRef}
              placement="top-end"
              gap={6}
              className="min-w-[140px] rounded-xl border border-[var(--border)] bg-[var(--bg-secondary)] shadow-[var(--shadow-lg)] p-1"
            >
              <div className="px-2.5 pt-1 pb-1.5 text-[11px] text-[var(--text-tertiary)]">{"Reasoning effort"}</div>
              {effortOptions.map((effort) => {
                const isSelected = effort === activeEffort;
                return (
                  <button
                    key={effort}
                    onClick={() => {
                      handleEffortChange(effort);
                      setEffortOpen(false);
                    }}
                    className={cn(
                      "w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-[13px] text-left transition-colors",
                      isSelected ? "bg-[var(--bg-tertiary)] text-[var(--text-primary)]" : "text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] hover:text-[var(--text-primary)]",
                    )}
                  >
                    <span className="flex-1">{effort}</span>
                    {isSelected && <Check size={14} className="shrink-0 text-[var(--brand)]" />}
                  </button>
                );
              })}
            </FloatingMenu>
          )}
        </div>
      )}
    </>
  );

  const hasAttachments = pendingImages.length > 0 || pendingFiles.length > 0;
  const composerAttachments = hasAttachments ? (
    <div className="flex flex-wrap gap-2 px-3 pt-3">
      {pendingImages.map((img, i) => (
        <div key={`img-${i}`} className="relative">
          <img
            src={img}
            className="h-14 w-14 object-cover rounded-lg border border-[var(--border)]"
            alt=""
          />
          <button
            type="button"
            onClick={() => setPendingImages((prev) => prev.filter((_, idx) => idx !== i))}
            className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full flex items-center justify-center bg-[var(--text-primary)] text-[var(--bg-primary)] shadow-[var(--shadow-sm)] hover:opacity-80 transition-opacity"
            aria-label={"Remove image"}
          >
            <X size={11} />
          </button>
        </div>
      ))}
      {pendingFiles.map((f, i) => (
        <div key={`file-${i}`} className="relative flex items-center gap-2 h-14 pl-2.5 pr-3 rounded-lg border border-[var(--border)] bg-[var(--bg-primary)] max-w-[220px]">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-[var(--bg-tertiary)] text-[var(--text-secondary)]">
            <FileText size={15} />
          </span>
          <div className="min-w-0">
            <div className="text-xs text-[var(--text-primary)] truncate">{f.name}</div>
            <div className="text-[10px] uppercase tracking-wide text-[var(--text-tertiary)]">
              {f.name.includes(".") ? f.name.split(".").pop() : "file"} · {(f.size / 1024).toFixed(0)} KB
            </div>
          </div>
          <button
            type="button"
            onClick={() => setPendingFiles((prev) => prev.filter((_, idx) => idx !== i))}
            className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full flex items-center justify-center bg-[var(--text-primary)] text-[var(--bg-primary)] shadow-[var(--shadow-sm)] hover:opacity-80 transition-opacity"
            aria-label="Remove file"
          >
            <X size={11} />
          </button>
        </div>
      ))}
    </div>
  ) : null;

  // ── Start screen: the first message creates the session, then is sent
  // once the new session's event stream is connected (so no reply is missed).
  const [firstMessage, setFirstMessage] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [starterSeed, setStarterSeed] = useState<{ text: string; key: number } | undefined>();

  const startConversation = async (text: string) => {
    if (starting) return;
    setStarting(true);
    try {
      // Keep the agent picked on the start screen (onNewSession resets it).
      const model = await newSession(selectedModel, models);
      setSelectedModel(model);
      clearAgentRunBoard();
      setFirstMessage(text);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not start a conversation");
    } finally {
      setStarting(false);
    }
  };

  useEffect(() => {
    if (firstMessage === null || !activeSession) return;
    const send = () => {
      setFirstMessage(null);
      void handleSend(firstMessage);
    };
    if (connected) {
      send();
      return;
    }
    // Fall back after a few seconds; the server keeps the reply in the session.
    const timer = setTimeout(send, 4000);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [firstMessage, activeSession?.session_id, connected]);

  const greeting = (() => {
    const hour = new Date().getHours();
    if (hour < 5) return "Up late?";
    if (hour < 12) return "Good morning";
    if (hour < 18) return "Good afternoon";
    return "Good evening";
  })();

  return (
    <div className="chat-page-background relative flex h-full overflow-hidden bg-[var(--bg-primary)]">
      {/* ── Session List ── */}
      {mode === "chat" && (
        <SessionList
          sessions={sessions}
          activeId={activeSession?.session_id || null}
          onSelect={onSelectSession}
          onNew={onNewSession}
          onDelete={handleDeleteSession}
          onToggleHistorySearchable={handleToggleHistorySearchable}
          onOpenProviders={() => setShowProviders(true)}
          onOpenMcpServers={() => setShowMcpServers(true)}
          onOpenTasks={() => setShowTasks(true)}
          onOpenEmail={() => setShowEmail(true)}
          onOpenScheduledTasks={() => setShowScheduledTasks(true)}
          onOpenSkills={() => setShowSkills(true)}
          onQuickAction={handleQuickAction}
          mobileOpen={mobileSidebarOpen}
          onMobileClose={() => setMobileSidebarOpen(false)}
          workspace={activeSession?.workspace || workspace || undefined}
          activeMonitors={activeMonitors}
        />
      )}
      {mode === "work" && (
        <div className="w-12 shrink-0 border-r border-[var(--border)] bg-[var(--bg-secondary)] flex flex-col items-center py-2 gap-2">
          <button
            onClick={() => onViewChange("chat")}
            className="w-8 h-8 flex items-center justify-center rounded-lg hover:bg-[var(--bg-tertiary)] text-[var(--text-tertiary)] hover:text-[var(--text-primary)] transition-colors"
            title={"Sessions"}
          >
            <MessageSquare size={16} />
          </button>
          <button onClick={() => setMobileWorkChat((value) => !value)} className="md:hidden w-8 h-8 flex items-center justify-center rounded-lg hover:bg-[var(--bg-tertiary)] text-[var(--text-secondary)]" aria-label={mobileWorkChat ? "Work Mode" : "Chat Mode"} title={mobileWorkChat ? "Work Mode" : "Chat Mode"}>
            <LayoutPanelLeft size={16} />
          </button>
        </div>
      )}

      {mode === "work" ? (
        /* ── WORK MODE ───────────────────────────────────────── */
        <>
          {/* AI Chat — left sidebar (350px) */}
          <div className={cn("w-[350px] shrink-0 min-h-0 overflow-hidden border-r border-[var(--border)] flex-col md:flex", mobileWorkChat ? "flex max-md:flex-1 max-md:w-0" : "hidden")}>
            <div className="flex-1 min-h-0 flex flex-col">
              {activeSession ? (
                <>
                  {replaying && replayProgress.total > 0 && (
                    <div className="px-4 pt-2 pb-1 border-b border-[var(--border)] bg-[var(--bg-secondary)]">
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-xs text-[var(--success)] flex items-center gap-1.5">
                          <Sparkles size={11} className="animate-pulse" />
                          {"Replaying branch"}
                        </span>
                        <span className="text-xs text-[var(--text-tertiary)] font-mono">
                          {replayProgress.current} / {replayProgress.total}
                        </span>
                      </div>
                      <div className="h-1 rounded-full overflow-hidden bg-[var(--bg-tertiary)]">
                        <div
                          className="h-full transition-all duration-200 rounded-full bg-gradient-to-r from-[var(--success)] to-[var(--accent)]"
                          style={{
                            width: `${(replayProgress.current / replayProgress.total) * 100}%`,
                          }}
                        />
                      </div>
                    </div>
                  )}
                  {goal && <GoalCard goal={goal} busy={goalSaving || sending} onPause={() => setGoalStatus("paused")} onResume={() => setGoalStatus("active")} onEdit={() => setGoalComposerOpen(true)} onHistory={() => setGoalTimelineOpen(true)} />}
                  <ChatPanel
                    sessionId={activeSession.session_id}
                    onVisibleImage={requestMessageImages}
                    messages={messages}
                    connected={connected}
                    sending={sending}
                    onToolConfirm={handleToolConfirm}
                    onUserInput={handleUserInput}
                    onBranch={handleBranch}
                    replaying={replaying}
                    externalSubAgentId={viewingSubAgent}
                    onSubAgentModalClose={() => setViewingSubAgent(null)}
                    getSubAgentContent={getSubAgentContent}
                  />
                  <McpStatusBar status={mcpStatus} />

                  <div
                    className="shrink-0 px-2 pt-1"
                    style={{ paddingBottom: "max(0.5rem, env(safe-area-inset-bottom))" }}
                    onDrop={handleDrop}
                    onDragOver={handleDragOver}
                  >
                    <SessionStatsBar
                      sessionId={activeSession?.session_id ?? null}
                      refreshKey={messages.length * 100 + (sending ? 1 : 0) + statsTick}
                    />

                    <ChatInput
                      sending={sending}
                      replaying={replaying}
                      onSend={handleSend}
                      onAbort={handleAbort}
                      onFileUpload={handleFileUpload}
                      onFilePaste={handleFilePaste}
                      onDelegateOpen={() => setShowDelegate(true)}
                      showDelegate={true}
                      onGoalOpen={() => setGoalComposerOpen(true)}
                      hasGoal={!!goal}
                      toolbar={renderComposerControls(sidebarAgentRef, sidebarEffortRef)}
                      attachments={composerAttachments}
                      canSendEmpty={hasAttachments}
                    />
                  </div>
                </>
              ) : (
                <div className="flex-1 flex items-center justify-center px-4">
                  <div className="text-center">
                    <div aria-hidden="true" className="mb-2 text-3xl">🦀</div>
                    <p className="font-display text-xl text-[var(--text-primary)]">{greeting}</p>
                    <button type="button" onClick={onNewSession} className="mt-3 text-[13px] text-[var(--text-secondary)] hover:text-[var(--text-primary)] underline underline-offset-4 decoration-[var(--border-strong)]">
                      {"Start a new conversation"}
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Workspace — right side (flex-1) */}
          <div className={cn("flex-1 flex-col min-w-0 bg-[var(--bg-primary)] md:flex", mobileWorkChat ? "hidden" : "flex")}>
            {/* Work tabs */}
            <div className="flex items-center gap-1 px-3 h-11 overflow-x-auto overscroll-x-contain border-b border-[var(--border)] bg-[var(--bg-secondary)] shrink-0">
              <div role="tablist" aria-label="Work" className="flex items-center gap-0.5">
                {WORK_TABS.map((tab) => {
                  const active = workTab === tab.id;
                  return (
                    <button
                      key={tab.id}
                      role="tab"
                      aria-selected={active}
                      onClick={() => onWorkTabChange(tab.id)}
                      className={cn(
                        "flex shrink-0 items-center gap-1.5 px-2.5 h-7 rounded-md text-[12px] transition-colors",
                        active
                          ? "text-[var(--text-primary)] bg-[var(--bg-tertiary)] font-medium"
                          : "text-[var(--text-tertiary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)]",
                      )}
                    >
                      {tab.icon}
                      {tab.label}
                      {tab.id === "meeting" && meetingActive && (
                        <span aria-label="Meeting in progress" className="w-1.5 h-1.5 rounded-full bg-[var(--danger)]" />
                      )}
                    </button>
                  );
                })}
              </div>

              {workTab === "documents" && docState && (
                <>
                  <div className="w-px h-4 mx-1 bg-[var(--border)]" />
                  <span className="min-w-0 text-[13px] font-medium truncate text-[var(--text-primary)]">
                    {docState.fileName}
                  </span>
                  <button
                    onClick={() => { setDocState(null); setCurrentDocPath(null); }}
                    title={"Close document"}
                    aria-label={"Close document"}
                    className="p-1 rounded text-[var(--text-tertiary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
                  >
                    <X size={14} />
                  </button>
                </>
              )}
              {workTab === "meeting" && meetingActive && (
                <>
                  <div className="w-px h-4 mx-1 bg-[var(--border)]" />
                  <button
                    onClick={() => setMeetingActive(false)}
                    className="flex items-center gap-1 px-2 py-1 rounded text-[11px] text-[var(--text-tertiary)] hover:text-[var(--danger)] hover:bg-[var(--danger-bg)] transition-colors"
                  >
                    <X size={13} />
                    {"Close meeting"}
                  </button>
                </>
              )}

              <div className="flex-1 min-w-0" />
              <Button
                size="icon"
                variant="ghost"
                onClick={onNewSession}
                title={"Start new conversation"}
                aria-label={"Start new conversation"}
              >
                <Sparkles size={15} />
              </Button>
              {workTab === "documents" && (
                <>
                  <WorkspaceSwitcher current={workspace} onChange={setWorkspace} />
                  <Button
                    size="icon"
                    variant="ghost"
                    onClick={() => setShowWorkFiles((v) => !v)}
                    title={showWorkFiles ? "Hide file browser" : "Show file browser"}
                    className={cn(showWorkFiles ? "text-[var(--brand)] bg-[var(--brand-bg)]" : "")}
                  >
                    {showWorkFiles ? <PanelRightClose size={15} /> : <PanelRightOpen size={15} />}
                  </Button>
                </>
              )}
            </div>

            {/* Workspace content — horizontal: content + file list */}
            <div className="flex-1 min-h-0 flex">
              {/* Content area (left) */}
              <div className="flex-1 min-h-0 overflow-hidden">
                {workTab === "browser" && <BrowserPanel />}
                {workTab === "calendar" && <CalendarPage />}
                {/* Stays mounted while the meeting runs, so switching tabs does not end it. */}
                {meetingActive && activeSession && (
                  <div className={cn("h-full", workTab !== "meeting" && "hidden")}>
                    <MeetingPanel
                      sessionId={activeSession.session_id}
                      onPrompt={(text) => {
                        setMessages((prev) => [...prev, { id: `u-${Date.now()}`, role: "user", content: text }]);
                        setSending(true);
                        sessionsApi.sendPrompt(activeSession.session_id, text, selectedModel, undefined, effectiveAgent, activeEffort, selectedProvider ?? undefined, currentDocPath || undefined)
                          .catch(() => setSending(false));
                      }}
                    />
                  </div>
                )}
                {workTab === "meeting" && !(meetingActive && activeSession) && (
                  <div className="h-full flex items-center justify-center text-[var(--text-tertiary)]">
                    <div className="text-center">
                      <Mic size={32} className="mx-auto mb-3 opacity-30" />
                      {activeSession ? (
                        <Button size="sm" onClick={handleStartMeeting}>{"Start meeting"}</Button>
                      ) : (
                        <p className="text-sm">{"Start or open a conversation to hold a meeting."}</p>
                      )}
                    </div>
                  </div>
                )}
                {workTab === "documents" && (workspaceType === "code" && currentDocPath ? (
                  <CodePanel
                    key={currentDocPath}
                    initialPath={currentDocPath}
                    initialContent=""
                    workMode={true}
                  />
                ) : workspaceType === "prototype" && currentDocPath ? (
                  <PrototypePanel
                    key={currentDocPath}
                    filePath={currentDocPath}
                    onClose={() => { setDocState(null); setCurrentDocPath(null); }}
                  />
                ) : workspaceType === "markdown" && currentDocPath ? (
                  <MarkdownPanel
                    key={currentDocPath}
                    filePath={currentDocPath}
                    onClose={() => { setDocState(null); setCurrentDocPath(null); }}
                  />
                ) : docState ? (
                  <DocumentPanel
                    doc={docState}
                    onClose={() => { setDocState(null); setCurrentDocPath(null); }}
                    maximized={false}
                    onToggleMaximize={() => {}}
                    onDownload={() => {
                      if (docState?.filePath) {
                        const link = document.createElement("a");
                        link.href = `/api/documents/download?path=${encodeURIComponent(docState.filePath)}`;
                        link.download = docState.fileName;
                        link.click();
                      }
                    }}
                    onRefreshPreview={async () => {
                      if (!docState?.filePath) return;
                      setDocState((prev) => prev ? { ...prev, previewLoading: true } : prev);
                      try {
                        const preview = await documentsApi.getPreview(
                          docState.filePath,
                          docState.workspace || activeSession?.workspace || "",
                        );
                        setDocState((prev) => prev ? {
                          ...prev,
                          previewLoading: false,
                          previewHtml: preview.html,
                          previewError: null,
                        } : prev);
                      } catch (e: any) {
                        setDocState((prev) => prev ? {
                          ...prev,
                          previewLoading: false,
                          previewError: e?.message || "Failed to refresh preview",
                        } : prev);
                      }
                    }}
                    workMode={true}
                    pendingHighlight={pendingHighlight}
                    onAIEdit={handleAIEdit}
                  />
                ) : (
                  <div className="h-full flex items-center justify-center text-[var(--text-tertiary)]">
                    <div className="text-center">
                      <LayoutPanelTop size={32} className="mx-auto mb-3 opacity-30" />
                      <p className="text-sm">{"Nothing to work on. Ask AI to create or open a file."}</p>
                    </div>
                  </div>
                ))}
              </div>

              {/* File list on the Documents tab — right side */}
              {workTab === "documents" && (
                <FileBrowser
                  collapsed={!showWorkFiles}
                  onToggle={() => setShowWorkFiles((v) => !v)}
                  sessionId={activeSession?.session_id || null}
                  workspace={activeSession?.workspace || workspace || undefined}
                  onOpenDoc={(path, name) => {
                    setShowWorkFiles(false);
                    handleOpenDoc(path, name);
                  }}
                  refreshTrigger={fileTreeRefreshKey}
                />
              )}
            </div>
          </div>
        </>
      ) : (
        <>
        <div className="flex-1 flex flex-col min-w-0 min-h-0 overflow-hidden">
          {/* Toolbar */}
          <div className="flex items-center gap-1 px-2 sm:px-3 h-12 shrink-0 overflow-x-auto overscroll-x-contain bg-transparent">
            <Button
              size="icon"
              variant="ghost"
              onClick={() => setMobileSidebarOpen(true)}
              title={"Sessions"}
              className="md:hidden"
            >
              <Menu size={16} />
            </Button>
            <WorkspaceSwitcher current={workspace} onChange={setWorkspace} />
            <div className="flex-1 min-w-0">
              {activeSession && (
                <div className="flex items-center gap-2">
                  <BranchSelector
                    sessionId={activeSession.session_id}
                    activeBranch={activeBranch}
                    onSwitch={handleSwitchBranch}
                    onReplay={startReplay}
                  />
                  <Button
                    size="xs"
                    variant="ghost"
                    onClick={openCompression}
                    disabled={sending}
                    leftIcon={<Archive size={13} />}
                    title={"Compress conversation"}
                    className="text-[var(--text-secondary)] hover:text-[var(--brand)]"
                  >
                    {"Compress"}
                  </Button>
                </div>
              )}
            </div>
            <NotificationBell onSwitchSession={onSelectSessionById} onNotificationAction={handleNotificationAction} />
            {completedTasks.length > 0 && (
              <Button
                size="icon"
                variant="ghost"
                onClick={() => setShowResultCompare(true)}
                title={"Task Board"}
                className="text-[var(--accent-2)] hover:text-[var(--accent-2)] hover:bg-[var(--accent-2-bg)]"
              >
                <Bot size={15} />
              </Button>
            )}
            <Button
              size="icon"
              variant="ghost"
              onClick={() => setShowFiles((v) => !v)}
              title={showFiles ? "Hide file browser" : "Show file browser"}
              className={cn(showFiles ? "text-[var(--brand)] bg-[var(--brand-bg)]" : "")}
            >
              {showFiles ? <PanelRightClose size={15} /> : <PanelRightOpen size={15} />}
            </Button>
          </div>

        {activeSession ? (
          <>
            {replaying && replayProgress.total > 0 && (
              <div className="px-4 pt-2 pb-1 border-b border-[var(--border)] bg-[var(--bg-secondary)]">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs text-[var(--success)] flex items-center gap-1.5">
                    <Sparkles size={11} className="animate-pulse" />
                    {"Replaying branch"}
                  </span>
                  <span className="text-xs text-[var(--text-tertiary)] font-mono">
                    {replayProgress.current} / {replayProgress.total}
                  </span>
                </div>
                <div className="h-1 rounded-full overflow-hidden bg-[var(--bg-tertiary)]">
                  <div
                    className="h-full transition-all duration-200 rounded-full bg-gradient-to-r from-[var(--success)] to-[var(--accent)]"
                    style={{
                      width: `${(replayProgress.current / replayProgress.total) * 100}%`,
                    }}
                  />
                </div>
              </div>
            )}

            {goal && <GoalCard goal={goal} busy={goalSaving || sending} onPause={() => setGoalStatus("paused")} onResume={() => setGoalStatus("active")} onEdit={() => setGoalComposerOpen(true)} onHistory={() => setGoalTimelineOpen(true)} />}
            <ChatPanel
              sessionId={activeSession.session_id}
              onVisibleImage={requestMessageImages}
              messages={messages}
              connected={connected}
              sending={sending}
              onToolConfirm={handleToolConfirm}
              onUserInput={handleUserInput}
              onBranch={handleBranch}
              replaying={replaying}
              externalSubAgentId={viewingSubAgent}
              onSubAgentModalClose={() => setViewingSubAgent(null)}
              getSubAgentContent={getSubAgentContent}
            />

            <McpStatusBar status={mcpStatus} />

            <div
              className="shrink-0 px-3 sm:px-6 pt-1"
              style={{ paddingBottom: "max(0.75rem, env(safe-area-inset-bottom))" }}
              onDrop={handleDrop}
              onDragOver={handleDragOver}
            >
              <div className="mx-auto w-full max-w-3xl">
              <SessionStatsBar
                sessionId={activeSession?.session_id ?? null}
                refreshKey={messages.length * 100 + (sending ? 1 : 0) + statsTick}
              />

              <ChatInput
                sending={sending}
                replaying={replaying}
                onSend={handleSend}
                onAbort={handleAbort}
                onFileUpload={handleFileUpload}
                onFilePaste={handleFilePaste}
                onDelegateOpen={() => setShowDelegate(true)}
                showDelegate={true}
                onGoalOpen={() => setGoalComposerOpen(true)}
                hasGoal={!!goal}
                toolbar={renderComposerControls(mainAgentRef, mainEffortRef)}
                attachments={composerAttachments}
                canSendEmpty={hasAttachments}
              />
              </div>
            </div>
          </>
        ) : (
          /* Start screen: greeting and a composer; the first message creates the session */
          <div className="flex-1 min-h-0 overflow-y-auto px-3 sm:px-6">
            <div className="mx-auto flex min-h-full w-full max-w-2xl flex-col justify-center pt-6 pb-[12vh] animate-fade-in">
              <h1 className="font-display mb-7 sm:mb-9 flex items-center justify-center gap-3 text-center text-[1.9rem] sm:text-[2.5rem] leading-tight text-[var(--text-primary)]">
                <span aria-hidden="true" className="text-[0.85em]">🦀</span>
                <span>{greeting}</span>
              </h1>
              <div onDrop={handleDrop} onDragOver={handleDragOver}>
                <ChatInput
                  sending={false}
                  replaying={starting}
                  onSend={startConversation}
                  onAbort={handleAbort}
                  onFileUpload={handleFileUpload}
                  onFilePaste={handleFilePaste}
                  onDelegateOpen={() => setShowDelegate(true)}
                  toolbar={renderComposerControls(mainAgentRef, mainEffortRef)}
                  attachments={composerAttachments}
                  canSendEmpty={hasAttachments}
                  placeholder={"How can I help you today?"}
                  autoFocus
                  seed={starterSeed}
                />
              </div>
              <div className="mt-4 flex flex-wrap justify-center gap-2">
                {STARTER_PROMPTS.map((p) => (
                  <button
                    key={p.label}
                    type="button"
                    onClick={() => setStarterSeed({ text: p.prompt, key: Date.now() })}
                    className={cn(
                      "flex items-center gap-1.5 h-8 px-3 rounded-lg text-[13px]",
                      "border border-[var(--border)] bg-transparent text-[var(--text-secondary)]",
                      "hover:bg-[var(--bg-secondary)] hover:text-[var(--text-primary)] hover:border-[var(--border-strong)]",
                      "transition-colors",
                    )}
                  >
                    <span className="text-[var(--text-tertiary)]">{p.icon}</span>
                    {p.label}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}
        </div>

        <FileBrowser
        collapsed={!showFiles}
        onToggle={() => setShowFiles((v) => !v)}
        sessionId={activeSession?.session_id || null}
        workspace={activeSession?.workspace || workspace || undefined}
        onOpenDoc={handleOpenDoc}
        refreshTrigger={fileTreeRefreshKey}
      />

      {/* Document Panel — right sidebar when AI is working on a doc */}
      {docState && (
        <div
          className={cn(
            "flex flex-col bg-[var(--bg-primary)] shrink-0 transition-[width] duration-150 max-md:!absolute max-md:inset-0 max-md:z-30 max-md:!w-full max-md:!max-w-none",
            docPanelMaximized
              ? "absolute inset-y-0 right-0 z-30 border-l-0 shadow-[-8px_0_24px_rgba(0,0,0,0.35)]"
              : "relative border-l border-[var(--border)]",
          )}
          style={
            docPanelMaximized
              ? { width: `calc(100% - ${SESSION_LIST_W}px)` }
              : { width: docPanelWidth, maxWidth: `calc(100% - ${SESSION_LIST_W}px - ${CHAT_MIN_W}px)` }
          }
        >
          {/* Drag handle for resizing — only in sidebar mode */}
          {!docPanelMaximized && (
            <div
              className="absolute left-0 inset-y-0 w-3 z-10 cursor-col-resize select-none
                         hover:w-4 transition-all group"
              onMouseDown={handleDocPanelResizeStart}
            >
              {/* Visible drag rail */}
              <div className="absolute inset-y-0 left-0 w-[3px]
                              bg-[var(--border-strong)] group-hover:bg-[var(--accent)]
                              group-active:bg-[var(--accent)] transition-colors" />
              {/* Grip dots — appear on hover */}
              <div className="flex flex-col items-center justify-center h-full gap-[3px] pl-[2px]
                              opacity-0 group-hover:opacity-100 transition-opacity">
                <div className="w-[4px] h-[4px] rounded-full bg-[var(--accent)]" />
                <div className="w-[4px] h-[4px] rounded-full bg-[var(--accent)]" />
                <div className="w-[4px] h-[4px] rounded-full bg-[var(--accent)]" />
              </div>
            </div>
          )}
          <DocumentPanel
            doc={docState}
            onClose={() => { setDocState(null); setCurrentDocPath(null); }}
            maximized={docPanelMaximized}
            onToggleMaximize={() => setDocPanelMaximized((v) => !v)}
            onDownload={() => {
              // Trigger download via backend
              if (docState?.filePath) {
                const link = document.createElement("a");
                link.href = `/api/documents/download?path=${encodeURIComponent(docState.filePath)}`;
                link.download = docState.fileName;
                link.click();
              }
            }}
            onRefreshPreview={async () => {
              if (!docState?.filePath) return;
              setDocState((prev) => prev ? { ...prev, previewLoading: true } : prev);
              try {
                const preview = await documentsApi.getPreview(
                  docState.filePath,
                  docState.workspace || activeSession?.workspace || "",
                );
                setDocState((prev) => prev ? {
                  ...prev,
                  previewLoading: false,
                  previewHtml: preview.html,
                  previewError: null,
                } : prev);
              } catch (e: any) {
                setDocState((prev) => prev ? {
                  ...prev,
                  previewLoading: false,
                  previewError: e?.message || "Failed to refresh preview",
                } : prev);
              }
            }}
          />
        </div>
      )}
        </>
      )}

      {/* The live sub-agent board belongs to the Work page. */}
      {mode === "work" && (
        <AgentRunBoard
          tasks={agentRunBoardRuns}
          onTaskClick={(t) => setViewingSubAgent(t.subId)}
        />
      )}

      <TodoWidget
        sessionId={activeSession?.session_id || null}
        refreshKey={todoRefreshKey}
      />

      {/* No provider overlay */}
      {!providersLoading && providers.length === 0 && (
        <Modal
          open={true}
          onOpenChange={() => {}}
          title={"Welcome to CrabAgent"}
          description={"Add Provider"}
          size="sm"
          hideClose
          disableBackdropClose
          footer={
            <Button variant="brand" onClick={() => setShowProviders(true)}>
              Add Provider
            </Button>
          }
        >
          <div className="text-center py-2">
            <div className="text-4xl mb-3">🔑</div>
            <p className="text-sm text-[var(--text-secondary)] leading-relaxed">
              CrabAgent needs at least one LLM provider to function. Add your API
              key to start chatting.
            </p>
          </div>
        </Modal>
      )}

      {showProviders && (
        <ProviderPanel
          providers={providers}
          catalog={catalog}
          onClose={() => setShowProviders(false)}
          onRefresh={() => {
            refreshProviders();
          }}
        />
      )}

      {showMcpServers && (
        <McpServerPanel
          servers={mcpServers}
          status={mcpStatus}
          onClose={() => setShowMcpServers(false)}
          onRefresh={() => {
            mcpServersApi.listMcpServers().then(setMcpServers);
            mcpServersApi.getMcpStatus().then(setMcpStatus);
          }}
        />
      )}

      {showScheduledTasks && (
        <ScheduledTaskPanel
          onClose={() => setShowScheduledTasks(false)}
          onSwitchSession={onSelectSessionById}
        />
      )}

      {showTasks && (
        <TaskPanel
          onClose={() => setShowTasks(false)}
          onSwitchSession={onSelectSessionById}
        />
      )}

      {showEmail && (
        <EmailPanel
          onClose={() => setShowEmail(false)}
        />
      )}

      {showSkills && <SkillsPanel onClose={() => setShowSkills(false)} />}

      {activeSession && <GoalComposer open={goalComposerOpen} goal={goal} onOpenChange={setGoalComposerOpen} onSubmit={saveGoal} />}
      {activeSession && <GoalTimeline open={goalTimelineOpen} sessionId={activeSession.session_id} goal={goal} onOpenChange={setGoalTimelineOpen} />}

      {showDelegate && activeSession && (
        <DelegateModal
          onClose={() => setShowDelegate(false)}
          onDelegate={handleDelegateFromModal}
        />
      )}

      {showResultCompare && agentRunBoardRuns.length > 0 && (
        <ResultCompare
          tasks={agentRunBoardRuns}
          onClose={() => setShowResultCompare(false)}
          onExport={handleExportReport}
        />
      )}

      {showCompression && (
        <Modal
          open={true}
          onOpenChange={(open) => !open && !compressing && setShowCompression(false)}
          title={"Compress conversation"}
          description={"Create a summary with the selected model and replace the active conversation context. Original messages are kept."}
          size="sm"
          footer={
            <>
              <Button variant="ghost" onClick={() => setShowCompression(false)} disabled={compressing}>{"Cancel"}</Button>
              <Button variant="brand" onClick={runCompression} loading={compressing} disabled={!compressionModel || !compressionProvider}>
                {"Compress now"}
              </Button>
            </>
          }
        >
          <div className="space-y-4">
            <label className="block text-xs text-[var(--text-secondary)]">
              {"Compression model"}
              <select
                value={`${compressionProvider}::${compressionModel}`}
                onChange={(event) => {
                  const [provider, model] = event.target.value.split("::");
                  setCompressionProvider(provider);
                  setCompressionModel(model);
                }}
                className="mt-1.5 w-full h-9 rounded-lg border border-[var(--border)] bg-[var(--bg-tertiary)] px-2 text-sm text-[var(--text-primary)]"
              >
                <option value="">{"Select a model"}</option>
                {providerModels.flatMap((item) => item.models.map((model) => (
                  <option key={`${item.provider.name}-${model.id}`} value={`${item.provider.name}::${model.id}`}>
                    {item.provider.display_name || item.provider.name} / {model.id}
                  </option>
                )))}
              </select>
            </label>
            <p className="text-xs leading-relaxed text-[var(--text-tertiary)]">{"Uses the same prompt, message cleanup, and retry behavior as automatic compression. The message list refreshes when it finishes."}</p>
            {compressionError && <p className="text-xs text-[var(--danger)]">{compressionError}</p>}
          </div>
        </Modal>
      )}

    </div>
  );
}
