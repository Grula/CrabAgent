/**
 * Which files the Work→Documents tab can open, and which panel renders each one.
 *
 * The tree's "Open in workspace" action and ChatPage's panel routing both read
 * these sets, so a file can never be offered in the tree and then fail to open.
 *
 * This module stays dependency-free on purpose: api/files.ts imports from it,
 * so anything here must run without a browser (the file tree's tests include it).
 */

/** Code extensions handled by the editor (lowercase, no leading dot). */
export const CODE_EXTENSIONS: ReadonlySet<string> = new Set([
  "py", "ts", "tsx", "js", "jsx", "json", "go", "rs", "java", "c", "cpp",
  "css", "scss", "vue", "svelte", "sh", "bash", "yaml", "yml", "toml", "sql",
]);

/** Office documents rendered by the preview service. */
export const OFFICE_EXTENSIONS: ReadonlySet<string> = new Set(["xlsx", "docx", "pptx"]);

/** Markdown files rendered by MarkdownPanel. */
export const MARKDOWN_EXTENSIONS: ReadonlySet<string> = new Set(["md"]);

/** HTML prototypes rendered by PrototypePanel. */
export const PROTOTYPE_EXTENSIONS: ReadonlySet<string> = new Set(["html"]);

/** Images rendered by ImagePanel (with the dot, as isImageFile in api/files.ts expects). */
export const IMAGE_EXTENSIONS: ReadonlySet<string> = new Set([
  ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp", ".avif",
]);

/** Panel that renders a file in the Documents tab. */
export type WorkspaceFileKind = "code" | "markdown" | "prototype" | "document" | "image";

/** Lowercase extension without the dot; "" for a name that has none. */
export function fileExtension(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot < 0 ? "" : name.slice(dot + 1).toLowerCase();
}

/**
 * True when a panel was handed an absolute path (the Work file tree sends those)
 * rather than a workspace-relative one. Reads and writes must say which it is:
 * the server resolves a relative path against the session workspace, but treats
 * an absolute one as outside it unless `absolute=true` is sent.
 */
export function isAbsolutePath(path: string): boolean {
  return path.startsWith("/");
}

/** True for files the code editor opens (rather than the office preview). */
export function isCodeFile(name: string): boolean {
  return CODE_EXTENSIONS.has(fileExtension(name));
}

/** The panel for this file name, or null when the Documents tab cannot open it. */
export function workspaceFileKind(name: string): WorkspaceFileKind | null {
  const ext = fileExtension(name);
  if (!ext) return null;
  if (PROTOTYPE_EXTENSIONS.has(ext)) return "prototype";
  if (MARKDOWN_EXTENSIONS.has(ext)) return "markdown";
  // Images have their own panel; `.svg` must not fall through to code or document.
  if (IMAGE_EXTENSIONS.has(`.${ext}`)) return "image";
  if (CODE_EXTENSIONS.has(ext)) return "code";
  if (OFFICE_EXTENSIONS.has(ext)) return "document";
  return null;
}

/** True when the tree should offer "Open in workspace" for this file. */
export function opensInWorkspace(name: string): boolean {
  return workspaceFileKind(name) !== null;
}

/**
 * True for files that need an Office preview request before they can render:
 * the office documents and anything with no dedicated panel. Code, Markdown,
 * HTML and images render in their own panel and must not call the preview service.
 */
export function needsOfficePreview(name: string): boolean {
  const kind = workspaceFileKind(name);
  return kind === null || kind === "document";
}

/**
 * True when the code panel's own snapshot reports unsaved edits in any of its
 * open tabs. A null snapshot means no editor is mounted (the page clears its ref
 * when the panel unmounts), so nothing can be lost and nothing is asked.
 */
export function hasUnsavedEditorEdits(
  state: { dirty: boolean; anyDirty: boolean } | null | undefined,
): boolean {
  return state != null && (state.dirty || state.anyDirty);
}

/**
 * True when clicking another Work tab would unmount the code editor and every
 * document panel and throw their buffers away. Only the Documents tab hosts
 * them, so leaving it while a buffer or a Markdown document is dirty needs the
 * same confirmation as the other discard paths.
 */
export function needsDiscardConfirmForWorkTabSwitch(args: {
  /** Work tab shown right now. */
  currentTab: string;
  /** Work tab the user clicked. */
  nextTab: string;
  /** Whether the code panel holds unsaved edits in any of its open tabs. */
  codeDirty: boolean;
  /** Whether the Markdown document on screen reports unsaved edits. */
  markdownDirty: boolean;
}): boolean {
  if (args.nextTab === args.currentTab) return false;
  if (args.currentTab !== "documents") return false;
  return args.codeDirty || args.markdownDirty;
}

/**
 * True when switching chats would carry unsaved buffers into a different
 * workspace. The documents are dropped either way — a stale buffer must never be
 * saved into the new chat's workspace — so a dirty one confirms first. Code and
 * Markdown alike: the switch clears both panels' state.
 */
export function needsDiscardConfirmForSessionSwitch(args: {
  /** Whether the code panel holds unsaved edits in any of its open tabs. */
  codeDirty: boolean;
  /** Whether any open Markdown document reports unsaved edits. */
  markdownDirty: boolean;
}): boolean {
  return args.codeDirty || args.markdownDirty;
}

/**
 * True when showing another document (or the code chip) would remount the panel
 * on screen and throw its edits away. The document panels are keyed by path, so
 * any path change unmounts the current one; the same path, or nothing on screen,
 * changes nothing and must not ask.
 */
export function needsDiscardConfirmForDocSwitch(args: {
  /** Document shown right now; null when the Documents tab shows nothing. */
  currentPath: string | null;
  /** Document the user picked; null for the empty state. */
  nextPath: string | null;
  /** Paths whose mounted Markdown panel reports unsaved edits. */
  markdownDirtyPaths: ReadonlySet<string>;
}): boolean {
  if (args.currentPath === null) return false;
  if (args.currentPath === args.nextPath) return false;
  return args.markdownDirtyPaths.has(args.currentPath);
}

/**
 * True when closing a document would throw unsaved text away. Closing drops its
 * tab and unmounts its panel, so the tab's X and the panel's X ask the same
 * question a switch to another document asks — only with nothing as the
 * destination. A clean document, or nothing on screen, closes straight away.
 */
export function needsDiscardConfirmForDocClose(args: {
  /** Document the tab's or the panel's X closes; null when none is shown. */
  closingPath: string | null;
  /** Paths whose mounted Markdown panel reports unsaved edits. */
  markdownDirtyPaths: ReadonlySet<string>;
}): boolean {
  if (args.closingPath === null) return false;
  return args.markdownDirtyPaths.has(args.closingPath);
}

/** Test id of the close confirmation: its own, so a test can target that path alone. */
export const CLOSE_DOC_DISCARD_TEST_ID = "close-doc-discard-confirm";

/**
 * What the close confirmation says. Both X buttons close a document (never the
 * code editor, which CodePanel owns), so it names the file being closed rather
 * than "the editor", and it carries its own test id.
 */
export function closeDocDiscardPrompt(filePath: string | null): {
  dirtyName: string;
  description: string;
  body: string;
  confirmTestId: string;
} {
  const name = (filePath || "").split("/").pop() || "this file";
  return {
    dirtyName: name,
    description: `Closing ${name} throws its unsaved edits away.`,
    body: "Save with Ctrl/Cmd+S to keep your edits, or discard them and close it.",
    confirmTestId: CLOSE_DOC_DISCARD_TEST_ID,
  };
}

/**
 * Forget the dirty reports of panels that are gone. A discarded document's panel
 * is unmounted, so its report is stale: leaving the path in the set would ask
 * again about text that no longer exists — on the next document switch, Work tab
 * or leave-Work move.
 */
export function forgetDiscardedMarkdownEdits(
  dirtyPaths: Set<string>,
  discardedPaths: Iterable<string | null | undefined>,
): void {
  for (const path of discardedPaths) {
    if (path) dirtyPaths.delete(path);
  }
}

/**
 * The leave-Work discard throws away every document, not only the one the
 * dialog named: leaving Work unmounts each Documents panel, so every dirty
 * Markdown report is stale once the user accepts. Forgetting them here keeps a
 * later document from being asked about (or silently marked dirty by) a panel
 * that no longer exists. Cancel never reaches this, so it changes nothing.
 */
export function forgetLeaveWorkDiscardedMarkdownEdits(dirtyPaths: Set<string>): void {
  forgetDiscardedMarkdownEdits(dirtyPaths, [...dirtyPaths]);
}

/**
 * At most one discard confirmation may be on screen. Browser Back (an
 * out-of-band hashchange) or any other second entry point can ask while a
 * dialog is already open; queueing that request would stack two dialogs and run
 * a move behind a question the user has not answered yet. A request that arrives
 * while a prompt is pending is therefore dropped — never replayed later — and
 * the pending one stays. With nothing pending, the request becomes the prompt.
 */
export function queuePendingPrompt<T>(pending: T | null, request: T): T | null {
  return pending === null ? request : pending;
}

/**
 * True when leaving the Work page would throw unsaved work away. Work hosts both
 * the code editor and every document panel, so a dirty code buffer and a dirty
 * Markdown document count alike; either one has to confirm before the page moves.
 */
export function needsDiscardConfirmForLeaveWork(args: {
  /** Whether the code panel holds unsaved edits in any of its open tabs. */
  codeDirty: boolean;
  /** Whether any open Markdown document reports unsaved edits. */
  markdownDirty: boolean;
}): boolean {
  return args.codeDirty || args.markdownDirty;
}
