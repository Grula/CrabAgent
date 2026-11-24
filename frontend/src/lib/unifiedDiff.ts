/**
 * Minimal classifier for the `diff_preview.unified` payload of a tool
 * confirmation. The backend already produced the text, so this only tags each
 * line for styling — it never applies or validates the diff.
 */

export type UnifiedDiffLineKind = "file" | "hunk" | "added" | "removed" | "context";

export interface UnifiedDiffLine {
  kind: UnifiedDiffLineKind;
  /** The line as received, prefix included. */
  text: string;
}

const FILE_PREFIXES = ["---", "+++"];

export function parseUnifiedDiff(unified: string): UnifiedDiffLine[] {
  if (!unified) return [];
  const raw = unified.split("\n");
  // A trailing newline does not add an extra empty line.
  if (raw.length > 0 && raw[raw.length - 1] === "") raw.pop();
  return raw.map((text) => {
    // File headers first: "--- a/x" must not be read as a removed "-- a/x".
    if (FILE_PREFIXES.some((prefix) => text.startsWith(prefix))) {
      return { kind: "file", text };
    }
    if (text.startsWith("@@")) return { kind: "hunk", text };
    if (text.startsWith("+")) return { kind: "added", text };
    if (text.startsWith("-")) return { kind: "removed", text };
    return { kind: "context", text };
  });
}
