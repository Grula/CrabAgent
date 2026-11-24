import type { CodeEditorHandle, CreateEditorOptions } from "./impl";

export type { CodeEditorHandle, CreateEditorOptions };

/**
 * CodeMirror is loaded on first use through a dynamic import so it lands in its
 * own bundle chunk instead of the main bundle.
 */
export async function createCodeEditor(
  host: HTMLElement,
  options: CreateEditorOptions,
): Promise<CodeEditorHandle> {
  const mod = await import("./impl");
  return mod.createCodeEditor(host, options);
}
