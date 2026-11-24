/**
 * Diff preview attached to a `tool_confirm_request` card (write/edit tools).
 * Purely additive: when the backend has nothing to preview the key is absent
 * and this component is never rendered, so the dialog stays as it was.
 */
import { memo, useMemo } from "react";
import type { DiffPreview } from "../types/ChatMessage";
import { parseUnifiedDiff, type UnifiedDiffLineKind } from "../lib/unifiedDiff";
import { cn } from "../lib/cn";

const LINE_CLASS: Record<UnifiedDiffLineKind, string> = {
  added: "text-[var(--success)] bg-[var(--success-bg)]",
  removed: "text-[var(--danger)] bg-[var(--danger-bg)]",
  hunk: "text-[var(--text-secondary)] opacity-80",
  file: "text-[var(--text-secondary)] opacity-60",
  context: "text-[var(--text-secondary)]",
};

export const ToolConfirmDiff = memo(function ToolConfirmDiff({ diff }: { diff: DiffPreview }) {
  const lines = useMemo(() => parseUnifiedDiff(diff.unified), [diff.unified]);
  return (
    <div
      data-testid="tool-confirm-diff"
      className="mb-2 rounded-lg border border-[var(--border)] overflow-hidden"
    >
      <div className="flex flex-wrap items-center gap-2 px-2.5 py-1.5 text-[11px] bg-[var(--bg-primary)] border-b border-[var(--border)]">
        <span
          className="font-mono text-[var(--text-primary)] truncate"
          title={diff.path}
        >
          {diff.path}
        </span>
        <span data-testid="diff-added" className="text-[var(--success)]">
          {`+${diff.added}`}
        </span>
        <span data-testid="diff-removed" className="text-[var(--danger)]">
          {`-${diff.removed}`}
        </span>
        {diff.new_file && (
          <span
            data-testid="diff-new-file"
            className="px-1.5 rounded bg-[var(--success-bg)] text-[var(--success)] border border-[var(--success-border)]"
          >
            new file
          </span>
        )}
        {diff.truncated && (
          <span
            data-testid="diff-truncated"
            className="text-[var(--warning)]"
            title="The diff was capped; only part of the change is shown."
          >
            truncated
          </span>
        )}
      </div>
      <div
        data-testid="diff-body"
        className="max-h-[40vh] overflow-auto p-2 text-[11px] leading-[1.4] font-mono bg-[var(--bg-secondary)]"
      >
        {lines.map((line, index) => (
          <div
            key={index}
            data-diff-kind={line.kind}
            className={cn("whitespace-pre", LINE_CLASS[line.kind])}
          >
            {line.text || " "}
          </div>
        ))}
      </div>
    </div>
  );
});
