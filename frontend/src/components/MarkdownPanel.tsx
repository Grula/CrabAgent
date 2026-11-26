import { useState, useEffect, useCallback, useRef } from "react";
import { Save, FileText, Code2, Eye, Columns2, AlertTriangle } from "lucide-react";
import { cn } from "../lib/cn";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { readFile, saveFile, type SaveFileOptions } from "../api/files";
import { ApiError } from "../api/client";
import { isAbsolutePath } from "../lib/workspaceFiles";

type ViewMode = "split" | "source" | "preview";

interface Props {
  filePath: string;
  className?: string;
  onClose?: () => void;
  /** Chat session whose workspace a relative path resolves against. */
  sessionId?: string;
  /** Called when the buffer becomes dirty or clean (edit, save, reload). */
  onDirtyChange?: (dirty: boolean) => void;
}

/** Only pass session_id when there is one, so the param is never "undefined". */
function sessionOptions(sessionId?: string) {
  return sessionId ? { sessionId } : {};
}

export function MarkdownPanel({ filePath, className, onClose, sessionId, onDirtyChange }: Props) {
  const [source, setSource] = useState("");
  const [originalSource, setOriginalSource] = useState("");
  const [viewMode, setViewMode] = useState<ViewMode>("split");
  const [loading, setLoading] = useState(true);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // A >1 MB (truncated) or binary file must never be editable or savable.
  const [truncated, setTruncated] = useState(false);
  const [binary, setBinary] = useState(false);
  // The disk version this buffer came from: sent as expected_mtime_ms on save so
  // a stale write is refused with 409 instead of silently clobbering another writer.
  const [mtimeMs, setMtimeMs] = useState<number | undefined>(undefined);
  // The encoding the bytes were decoded with; echo it back so latin-1 stays latin-1.
  const [encoding, setEncoding] = useState<"utf-8" | "latin-1" | undefined>(undefined);
  // A save the server rejected as stale: offer the disk copy, never merge it.
  const [conflict, setConflict] = useState<{
    diskContent: string;
    mtimeMs?: number;
    encoding?: "utf-8" | "latin-1";
  } | null>(null);
  const readOnly = truncated || binary;
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // The buffer as it stands right now: read after a save's await to tell a
  // completed save from one the user typed over while it was in flight.
  const sourceRef = useRef(source);
  sourceRef.current = source;
  // Held in a ref so an inline callback from the parent never re-runs the effect.
  const onDirtyChangeRef = useRef(onDirtyChange);
  onDirtyChangeRef.current = onDirtyChange;

  const fileName = filePath.split("/").pop() || filePath;

  // ── Load file content ──────────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    readFile(filePath, isAbsolutePath(filePath), sessionOptions(sessionId))
      .then((res) => {
        if (cancelled) return;
        setSource(res.content ?? "");
        setOriginalSource(res.content ?? "");
        setDirty(false);
        setTruncated(res.truncated === true);
        setBinary(res.binary === true);
        setMtimeMs(res.mtime_ms);
        setEncoding(res.encoding);
        setConflict(null);
      })
      .catch((e: any) => {
        if (!cancelled) setError(e?.message || "Failed to load file");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [filePath, sessionId]);

  // ── Report dirtiness to the parent (tab-switch guard) ──────
  const lastReportedDirty = useRef<boolean | null>(null);
  useEffect(() => {
    if (lastReportedDirty.current === dirty) return;
    lastReportedDirty.current = dirty;
    onDirtyChangeRef.current?.(dirty);
  }, [dirty]);

  // ── Save ───────────────────────────────────────────────────
  /**
   * The server refused the save as stale (HTTP 409): read the disk copy and
   * offer Reload / keep mine. The buffer itself is never overwritten.
   */
  const showStaleConflict = useCallback(async () => {
    try {
      const res = await readFile(filePath, isAbsolutePath(filePath), sessionOptions(sessionId));
      if (res.binary || res.truncated) {
        setError("The file changed on disk. Reload it before saving.");
        return;
      }
      setConflict({ diskContent: res.content ?? "", mtimeMs: res.mtime_ms, encoding: res.encoding });
      setError(null);
    } catch {
      setError("The file changed on disk. Reload it before saving.");
    }
  }, [filePath, sessionId]);

  const handleSave = useCallback(async () => {
    if (!dirty || readOnly) return;
    setSaving(true);
    // The exact text being written: dirtiness is decided against it once the
    // write lands, so typing during the save stays dirty instead of being
    // marked clean (and losing the next Ctrl+S and the tab-switch guard).
    const sent = source;
    const options: SaveFileOptions = { ...sessionOptions(sessionId) };
    // The disk version this buffer was read from: a stale write gets a 409.
    if (mtimeMs !== undefined) options.expectedMtimeMs = mtimeMs;
    // Keep the file's own encoding; a latin-1 file must not become UTF-8.
    if (encoding) options.encoding = encoding;
    try {
      const result = await saveFile(filePath, sent, isAbsolutePath(filePath), options);
      setOriginalSource(sent);
      // Compare with the live buffer, not the closure: an edit made while the
      // write was in flight must keep the panel dirty.
      setDirty(sourceRef.current !== sent);
      // The written file's mtime is what the next save must expect.
      setMtimeMs(result?.mtime_ms ?? mtimeMs);
      setConflict(null);
      setError(null);
    } catch (e: any) {
      if (e instanceof ApiError && e.status === 409) {
        await showStaleConflict();
      } else {
        setError(e?.message || "Save failed");
      }
    } finally {
      setSaving(false);
    }
  }, [dirty, encoding, filePath, mtimeMs, readOnly, sessionId, showStaleConflict, source]);

  /** Discard the buffer and adopt the disk copy (the conflict banner's Reload). */
  const applyDiskContent = useCallback(() => {
    if (!conflict) return;
    setSource(conflict.diskContent);
    setOriginalSource(conflict.diskContent);
    setDirty(false);
    setMtimeMs(conflict.mtimeMs);
    setEncoding(conflict.encoding);
    setConflict(null);
    setError(null);
  }, [conflict]);

  // ── Keyboard shortcuts (Cmd/Ctrl+S) ────────────────────────
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "s") {
        e.preventDefault();
        handleSave();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [handleSave]);

  const handleEdit = useCallback((val: string) => {
    if (readOnly) return; // a truncated or binary buffer must never become savable
    setSource(val);
    setDirty(val !== originalSource);
  }, [originalSource, readOnly]);

  // ── Bidirectional scroll sync (editor ↔ preview) ───────────
  const previewRef = useRef<HTMLDivElement>(null);
  const syncingScroll = useRef(false);

  const handleEditorScroll = useCallback(() => {
    if (viewMode !== "split" || syncingScroll.current) return;
    const ta = textareaRef.current;
    const pv = previewRef.current;
    if (!ta || !pv) return;
    const maxScroll = ta.scrollHeight - ta.clientHeight;
    if (maxScroll <= 0) return;
    const ratio = ta.scrollTop / maxScroll;
    syncingScroll.current = true;
    pv.scrollTop = ratio * (pv.scrollHeight - pv.clientHeight);
    requestAnimationFrame(() => { syncingScroll.current = false; });
  }, [viewMode]);

  const handlePreviewScroll = useCallback(() => {
    if (viewMode !== "split" || syncingScroll.current) return;
    const ta = textareaRef.current;
    const pv = previewRef.current;
    if (!ta || !pv) return;
    const maxScroll = pv.scrollHeight - pv.clientHeight;
    if (maxScroll <= 0) return;
    const ratio = pv.scrollTop / maxScroll;
    syncingScroll.current = true;
    ta.scrollTop = ratio * (ta.scrollHeight - ta.clientHeight);
    requestAnimationFrame(() => { syncingScroll.current = false; });
  }, [viewMode]);

  const lineCount = source.split("\n").length;

  const VIEW_BUTTONS: { mode: ViewMode; icon: typeof Code2; label: string }[] = [
    { mode: "source", icon: Code2, label: "Source" },
    { mode: "split", icon: Columns2, label: "Split" },
    { mode: "preview", icon: Eye, label: "Preview" },
  ];

  if (loading) {
    return (
      <div className={cn("flex items-center justify-center h-full text-[var(--text-tertiary)]", className)}>
        <div className="w-6 h-6 border-2 border-[var(--border)] border-t-[var(--brand)] rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <div className={cn("flex flex-col h-full bg-[var(--bg-primary)]", className)}>
      {/* ── Toolbar ─────────────────────────────────────────── */}
      <div className="flex items-center gap-1 px-3 h-11 border-b border-[var(--border)] bg-[var(--bg-secondary)] shrink-0">
        {/* View mode switcher */}
        <div className="flex items-center gap-0.5 rounded-md bg-[var(--bg-tertiary)] p-0.5">
          {VIEW_BUTTONS.map(({ mode, icon: Icon, label }) => (
            <button
              key={mode}
              onClick={() => setViewMode(mode)}
              className={cn(
                "flex items-center gap-1.5 px-2.5 py-1 rounded text-[11px] font-medium transition-colors",
                viewMode === mode
                  ? "bg-[var(--bg-primary)] text-[var(--brand)] shadow-sm"
                  : "text-[var(--text-tertiary)] hover:text-[var(--text-primary)]",
              )}
            >
              <Icon size={12} />
              {label}
            </button>
          ))}
        </div>

        <div className="flex-1" />

        {/* File name */}
        <span className="text-sm font-medium truncate text-[var(--text-primary)] max-w-[200px]" title={fileName}>
          {fileName}
        </span>

        {/* Save button */}
        {dirty && !readOnly && (
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex items-center gap-1 px-2 py-1 ml-2 rounded text-[11px] text-[var(--accent)] hover:bg-[var(--accent-bg)] transition-colors disabled:opacity-50"
          >
            <Save size={11} />
            {saving ? "Saving..." : "Save"}
          </button>
        )}

        {onClose && (
          <button
            onClick={onClose}
            className="ml-1 p-1 rounded text-[var(--text-tertiary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors"
          >
            <span className="text-xs">✕</span>
          </button>
        )}
      </div>

      {/* ── Read-only notice (truncated file) ───────────────── */}
      {truncated && (
        <div
          data-testid="markdown-truncated-notice"
          className="flex items-center gap-1.5 px-3 py-1.5 text-[11px] shrink-0 bg-[var(--warning-bg)] text-[var(--warning)] border-b border-[var(--warning-border)]"
        >
          <AlertTriangle size={12} />
          This file is truncated because it is too large. It is read-only and cannot be saved from the UI.
        </div>
      )}

      {/* ── Stale save: the disk copy changed under the buffer ─ */}
      {conflict && (
        <div
          data-testid="markdown-conflict"
          className="flex items-center gap-2 px-3 py-1.5 text-[11px] shrink-0 bg-[var(--warning-bg)] text-[var(--warning)] border-b border-[var(--warning-border)]"
        >
          <AlertTriangle size={12} />
          <span className="min-w-0 truncate">This file changed on disk</span>
          <span className="flex-1" />
          <button
            onClick={applyDiskContent}
            className="px-2 py-0.5 rounded border border-[var(--warning-border)] hover:bg-[var(--bg-tertiary)] transition-colors"
          >
            Reload
          </button>
          <button
            onClick={() => setConflict(null)}
            className="px-2 py-0.5 rounded border border-[var(--warning-border)] hover:bg-[var(--bg-tertiary)] transition-colors"
          >
            Keep mine
          </button>
        </div>
      )}

      {/* ── Main content ────────────────────────────────────── */}
      {binary ? (
        <div data-testid="markdown-binary" className="flex-1 flex flex-col items-center justify-center gap-2 px-6 text-center">
          <AlertTriangle size={28} className="opacity-30" />
          <p className="text-sm text-[var(--text-secondary)]">This file cannot be edited as text</p>
          <p className="text-[11px] text-[var(--text-tertiary)]">
            Binary files are read-only and are not shown in the Markdown editor.
          </p>
        </div>
      ) : (
      <div className="flex-1 flex min-h-0">
        {/* Left: Source editor (visible in source & split) */}
        {(viewMode === "source" || viewMode === "split") && (
          <div className={cn(
            "flex min-h-0",
            viewMode === "split" ? "w-1/2 border-r border-[var(--border)]" : "w-full",
          )}>
            {/* Line numbers */}
            <div className="select-none text-right px-2 py-3 text-[12px] leading-[1.6] font-mono text-[var(--text-tertiary)] bg-[var(--bg-secondary)] border-r border-[var(--border)] shrink-0 overflow-hidden">
              {Array.from({ length: lineCount }, (_, i) => (
                <div key={i}>{i + 1}</div>
              ))}
            </div>

            {/* Textarea */}
            <textarea
              ref={textareaRef}
              value={source}
              onChange={(e) => handleEdit(e.target.value)}
              onScroll={handleEditorScroll}
              readOnly={readOnly}
              className={cn(
                "flex-1 bg-transparent text-[var(--text-primary)] text-[12px] leading-[1.6] font-mono p-3 border-0 resize-none outline-none",
                readOnly && "cursor-default",
              )}
              style={{ tabSize: 4, whiteSpace: "pre", overflowWrap: "normal", overflowX: "auto" }}
              spellCheck={false}
            />
          </div>
        )}

        {/* Right: Rendered preview (visible in preview & split) */}
        {(viewMode === "preview" || viewMode === "split") && (
          <div
            ref={previewRef}
            onScroll={handlePreviewScroll}
            className={cn(
              "overflow-auto p-6 bg-[var(--bg-primary)]",
              viewMode === "split" ? "w-1/2" : "w-full",
            )}
          >
            <div className="markdown-body max-w-[800px] mx-auto">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[rehypeHighlight]}
              >
                {source || `*${"Nothing to preview yet."}*`}
              </ReactMarkdown>
            </div>
          </div>
        )}
      </div>
      )}

      {/* ── Status bar ──────────────────────────────────────── */}
      <div className="flex items-center justify-between px-3 py-1 border-t border-[var(--border)] bg-[var(--bg-secondary)] shrink-0">
        <div className="flex items-center gap-2">
          <FileText size={10} className="text-[var(--text-tertiary)]" />
          <span className="text-[10px] font-mono text-[var(--text-tertiary)]">markdown</span>
          {dirty && !readOnly && (
            <span className="text-[10px] text-[var(--warning)]">● {"Modified"}</span>
          )}
          {truncated && (
            <span className="text-[10px] text-[var(--warning)]">{"Read-only (truncated)"}</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {error && (
            <span className="text-[10px] text-[var(--danger)] flex items-center gap-1">
              <AlertTriangle size={10} /> {error}
            </span>
          )}
          <span className="text-[10px] font-mono text-[var(--text-tertiary)]">
            {lineCount} lines
          </span>
        </div>
      </div>
    </div>
  );
}
