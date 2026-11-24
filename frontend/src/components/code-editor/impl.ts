import { Compartment, EditorState, type Extension } from "@codemirror/state";
import {
  EditorView,
  drawSelection,
  highlightActiveLine,
  highlightActiveLineGutter,
  highlightSpecialChars,
  keymap,
  lineNumbers,
} from "@codemirror/view";
import { defaultKeymap, history, historyKeymap, indentLess } from "@codemirror/commands";
import {
  HighlightStyle,
  bracketMatching,
  indentOnInput,
  indentUnit,
  syntaxHighlighting,
} from "@codemirror/language";
import { highlightSelectionMatches, search, searchKeymap } from "@codemirror/search";
import { tags } from "@lezer/highlight";
import { css } from "@codemirror/lang-css";
import { html } from "@codemirror/lang-html";
import { javascript } from "@codemirror/lang-javascript";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { python } from "@codemirror/lang-python";

const INDENT = "    ";

export interface CreateEditorOptions {
  /** Initial document text. */
  doc: string;
  /** Language name as produced by CodePanel's detectLanguage(). */
  language: string;
  /** Read-only editors can still be selected and copied, but not edited. */
  readOnly?: boolean;
  /** Called on every user edit with the new document text. */
  onChange: (content: string) => void;
}

export interface CodeEditorHandle {
  /** The live CodeMirror view — the seam for external-change sync (T6c). */
  view: EditorView;
  getContent(): string;
  /** Apply an external change, optionally keeping the current selection offsets. */
  replaceContent(next: string, options?: { preserveSelection?: boolean }): void;
  getSelection(): { from: number; to: number };
  setReadOnly(readOnly: boolean): void;
  focus(): void;
  destroy(): void;
}

/**
 * Editor chrome built from the design tokens in src/index.css, so light and
 * dark mode follow [data-theme] without a second palette.
 */
const editorTheme = EditorView.theme({
  "&": {
    height: "100%",
    backgroundColor: "transparent",
    color: "var(--text-primary)",
    fontSize: "12px",
  },
  "&.cm-focused": { outline: "none" },
  ".cm-scroller": {
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
    lineHeight: "1.6",
    overflow: "auto",
  },
  ".cm-content": {
    padding: "8px 0",
    caretColor: "var(--brand)",
  },
  ".cm-line": { padding: "0 8px" },
  ".cm-gutters": {
    backgroundColor: "var(--bg-secondary)",
    color: "var(--text-tertiary)",
    border: "none",
    borderRight: "1px solid var(--border)",
    paddingLeft: "4px",
  },
  ".cm-lineNumbers .cm-gutterElement": { padding: "0 8px 0 4px" },
  ".cm-activeLineGutter": {
    backgroundColor: "var(--bg-tertiary)",
    color: "var(--text-secondary)",
  },
  ".cm-activeLine": {
    backgroundColor: "color-mix(in srgb, var(--bg-tertiary) 55%, transparent)",
  },
  ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--brand)" },
  ".cm-selectionBackground, &.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-content ::selection":
    { backgroundColor: "var(--brand-bg-strong)" },
  ".cm-matchingBracket, &.cm-focused .cm-matchingBracket": {
    backgroundColor: "var(--brand-bg-strong)",
    color: "inherit",
    outline: "1px solid var(--brand-border)",
  },
  ".cm-nonmatchingBracket": { color: "var(--danger)" },
  ".cm-selectionMatch": { backgroundColor: "var(--accent-bg)" },
  ".cm-searchMatch": {
    backgroundColor: "var(--warning-bg)",
    outline: "1px solid var(--warning-border)",
  },
  ".cm-searchMatch.cm-searchMatch-selected": {
    backgroundColor: "var(--brand-bg-strong)",
  },
  ".cm-panels": {
    backgroundColor: "var(--bg-secondary)",
    color: "var(--text-primary)",
    borderBottom: "1px solid var(--border)",
    fontFamily: "inherit",
  },
  ".cm-panels.cm-panels-top": { borderBottom: "1px solid var(--border)" },
  ".cm-panel.cm-search": { padding: "6px 8px", fontSize: "11px" },
  ".cm-panel.cm-search label, .cm-panel.cm-search br": { fontSize: "11px" },
  ".cm-textfield": {
    backgroundColor: "var(--bg-tertiary)",
    color: "var(--text-primary)",
    border: "1px solid var(--border)",
    borderRadius: "4px",
    padding: "2px 6px",
    fontFamily: "inherit",
    fontSize: "11px",
  },
  ".cm-button": {
    backgroundImage: "none",
    backgroundColor: "var(--bg-tertiary)",
    color: "var(--text-secondary)",
    border: "1px solid var(--border)",
    borderRadius: "4px",
    padding: "2px 8px",
    fontFamily: "inherit",
    fontSize: "11px",
  },
  ".cm-button:hover": {
    backgroundColor: "var(--bg-elevated)",
    color: "var(--text-primary)",
  },
});

/** Syntax colors, all from the shared token palette. */
const crabHighlightStyle = HighlightStyle.define([
  { tag: [tags.keyword, tags.modifier, tags.operatorKeyword, tags.controlKeyword], color: "var(--accent-2)" },
  { tag: [tags.string, tags.special(tags.string), tags.regexp], color: "var(--success)" },
  { tag: [tags.number, tags.bool, tags.null, tags.atom], color: "var(--warning)" },
  { tag: [tags.comment, tags.lineComment, tags.blockComment, tags.docComment], color: "var(--text-tertiary)", fontStyle: "italic" },
  { tag: [tags.function(tags.variableName), tags.function(tags.propertyName), tags.definition(tags.function(tags.variableName))], color: "var(--brand)" },
  { tag: [tags.typeName, tags.className, tags.namespace, tags.standard(tags.typeName)], color: "var(--accent)" },
  { tag: [tags.tagName, tags.heading, tags.strong], color: "var(--brand)" },
  { tag: [tags.attributeName, tags.propertyName], color: "var(--text-secondary)" },
  { tag: [tags.attributeValue], color: "var(--success)" },
  { tag: [tags.link, tags.url], color: "var(--accent)", textDecoration: "underline" },
  { tag: [tags.emphasis], fontStyle: "italic" },
  { tag: [tags.meta, tags.annotation, tags.processingInstruction], color: "var(--text-tertiary)" },
  { tag: [tags.operator, tags.punctuation, tags.separator, tags.bracket], color: "var(--text-secondary)" },
  { tag: [tags.invalid], color: "var(--danger)" },
]);

function languageExtension(language: string): Extension {
  switch (language) {
    case "python":
      return python();
    case "javascript":
      return javascript();
    case "javascriptreact":
      return javascript({ jsx: true });
    case "typescript":
      return javascript({ typescript: true });
    case "typescriptreact":
      return javascript({ typescript: true, jsx: true });
    case "json":
      return json();
    case "css":
    case "scss":
      return css();
    case "html":
    case "vue":
    case "svelte":
      return html();
    case "markdown":
      return markdown();
    default:
      return [];
  }
}

/** Tab inserts 4 spaces (the behaviour the textarea editor had). */
const tabKeymap = [
  {
    key: "Tab",
    run: (view: EditorView): boolean => {
      if (view.state.readOnly) return false;
      view.dispatch(
        view.state.update(view.state.replaceSelection(INDENT), {
          scrollIntoView: true,
          userEvent: "input",
        }),
      );
      return true;
    },
  },
  { key: "Shift-Tab", run: indentLess },
];

/**
 * The smallest single-span change between two documents: the shared prefix and
 * suffix are trimmed, so only the edited middle is replaced. A whole-document
 * replacement would move every offset and lose the caret; this keeps offsets
 * outside the edit intact, which is what makes them mappable.
 */
export function minimalChange(
  before: string,
  after: string,
): { from: number; to: number; insert: string } {
  const limit = Math.min(before.length, after.length);
  let prefix = 0;
  while (prefix < limit && before.charCodeAt(prefix) === after.charCodeAt(prefix)) prefix += 1;
  let suffix = 0;
  const maxSuffix = Math.min(before.length - prefix, after.length - prefix);
  while (
    suffix < maxSuffix
    && before.charCodeAt(before.length - 1 - suffix) === after.charCodeAt(after.length - 1 - suffix)
  ) {
    suffix += 1;
  }
  return {
    from: prefix,
    to: before.length - suffix,
    insert: after.slice(prefix, after.length - suffix),
  };
}

export async function createCodeEditor(
  host: HTMLElement,
  options: CreateEditorOptions,
): Promise<CodeEditorHandle> {
  const readOnlyCompartment = new Compartment();
  const languageCompartment = new Compartment();

  const view = new EditorView({
    parent: host,
    state: EditorState.create({
      doc: options.doc,
      extensions: [
        lineNumbers(),
        highlightActiveLineGutter(),
        highlightSpecialChars(),
        history(),
        drawSelection(),
        indentOnInput(),
        bracketMatching(),
        highlightActiveLine(),
        highlightSelectionMatches(),
        search({ top: true }),
        EditorState.allowMultipleSelections.of(true),
        indentUnit.of(INDENT),
        syntaxHighlighting(crabHighlightStyle),
        languageCompartment.of(languageExtension(options.language)),
        readOnlyCompartment.of(EditorState.readOnly.of(options.readOnly === true)),
        keymap.of([...tabKeymap, ...searchKeymap, ...historyKeymap, ...defaultKeymap]),
        EditorView.updateListener.of((update) => {
          if (update.docChanged) options.onChange(update.state.doc.toString());
        }),
        editorTheme,
        EditorView.contentAttributes.of({ "aria-label": "Code editor" }),
      ],
    }),
  });

  return {
    view,
    getContent: () => view.state.doc.toString(),
    replaceContent: (next, opts) => {
      const state = view.state;
      const current = state.doc.toString();
      if (current === next) return;
      // Replace only the edited middle and map every selection endpoint through
      // the resulting ChangeDesc (assoc=1), so a caret before the edit stays on
      // its own text and a caret after it does not move.
      const changes = state.changes(minimalChange(current, next));
      const selection = opts?.preserveSelection === false
        ? undefined
        : state.selection.map(changes, 1);
      // An external reload must not scroll the reader away from where they were.
      const scroller = view.scrollDOM;
      const scrollTop = scroller.scrollTop;
      const scrollLeft = scroller.scrollLeft;
      view.dispatch({ changes, selection });
      scroller.scrollTop = scrollTop;
      scroller.scrollLeft = scrollLeft;
    },
    getSelection: () => {
      const range = view.state.selection.main;
      return { from: range.from, to: range.to };
    },
    setReadOnly: (readOnly) => {
      view.dispatch({ effects: readOnlyCompartment.reconfigure(EditorState.readOnly.of(readOnly)) });
    },
    focus: () => view.focus(),
    destroy: () => view.destroy(),
  };
}
