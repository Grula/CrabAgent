/** Agent-profile compaction threshold, as typed in the Agents page form. */

export const MIN_COMPACTION_TOKENS = 20000;

/** The typed compaction threshold as the API wants it: null for empty, else a whole number. */
export function parseCompaction(text: string): number | null {
  const trimmed = text.trim().replace(/[_,\s]/g, "");
  if (!trimmed) return null;
  const k = /^(\d+)k$/i.exec(trimmed);
  const value = k ? Number(k[1]) * 1000 : Number(trimmed);
  if (!Number.isInteger(value) || value < MIN_COMPACTION_TOKENS) {
    throw new Error(`Compaction threshold must be at least ${MIN_COMPACTION_TOKENS} tokens, or empty`);
  }
  return value;
}
