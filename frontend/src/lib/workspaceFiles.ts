/**
 * Which files the Work→Documents tab can open, and which panel renders each one.
 *
 * The tree's "Open in workspace" action and ChatPage's panel routing both read
 * these sets, so a file can never be offered in the tree and then fail to open.
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

/** Panel that renders a file in the Documents tab. */
export type WorkspaceFileKind = "code" | "markdown" | "prototype" | "document";

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
 * the office documents and anything with no dedicated panel. Code, Markdown
 * and HTML render in their own panel and must not call the preview service.
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
 * True when clicking another Work tab would unmount the code editor and throw
 * its buffers away. Only the Documents tab hosts the editor, so leaving it while
 * a buffer is dirty needs the same confirmation as the other discard paths.
 */
export function needsDiscardConfirmForWorkTabSwitch(args: {
  /** Work tab shown right now. */
  currentTab: string;
  /** Work tab the user clicked. */
  nextTab: string;
  /** Whether the code panel holds unsaved edits in any of its open tabs. */
  codeDirty: boolean;
}): boolean {
  if (args.nextTab === args.currentTab) return false;
  if (args.currentTab !== "documents") return false;
  return args.codeDirty;
}

/**
 * True when switching chats would carry unsaved buffers into a different
 * workspace. The documents are dropped either way — a stale buffer must never be
 * saved into the new chat's workspace — so a dirty one confirms first.
 */
export function needsDiscardConfirmForSessionSwitch(args: { codeDirty: boolean }): boolean {
  return args.codeDirty;
}
