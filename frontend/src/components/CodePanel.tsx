import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Code, FileWarning, RotateCw, Save } from "lucide-react";
import { cn } from "../lib/cn";
import { CodeTabs } from "./CodeTabs";
import * as filesApi from "../api/files";
import type { FileContextFields } from "../api/files";
import { ApiError } from "../api/client";
import { Modal } from "./ui";
import { DiscardChangesDialog } from "./DiscardChangesDialog";
import { createCodeEditor, type CodeEditorHandle } from "./code-editor";
import { diffLines } from "./code-editor/diff";

type TabStatus = "loading" | "ready" | "error";
/** Encoding that round-trips the file's bytes; echoed back on save. */
type FileEncoding = NonNullable<filesApi.FileContent["encoding"]>;

interface CodeTabState {
  path: string;
  name: string;
  content: string;
  originalContent: string;
  modified: boolean;
  language: string;
  status: TabStatus;
  errorMessage: string | null;
  binary: boolean;
  truncated: boolean;
  /** Encoding the read reported; the save must write the same bytes back. */
  encoding?: FileEncoding;
  /**
   * mtime_ms of the disk copy this buffer was read from (or last saved as).
   * Sent as expected_mtime_ms on save so the server rejects a stale write with
   * 409 instead of silently clobbering another writer.
   */
  mtimeMs?: number;
}

/**
 * Snapshot a parent can read: the open file and its dirty/read-only state.
 * Used by ChatPage to drive external-change sync (T6c).
 */
export interface CodePanelState {
  openPath: string | null;
  /** Unsaved edits in the active tab (what the prompt context reports). */
  dirty: boolean;
  /** Unsaved edits in any open tab: true when unmounting the panel would lose work. */
  anyDirty: boolean;
  readOnly: boolean;
  binary: boolean;
  truncated: boolean;
  language: string;
  status: TabStatus | "empty";
}

/**
 * A `file_updated` event routed into the panel. `seq` makes a repeated event
 * for the same path distinguishable, so a re-render never re-applies an old one.
 */
export interface ExternalFileChange {
  /** Workspace-relative path from the event payload. */
  path: string;
  /** Absolute path from the event payload; matches a tab opened with an absolute path. */
  absPath?: string;
  tool?: string;
  origin?: string;
  mtimeMs?: number;
  seq: number;
}

interface Props {
  initialPath?: string;
  initialContent?: string;
  className?: string;
  /**
   * False while the parent keeps this panel mounted but hidden behind another
   * document (`display:none`). Flipping back to true re-measures the editor:
   * CodeMirror measured a zero-size box while it was hidden.
   */
  visible?: boolean;
  workMode?: boolean;
  /** Chat session whose workspace the files belong to. */
  sessionId?: string;
  /** Force the panel read-only; the save shortcut is suppressed. */
  readOnly?: boolean;
  /** Shown when the panel is read-only for a caller-supplied reason. */
  readOnlyReason?: string;
  /** Seam for a parent that wants to sync external changes into the editor. */
  onEditorReady?: (handle: CodeEditorHandle | null) => void;
  /** Open path + dirty flag, kept in a shape a parent can read. */
  onStateChange?: (state: CodePanelState) => void;
  /** Latest `file_updated` event for this session; matched against the open tab. */
  externalChange?: ExternalFileChange | null;
}

/**
 * The open editor file as prompt fields: path always, buffer text only while
 * the buffer holds unsaved changes (otherwise the agent reads the disk copy).
 */
export function promptFileContext(
  state: CodePanelState | null,
  buffer: string,
): FileContextFields {
  if (!state?.openPath) return {};
  return filesApi.fileContextFields(state.openPath, state.dirty, buffer);
}

/** A tab path may be workspace-relative or absolute; the event carries both. */
function matchesPath(tabPath: string, change: ExternalFileChange): boolean {
  if (tabPath === change.path || tabPath === change.absPath) return true;
  const strip = (p: string) => p.replace(/^\.\//, "");
  return strip(tabPath) === strip(change.path);
}

/** Reads/writes for an absolute tab path must say so; relative paths stay relative. */
function isAbsolutePath(path: string): boolean {
  return path.startsWith("/");
}

function detectLanguage(name: string): string {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  const langMap: Record<string, string> = {
    py: "python", ts: "typescript", tsx: "typescriptreact",
    js: "javascript", jsx: "javascriptreact", json: "json",
    go: "go", rs: "rust", java: "java", c: "c", cpp: "cpp",
    css: "css", scss: "scss", html: "html", vue: "vue",
    svelte: "svelte", md: "markdown", yaml: "yaml", yml: "yaml",
    toml: "toml", sql: "sql", sh: "bash", bash: "bash",
    dockerfile: "dockerfile", txt: "text",
  };
  return langMap[ext] || "text";
}

function baseName(path: string): string {
  return path.split("/").pop() || path;
}

function newTab(path: string, content: string, hasContent: boolean): CodeTabState {
  return {
    path,
    name: baseName(path),
    content,
    originalContent: content,
    modified: false,
    language: detectLanguage(path),
    status: hasContent ? "ready" : "loading",
    errorMessage: null,
    binary: false,
    truncated: false,
  };
}

export function CodePanel({
  initialPath,
  initialContent,
  className,
  visible = true,
  sessionId,
  readOnly,
  readOnlyReason,
  onEditorReady,
  onStateChange,
  externalChange,
}: Props) {
  const [tabs, setTabs] = useState<CodeTabState[]>(() => {
    if (!initialPath) return [];
    const provided = typeof initialContent === "string" && initialContent.length > 0;
    return [newTab(initialPath, initialContent ?? "", provided)];
  });
  const [activePath, setActivePath] = useState<string | null>(initialPath || null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editorFailure, setEditorFailure] = useState(false);
  const [mountNonce, setMountNonce] = useState(0);
  // A dirty buffer whose file changed on disk: never overwritten, only offered a reload.
  const [conflict, setConflict] = useState<{
    path: string;
    tool: string;
    diskContent: string;
    /** Encoding the disk copy was decoded with, adopted when the user reloads. */
    encoding?: FileEncoding;
    /** mtime_ms of the disk copy, adopted by the tab when the user reloads. */
    mtimeMs?: number;
  } | null>(null);
  const [diffOpen, setDiffOpen] = useState(false);
  // A dirty tab the user asked to close; the buffer is only dropped on confirm.
  const [pendingClose, setPendingClose] = useState<string | null>(null);

  const hostRef = useRef<HTMLDivElement>(null);
  const handleRef = useRef<CodeEditorHandle | null>(null);
  const activePathRef = useRef<string | null>(activePath);
  const tabsRef = useRef<CodeTabState[]>(tabs);
  const readOnlyPropRef = useRef(readOnly === true);
  const sessionIdRef = useRef(sessionId);
  const savingRef = useRef(false);
  const inFlight = useRef<Set<string>>(new Set());
  const loadIds = useRef<Record<string, number>>({});
  // The save this panel just made, so its own `file_updated` echo is ignored.
  const selfSaveRef = useRef<{ path: string; mtimeMs?: number } | null>(null);
  const lastChangeSeqRef = useRef(0);
  // Held in refs so an inline callback from the parent never remounts the editor.
  const onEditorReadyRef = useRef(onEditorReady);
  const onStateChangeRef = useRef(onStateChange);

  activePathRef.current = activePath;
  tabsRef.current = tabs;
  readOnlyPropRef.current = readOnly === true;
  sessionIdRef.current = sessionId;
  onEditorReadyRef.current = onEditorReady;
  onStateChangeRef.current = onStateChange;

  const activeTab = tabs.find((t) => t.path === activePath);
  const editorReadOnly = readOnly === true || activeTab?.truncated === true;

  // ── Load file content ─────────────────────────────────────────
  const loadTab = useCallback(async (path: string) => {
    const seq = (loadIds.current[path] || 0) + 1;
    loadIds.current[path] = seq;
    setTabs((prev) => prev.map((t) => (
      t.path === path && t.status !== "loading" ? { ...t, status: "loading", errorMessage: null } : t
    )));
    try {
      const res = (await filesApi.readFile(path, isAbsolutePath(path), { sessionId: sessionIdRef.current })) as filesApi.FileContent & { binary?: boolean };
      if (loadIds.current[path] !== seq) return;
      // The backend reports binary files with binary: true; treat a missing
      // field as "not binary" so this works before and after that lands.
      const binary = res?.binary === true;
      const content = binary ? "" : (res?.content ?? "");
      setTabs((prev) => prev.map((t) => (t.path === path ? {
        ...t,
        content,
        originalContent: content,
        modified: false,
        status: "ready",
        binary,
        truncated: res?.truncated === true,
        // Keep the decoding the read used; re-encoding a latin-1 file as UTF-8
        // would change every byte >= 0x80.
        encoding: res?.encoding,
        // The version this buffer is based on; the next save sends it back.
        mtimeMs: res?.mtime_ms,
        errorMessage: null,
      } : t)));
    } catch (e: unknown) {
      if (loadIds.current[path] !== seq) return;
      const message = e instanceof Error ? e.message : "Could not load this file";
      setTabs((prev) => prev.map((t) => (t.path === path ? { ...t, status: "error", errorMessage: message } : t)));
    }
  }, []);

  useEffect(() => {
    for (const tab of tabs) {
      if (tab.status !== "loading" || inFlight.current.has(tab.path)) continue;
      inFlight.current.add(tab.path);
      void loadTab(tab.path).finally(() => inFlight.current.delete(tab.path));
    }
  }, [tabs, loadTab]);

  // The parent keeps this panel mounted while the user picks other files in the
  // tree; a new path becomes a tab, and reopening an open one just focuses it.
  useEffect(() => {
    if (!initialPath) return;
    setTabs((prev) => (
      prev.some((t) => t.path === initialPath)
        ? prev
        : [...prev, newTab(initialPath, initialContent ?? "", typeof initialContent === "string" && initialContent.length > 0)]
    ));
    setActivePath(initialPath);
  }, [initialPath, initialContent]);

  // ── Tab handling ──────────────────────────────────────────────
  const discardTab = useCallback((path: string) => {
    setTabs((prev) => {
      const idx = prev.findIndex((t) => t.path === path);
      const next = prev.filter((t) => t.path !== path);
      if (activePathRef.current === path) {
        const newIdx = Math.min(idx, next.length - 1);
        setActivePath(next[newIdx]?.path || null);
      }
      return next;
    });
    // The banner belongs to a path: dropping that tab drops its conflict too.
    setConflict((current) => (current && current.path === path ? null : current));
  }, []);

  /** A dirty tab asks before its buffer is thrown away; a clean one just closes. */
  const closeTab = useCallback((path: string) => {
    const tab = tabsRef.current.find((t) => t.path === path);
    if (tab?.modified) {
      setPendingClose(path);
      return;
    }
    discardTab(path);
  }, [discardTab]);

  const handleEditorChange = useCallback((path: string, content: string) => {
    setTabs((prev) => prev.map((t) => (
      t.path === path ? { ...t, content, modified: content !== t.originalContent } : t
    )));
  }, []);

  // ── Save ──────────────────────────────────────────────────────
  /**
   * A save the server rejected as stale (HTTP 409): read the disk copy and
   * offer the same Reload / Show diff banner the external-change path shows.
   * The buffer and the caret stay untouched, and the tab keeps its old mtime so
   * a plain retry conflicts again instead of overwriting the other writer.
   */
  const showStaleConflict = useCallback(async (path: string) => {
    try {
      const res = await filesApi.readFile(path, isAbsolutePath(path), { sessionId: sessionIdRef.current });
      // The tab may have been closed or reverted while the read was in flight;
      // a banner with nothing left to resolve must not appear.
      const tab = tabsRef.current.find((t) => t.path === path);
      if (!tab || !tab.modified) return;
      if (res.binary || res.truncated) {
        setError("The file changed on disk. Reload it before saving.");
        return;
      }
      setConflict({
        path,
        tool: "another process",
        diskContent: res.content ?? "",
        encoding: res.encoding,
        mtimeMs: res.mtime_ms,
      });
      setError(null);
    } catch {
      setError("The file changed on disk. Reload it before saving.");
    }
  }, []);

  const handleSave = useCallback(async () => {
    const path = activePathRef.current;
    const tab = tabsRef.current.find((t) => t.path === path);
    if (!tab || tab.status !== "ready" || tab.binary) return;
    if (tab.truncated || readOnlyPropRef.current) return; // read-only: nothing may be written
    if (savingRef.current) return;
    const content = handleRef.current?.getContent() ?? tab.content;
    if (content === tab.originalContent) return;
    savingRef.current = true;
    setSaving(true);
    try {
      const options: filesApi.SaveFileOptions = {
        sessionId: sessionIdRef.current,
        // The disk version this buffer was read from: the server answers 409
        // when another writer replaced it, instead of silently overwriting.
        expectedMtimeMs: tab.mtimeMs,
      };
      // Write the bytes back in the encoding the file was read with; the server
      // defaults to UTF-8, which would corrupt every byte >= 0x80 of latin-1.
      if (tab.encoding) options.encoding = tab.encoding;
      const result = await filesApi.saveFile(tab.path, content, isAbsolutePath(tab.path), options);
      selfSaveRef.current = { path: tab.path, mtimeMs: result?.mtime_ms };
      // Typing may have continued while the request was in flight. Only `content`
      // reached the disk, so compare against the live buffer (`t` is the current
      // tab state, not the pre-await closure): the newer text stays dirty.
      setTabs((prev) => prev.map((t) => (
        t.path === tab.path ? {
          ...t,
          originalContent: content,
          modified: t.content !== content,
          // The written file's mtime is what the next save must expect.
          mtimeMs: result?.mtime_ms ?? tab.mtimeMs,
        } : t
      )));
      setConflict((current) => (current && current.path === tab.path ? null : current));
      setError(null);
    } catch (e: unknown) {
      if (e instanceof ApiError && e.status === 409) {
        await showStaleConflict(tab.path);
      } else {
        setError(e instanceof Error ? e.message : "Save failed");
      }
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  }, [showStaleConflict]);

  // Keyboard shortcuts: Ctrl/Cmd+S saves and never opens the browser save dialog.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void handleSave();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [handleSave]);

  // ── External changes (agent tool, another client) ─────────────
  /** Discard the buffer and adopt the disk copy, keeping the caret on its text. */
  const applyDiskContent = useCallback((path: string, diskContent: string, mtimeMs?: number, encoding?: FileEncoding) => {
    if (activePathRef.current === path) {
      handleRef.current?.replaceContent(diskContent, { preserveSelection: true });
    }
    setTabs((prev) => prev.map((t) => (
      t.path === path
        ? {
          ...t,
          content: diskContent,
          originalContent: diskContent,
          modified: false,
          status: "ready",
          encoding: encoding ?? t.encoding,
          mtimeMs: mtimeMs ?? t.mtimeMs,
        }
        : t
    )));
    setConflict(null);
    setDiffOpen(false);
  }, []);

  useEffect(() => {
    if (!externalChange || externalChange.seq === lastChangeSeqRef.current) return;
    lastChangeSeqRef.current = externalChange.seq;
    const tab = tabsRef.current.find((t) => matchesPath(t.path, externalChange));
    // A path no tab has open must not touch the editor at all.
    if (!tab || tab.status !== "ready" || tab.binary || tab.truncated) return;
    // Our own save echo: the buffer already holds what was written.
    if (
      externalChange.origin === "ui"
      && selfSaveRef.current?.path === tab.path
      && (externalChange.mtimeMs === undefined || externalChange.mtimeMs === selfSaveRef.current.mtimeMs)
    ) {
      return;
    }

    const seq = externalChange.seq;
    const tool = externalChange.tool || "another process";
    const path = tab.path;
    void (async () => {
      let next: string;
      let diskMtimeMs: number | undefined;
      let diskEncoding: FileEncoding | undefined;
      try {
        const res = await filesApi.readFile(path, isAbsolutePath(path), {
          sessionId: sessionIdRef.current,
        });
        // A non-text payload has no editable content to sync.
        if (res.binary || res.truncated) return;
        next = res.content ?? "";
        diskMtimeMs = res.mtime_ms;
        diskEncoding = res.encoding;
      } catch {
        return; // A failed re-read leaves the buffer exactly as it was.
      }
      if (lastChangeSeqRef.current !== seq) return; // a newer event superseded this one
      // The tab may have been closed, or its buffer may have been typed into
      // while this re-read was in flight: decide on the live tab, never on the
      // pre-await copy.
      const current = tabsRef.current.find((t) => t.path === path);
      if (!current || current.status !== "ready" || current.binary || current.truncated) return;
      const buffer = activePathRef.current === path && handleRef.current
        ? handleRef.current.getContent()
        : current.content;
      // Identical content: no transaction, so no cursor jump and no echo loop.
      if (next === buffer) {
        // Even an identical rewrite moved the mtime: remember it, or the next
        // save would be rejected as stale for no reason.
        setTabs((prev) => prev.map((t) => (
          t.path === path
            ? { ...t, ...(t.modified ? { content: next, originalContent: next, modified: false, encoding: diskEncoding ?? t.encoding } : {}), mtimeMs: diskMtimeMs ?? t.mtimeMs }
            : t
        )));
        setConflict((cur) => (cur && cur.path === path ? null : cur));
        return;
      }
      if (current.modified) {
        // Never overwrite unsaved work — offer reload or a diff instead.
        setConflict({ path, tool, diskContent: next, encoding: diskEncoding, mtimeMs: diskMtimeMs });
        return;
      }
      if (activePathRef.current === path) {
        handleRef.current?.replaceContent(next, { preserveSelection: true });
      }
      setTabs((prev) => prev.map((t) => (
        t.path === path
          ? { ...t, content: next, originalContent: next, modified: false, status: "ready", encoding: diskEncoding ?? t.encoding, mtimeMs: diskMtimeMs }
          : t
      )));
    })();
  }, [externalChange]);

  // ── Session switch ────────────────────────────────────────────
  // Another chat means another workspace: the previous chat's documents must not
  // survive the switch, or a save would create them inside the new workspace.
  // The parent clears the open path too (it follows the reported openPath).
  const tabsSessionRef = useRef(sessionId);
  useEffect(() => {
    if (tabsSessionRef.current === sessionId) return;
    tabsSessionRef.current = sessionId;
    setTabs([]);
    setActivePath(null);
    setConflict(null);
    setPendingClose(null);
    setDiffOpen(false);
  }, [sessionId]);

  // ── Editor lifecycle ──────────────────────────────────────────
  const activeStatus = activeTab?.status ?? null;
  const activeBinary = activeTab?.binary ?? false;

  useEffect(() => {
    const host = hostRef.current;
    const tab = tabsRef.current.find((t) => t.path === activePath);
    if (!host || !tab || tab.status !== "ready" || tab.binary) return;
    let disposed = false;
    setEditorFailure(false);
    createCodeEditor(host, {
      doc: tab.content,
      language: tab.language,
      readOnly: tab.truncated || readOnlyPropRef.current,
      onChange: (content) => handleEditorChange(tab.path, content),
    }).then((handle) => {
      if (disposed) {
        handle.destroy();
        return;
      }
      handleRef.current = handle;
      onEditorReadyRef.current?.(handle);
    }).catch(() => {
      if (!disposed) setEditorFailure(true);
    });
    return () => {
      disposed = true;
      if (handleRef.current) {
        handleRef.current.destroy();
        handleRef.current = null;
      }
      onEditorReadyRef.current?.(null);
    };
    // The document is only read when the editor is (re)created; edits must not remount it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activePath, activeStatus, activeBinary, editorReadOnly, mountNonce, handleEditorChange]);

  // ── Hidden -> visible ─────────────────────────────────────────
  // The parent keeps this panel mounted and hides it (`display:none`) while a
  // document is shown. CodeMirror measured an empty box back then, so it has to
  // re-measure once it is displayed again — without recreating the view, which
  // would drop the caret, undo history and the buffer.
  const visibleRef = useRef(visible);
  useEffect(() => {
    if (visibleRef.current === visible) return;
    visibleRef.current = visible;
    if (!visible) return;
    handleRef.current?.view?.requestMeasure();
  }, [visible]);

  // ── Parent-facing state ───────────────────────────────────────
  // `anyDirty` covers tabs the user is not looking at: the parent unmounts this
  // panel when it switches to another one, and that drops every open tab.
  const anyDirty = tabs.some((t) => t.modified);
  useEffect(() => {
    onStateChangeRef.current?.({
      openPath: activeTab?.path ?? null,
      dirty: activeTab?.modified === true,
      anyDirty,
      readOnly: editorReadOnly,
      binary: activeTab?.binary === true,
      truncated: activeTab?.truncated === true,
      language: activeTab?.language ?? "text",
      status: activeTab?.status ?? "empty",
    });
  }, [
    activeTab?.path,
    activeTab?.modified,
    anyDirty,
    activeTab?.binary,
    activeTab?.truncated,
    activeTab?.language,
    activeTab?.status,
    editorReadOnly,
  ]);

  // Built only while the diff modal is open, so an idle panel pays nothing.
  const conflictTab = conflict ? tabs.find((t) => t.path === conflict.path) : undefined;
  const diffView = conflict && diffOpen
    ? diffLines(
        (activePath === conflict.path ? handleRef.current?.getContent() : undefined)
          ?? conflictTab?.content
          ?? "",
        conflict.diskContent,
      )
    : null;

  return (
    <div className={cn("flex flex-col h-full bg-[var(--bg-primary)]", className)}>
      {/* Tab bar */}
      <CodeTabs
        tabs={tabs.map((t) => ({ path: t.path, name: t.name, modified: t.modified }))}
        activePath={activePath}
        onSelect={(path) => setActivePath(path)}
        onClose={closeTab}
      />

      {/* Editor / states */}
      {!activeTab ? (
        <div className="flex-1 flex items-center justify-center text-[var(--text-tertiary)]">
          <div className="text-center">
            <Code size={28} className="mx-auto mb-2 opacity-30" />
            <p className="text-sm">{"Select a file from the file browser"}</p>
          </div>
        </div>
      ) : activeTab.status === "loading" ? (
        <div data-testid="code-loading" className="flex-1 flex items-center justify-center text-xs text-[var(--text-tertiary)]">
          Loading file…
        </div>
      ) : activeTab.status === "error" ? (
        <div data-testid="code-load-error" className="flex-1 flex flex-col items-center justify-center gap-2 px-6 text-center">
          <AlertTriangle size={26} className="text-[var(--danger)]" />
          <p className="text-sm text-[var(--text-secondary)]">Could not load {activeTab.name}</p>
          <p className="text-[11px] text-[var(--text-tertiary)]">{activeTab.errorMessage}</p>
          <button
            onClick={() => void loadTab(activeTab.path)}
            className="flex items-center gap-1 px-2.5 py-1 rounded text-[11px] border border-[var(--border)] text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] transition-colors"
          >
            <RotateCw size={11} /> Retry
          </button>
        </div>
      ) : activeTab.binary ? (
        <div data-testid="code-binary" className="flex-1 flex flex-col items-center justify-center gap-2 px-6 text-center">
          <FileWarning size={28} className="opacity-30" />
          <p className="text-sm text-[var(--text-secondary)]">This file cannot be edited as text</p>
          <p className="text-[11px] text-[var(--text-tertiary)]">
            Binary files are read-only and are not shown in the code editor.
          </p>
        </div>
      ) : (
        <div className="flex-1 flex flex-col min-h-0">
          {conflict && conflict.path === activeTab.path && (
            <div
              data-testid="code-external-change"
              className="flex items-center gap-2 px-3 py-1.5 text-[11px] shrink-0 bg-[var(--warning-bg)] text-[var(--warning)] border-b border-[var(--warning-border)]"
            >
              <AlertTriangle size={12} />
              <span className="min-w-0 truncate">Changed on disk by {conflict.tool}</span>
              <span className="flex-1" />
              <button
                onClick={() => applyDiskContent(conflict.path, conflict.diskContent, conflict.mtimeMs, conflict.encoding)}
                className="px-2 py-0.5 rounded border border-[var(--warning-border)] hover:bg-[var(--bg-tertiary)] transition-colors"
              >
                Reload
              </button>
              <button
                onClick={() => setDiffOpen(true)}
                className="px-2 py-0.5 rounded border border-[var(--warning-border)] hover:bg-[var(--bg-tertiary)] transition-colors"
              >
                Show diff
              </button>
            </div>
          )}
          {activeTab.truncated && (
            <div
              data-testid="code-truncated-notice"
              className="flex items-center gap-1.5 px-3 py-1.5 text-[11px] shrink-0 bg-[var(--warning-bg)] text-[var(--warning)] border-b border-[var(--warning-border)]"
            >
              <AlertTriangle size={12} />
              This file is truncated because it is too large. It is read-only and cannot be saved from the UI.
            </div>
          )}
          {readOnly && !activeTab.truncated && (
            <div
              data-testid="code-readonly-notice"
              className="flex items-center gap-1.5 px-3 py-1.5 text-[11px] shrink-0 bg-[var(--warning-bg)] text-[var(--warning)] border-b border-[var(--warning-border)]"
            >
              <AlertTriangle size={12} />
              {readOnlyReason || "This file is read-only."}
            </div>
          )}
          {editorFailure && (
            <div className="flex items-center gap-2 px-3 py-1.5 text-[11px] shrink-0 bg-[var(--danger-bg)] text-[var(--danger)] border-b border-[var(--danger-border)]">
              <AlertTriangle size={12} />
              The code editor failed to load.
              <button
                onClick={() => { setEditorFailure(false); setMountNonce((n) => n + 1); }}
                className="underline"
              >
                Retry
              </button>
            </div>
          )}
          <div
            ref={hostRef}
            data-testid="code-editor-host"
            className="flex-1 min-h-0 overflow-hidden [&>.cm-editor]:h-full"
          />
        </div>
      )}

      {/* Status bar */}
      <div className="flex items-center justify-between px-3 py-1 border-t border-[var(--border)] bg-[var(--bg-secondary)] shrink-0">
        <div className="flex items-center gap-2">
          {activeTab && (
            <span className="text-[10px] font-mono text-[var(--text-tertiary)]">{activeTab.language}</span>
          )}
          {activeTab?.encoding && (
            <span
              data-testid="code-encoding"
              className="text-[10px] font-mono text-[var(--text-tertiary)]"
              title="File encoding used when saving"
            >
              {activeTab.encoding}
            </span>
          )}
          {activeTab?.truncated && (
            <span className="text-[10px] text-[var(--warning)]">Read-only (truncated)</span>
          )}
          {readOnly && !activeTab?.truncated && (
            <span className="text-[10px] text-[var(--warning)]">{"Read-only"}</span>
          )}
          {activeTab?.modified && (
            <span className="text-[10px] text-[var(--warning)]">● Modified</span>
          )}
        </div>
        <div className="flex items-center gap-1">
          {error && (
            <span className="text-[10px] text-[var(--danger)] flex items-center gap-1">
              <AlertTriangle size={10} /> {error}
            </span>
          )}
          {activeTab?.modified && !editorReadOnly && (
            <button
              onClick={() => void handleSave()}
              disabled={saving}
              className="flex items-center gap-1 px-2 py-0.5 rounded text-[10px] text-[var(--accent)] hover:bg-[var(--accent-bg)] transition-colors disabled:opacity-50"
            >
              <Save size={10} />
              {saving ? "Saving..." : "Save"}
            </button>
          )}
        </div>
      </div>

      {/* Unsaved buffer vs. disk copy — the "Show diff" banner action */}
      {conflict && diffView && (
        <Modal
          open={diffOpen}
          onOpenChange={setDiffOpen}
          title={`Changes in ${baseName(conflict.path)}`}
          description={`On disk (${conflict.tool}) vs your unsaved buffer — ${diffView.added} added, ${diffView.removed} removed`}
          size="xl"
          footer={(
            <>
              <button
                onClick={() => setDiffOpen(false)}
                className="px-2.5 py-1 rounded text-[11px] border border-[var(--border)] text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] transition-colors"
              >
                Keep my buffer
              </button>
              <button
                onClick={() => applyDiskContent(conflict.path, conflict.diskContent, conflict.mtimeMs, conflict.encoding)}
                className="px-2.5 py-1 rounded text-[11px] bg-[var(--brand)] text-[var(--text-on-brand)] hover:bg-[var(--brand-hover)] transition-colors"
              >
                Reload from disk
              </button>
            </>
          )}
        >
          <div data-testid="code-diff-body" className="font-mono text-[11px] leading-relaxed">
            {diffView.hiddenBefore > 0 && (
              <div className="px-2 py-0.5 text-[var(--text-tertiary)]">
                … {diffView.hiddenBefore} unchanged {diffView.hiddenBefore === 1 ? "line" : "lines"} above
              </div>
            )}
            {diffView.lines.map((line, idx) => (
              <div
                // eslint-disable-next-line react/no-array-index-key
                key={idx}
                className={cn(
                  "flex gap-2 px-2 whitespace-pre-wrap",
                  line.kind === "added"
                    ? "bg-[var(--success-bg)] text-[var(--success)]"
                    : line.kind === "removed"
                      ? "bg-[var(--danger-bg)] text-[var(--danger)]"
                      : "text-[var(--text-secondary)]",
                )}
              >
                <span className="w-4 shrink-0 text-center opacity-70">
                  {line.kind === "added" ? "+" : line.kind === "removed" ? "-" : " "}
                </span>
                <span className="w-8 shrink-0 text-right text-[var(--text-tertiary)]">{line.before ?? ""}</span>
                <span className="w-8 shrink-0 text-right text-[var(--text-tertiary)]">{line.after ?? ""}</span>
                <span className="min-w-0">{line.text || " "}</span>
              </div>
            ))}
            {diffView.hiddenAfter > 0 && (
              <div className="px-2 py-0.5 text-[var(--text-tertiary)]">
                … {diffView.hiddenAfter} unchanged {diffView.hiddenAfter === 1 ? "line" : "lines"} below
              </div>
            )}
            {diffView.lines.length === 0 && (
              <p className="text-[var(--text-tertiary)]">The buffer and the disk copy are identical.</p>
            )}
          </div>
        </Modal>
      )}
      {/* Closing a tab with unsaved edits asks before the buffer is dropped. */}
      {pendingClose && (
        <DiscardChangesDialog
          fileName={baseName(pendingClose)}
          description="This tab has unsaved edits. Closing it throws them away."
          body="Save with Ctrl/Cmd+S to keep your edits, or discard them and close the tab."
          confirmTestId="code-discard-confirm"
          onCancel={() => setPendingClose(null)}
          onDiscard={() => { const path = pendingClose; setPendingClose(null); discardTab(path); }}
        />
      )}
    </div>
  );
}
