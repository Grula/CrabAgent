/**
 * A minimal line diff for the "changed on disk" review modal.
 *
 * Dependency-free on purpose: the panel only ever compares two versions of the
 * file the user is looking at, so an LCS over the edited middle plus a little
 * context either side is enough — no need for a diff package.
 */

export type DiffKind = "context" | "added" | "removed";

export interface DiffLine {
  kind: DiffKind;
  text: string;
  /** 1-indexed line in the buffer, or null for a disk-only line. */
  before: number | null;
  /** 1-indexed line on disk, or null for a buffer-only line. */
  after: number | null;
}

export interface LineDiff {
  lines: DiffLine[];
  /** Buffer lines hidden above / below the shown window. */
  hiddenBefore: number;
  hiddenAfter: number;
  added: number;
  removed: number;
}

type Op = { kind: DiffKind; text: string };

/** Lines of a document; a trailing newline does not add an extra empty line. */
function splitLines(text: string): string[] {
  if (text === "") return [];
  const lines = text.split("\n");
  if (lines[lines.length - 1] === "") lines.pop();
  return lines;
}

/** Above this many middle lines the pairwise table is skipped for a plain replace. */
const LCS_CELL_LIMIT = 160_000;

function middleOps(before: string[], after: string[]): Op[] {
  if (before.length === 0) return after.map((text) => ({ kind: "added" as const, text }));
  if (after.length === 0) return before.map((text) => ({ kind: "removed" as const, text }));
  if (before.length * after.length > LCS_CELL_LIMIT) {
    return [
      ...before.map((text) => ({ kind: "removed" as const, text })),
      ...after.map((text) => ({ kind: "added" as const, text })),
    ];
  }

  const n = before.length;
  const m = after.length;
  const width = m + 1;
  // dp[i][j] = LCS length of before[0..i) and after[0..j).
  const dp = new Uint32Array((n + 1) * width);
  for (let i = 1; i <= n; i += 1) {
    for (let j = 1; j <= m; j += 1) {
      dp[i * width + j] = before[i - 1] === after[j - 1]
        ? dp[(i - 1) * width + (j - 1)] + 1
        : Math.max(dp[(i - 1) * width + j], dp[i * width + (j - 1)]);
    }
  }

  const ops: Op[] = [];
  let i = n;
  let j = m;
  while (i > 0 && j > 0) {
    if (before[i - 1] === after[j - 1]) {
      ops.push({ kind: "context", text: before[i - 1] });
      i -= 1;
      j -= 1;
    } else if (dp[(i - 1) * width + j] >= dp[i * width + (j - 1)]) {
      ops.push({ kind: "removed", text: before[i - 1] });
      i -= 1;
    } else {
      ops.push({ kind: "added", text: after[j - 1] });
      j -= 1;
    }
  }
  while (i > 0) {
    ops.push({ kind: "removed", text: before[i - 1] });
    i -= 1;
  }
  while (j > 0) {
    ops.push({ kind: "added", text: after[j - 1] });
    j -= 1;
  }
  return ops.reverse();
}

/**
 * Compare the editor buffer (`before`) with the disk copy (`after`).
 * Unchanged runs far from the edit are elided and reported via hidden*.
 */
export function diffLines(before: string, after: string, context = 2): LineDiff {
  const a = splitLines(before);
  const b = splitLines(after);

  let prefix = 0;
  const limit = Math.min(a.length, b.length);
  while (prefix < limit && a[prefix] === b[prefix]) prefix += 1;
  let suffix = 0;
  while (
    suffix < limit - prefix
    && a[a.length - 1 - suffix] === b[b.length - 1 - suffix]
  ) {
    suffix += 1;
  }

  const midA = a.slice(prefix, a.length - suffix);
  const midB = b.slice(prefix, b.length - suffix);
  const ops = middleOps(midA, midB);

  const start = Math.max(0, prefix - context);
  const end = Math.min(a.length, a.length - suffix + context);
  const tailStart = a.length - suffix;

  const lines: DiffLine[] = [];
  let beforeLine = start + 1;
  let afterLine = start + 1; // context before the edit maps 1:1
  for (let idx = start; idx < prefix; idx += 1) {
    lines.push({ kind: "context", text: a[idx], before: beforeLine, after: afterLine });
    beforeLine += 1;
    afterLine += 1;
  }
  for (const op of ops) {
    if (op.kind === "removed") {
      lines.push({ kind: "removed", text: op.text, before: beforeLine, after: null });
      beforeLine += 1;
    } else if (op.kind === "added") {
      lines.push({ kind: "added", text: op.text, before: null, after: afterLine });
      afterLine += 1;
    } else {
      lines.push({ kind: "context", text: op.text, before: beforeLine, after: afterLine });
      beforeLine += 1;
      afterLine += 1;
    }
  }
  for (let idx = tailStart; idx < end; idx += 1) {
    lines.push({ kind: "context", text: a[idx], before: beforeLine, after: afterLine });
    beforeLine += 1;
    afterLine += 1;
  }

  return {
    lines,
    hiddenBefore: start,
    hiddenAfter: a.length - end,
    added: ops.filter((op) => op.kind === "added").length,
    removed: ops.filter((op) => op.kind === "removed").length,
  };
}
