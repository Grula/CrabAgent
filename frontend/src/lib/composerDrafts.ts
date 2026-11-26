/**
 * Unsent composer text, kept per chat: switching chats keeps each chat's draft
 * with that chat, and a reload brings it back. Browser storage is a convenience
 * here (it can be blocked or cleared), so every access is wrapped and an
 * in-memory copy keeps drafts working within the page either way.
 */

const PREFIX = "crab_draft:";
const MAX_CHARS = 20000;
const memory = new Map<string, string>();

export function loadDraft(key: string): string {
  if (memory.has(key)) return memory.get(key) ?? "";
  try {
    return localStorage.getItem(PREFIX + key) ?? "";
  } catch {
    return "";
  }
}

export function saveDraft(key: string, text: string): void {
  const value = text.slice(0, MAX_CHARS);
  if (value.trim()) memory.set(key, value);
  else memory.delete(key);
  try {
    if (value.trim()) localStorage.setItem(PREFIX + key, value);
    else localStorage.removeItem(PREFIX + key);
  } catch {
    /* storage unavailable: the in-memory copy still works */
  }
}
