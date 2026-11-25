/**
 * The Documents tab's two-level model, as pure decisions.
 *
 * ChatPage owns the state (`openDocs`, `currentDocPath`, `lastCodePath`); these
 * helpers answer the two questions that used to be answered in two places —
 * "what does the strip show" and "what takes over when something closes" — so
 * the tab's own close button and a panel's close button cannot drift apart.
 */
import type { DocTab } from "../components/DocTabs";

/** One document open in the strip (the code editor keeps its own tabs). */
export interface OpenDocRef {
  path: string;
  name: string;
}

/** Basename of a workspace path, used for the tab chip labels. */
export function fileBaseName(path: string): string {
  return path.split("/").pop() || path;
}

/**
 * What one CodePanel state report changes in ChatPage's tab state. An absent
 * field means "leave it alone".
 */
export interface EditorReportUpdate {
  /** New `lastCodePath`: null when the editor has no tab left. */
  lastCodePath?: string | null;
  /** New `currentDocPath`: the code tab the report followed. */
  activePath?: string | null;
}

/**
 * Apply a CodePanel state report to the tab state.
 * - `openPath === null`: every code tab is closed, so the chip goes with them
 *   (a chip without a tab opens an empty editor). The visible area only clears
 *   when the editor is the panel on screen; a document keeps showing otherwise.
 * - a live tab behind an active document: the chip path follows it, but the
 *   document stays active — stealing the path would replace it with the editor.
 */
export function editorReportUpdate(
  report: { openPath: string | null },
  options: { codeActive: boolean },
): EditorReportUpdate {
  if (report.openPath === null) {
    return options.codeActive
      ? { lastCodePath: null, activePath: null }
      : { lastCodePath: null };
  }
  if (!options.codeActive) return {};
  return { lastCodePath: report.openPath, activePath: report.openPath };
}

/**
 * The strip: the code chip (once the editor started and still holds a tab),
 * then the open documents. The chip is what makes the hidden editor reachable,
 * so it only exists while there is an editor tab behind it.
 */
export function buildDocTabItems(options: {
  codeStarted: boolean;
  lastCodePath: string | null;
  codeActive: boolean;
  openDocs: OpenDocRef[];
  currentDocPath: string | null;
}): DocTab[] {
  const items: DocTab[] = [];
  if (options.codeStarted && options.lastCodePath) {
    items.push({
      path: options.lastCodePath,
      name: fileBaseName(options.lastCodePath),
      kind: "code",
      active: options.codeActive,
    });
  }
  for (const doc of options.openDocs) {
    items.push({
      path: doc.path,
      name: doc.name,
      kind: "doc",
      active: options.currentDocPath === doc.path,
    });
  }
  return items;
}

/** What takes over in the content area after the active item goes away. */
export type CloseFallback =
  | { kind: "code"; path: string }
  | { kind: "doc"; path: string; name: string }
  | null;

/**
 * The fallback chain both close paths share: the code chip when the editor
 * still has a tab, else the neighbouring document (same index, clamped to the
 * end of the shortened list), else nothing and the empty state shows.
 */
export function fallbackAfterClose(options: {
  /** Open documents, still including `closedPath`. */
  openDocs: OpenDocRef[];
  /** The document being closed, or null when none is active. */
  closedPath: string | null;
  /** `lastCodePath`, but only while CodePanel reports a live tab. */
  codePath: string | null;
}): CloseFallback {
  const { openDocs, closedPath, codePath } = options;
  if (codePath && codePath !== closedPath) return { kind: "code", path: codePath };
  const index = closedPath === null ? -1 : openDocs.findIndex((doc) => doc.path === closedPath);
  const remaining = closedPath === null ? openDocs : openDocs.filter((doc) => doc.path !== closedPath);
  const neighbour = remaining[index < 0 ? 0 : Math.min(index, remaining.length - 1)];
  return neighbour ? { kind: "doc", path: neighbour.path, name: neighbour.name } : null;
}
