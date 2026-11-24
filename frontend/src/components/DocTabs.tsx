import { File, X } from "lucide-react";
import { cn } from "../lib/cn";

/** One chip in the Documents tab strip: the code editor or an open document. */
export interface DocTab {
  /** Path handed back to the parent on select/close. */
  path: string;
  /** Basename shown on the chip. */
  name: string;
  /** `code` chips have no close button: CodePanel owns their tabs (and asks when dirty). */
  kind: "code" | "doc";
  active: boolean;
}

interface Props {
  tabs: DocTab[];
  onSelect: (path: string) => void;
  onClose: (path: string) => void;
}

/**
 * The Documents tab strip: one chip per open document plus the code editor,
 * so switching between them never has to unmount a panel. Purely presentational
 * — the parent owns the open list and what is active.
 */
export function DocTabs({ tabs, onSelect, onClose }: Props) {
  return (
    <div
      data-testid="doc-tabs"
      className="flex items-center overflow-x-auto border-b border-[var(--border)] bg-[var(--bg-secondary)] shrink-0"
    >
      {tabs.map((tab) => (
        <div
          key={`${tab.kind}:${tab.path}`}
          data-testid="doc-tab"
          data-kind={tab.kind}
          data-active={tab.active ? "true" : "false"}
          title={tab.path}
          onClick={() => onSelect(tab.path)}
          className={cn(
            "flex items-center gap-1.5 px-3 py-1.5 text-[11px] border-r border-[var(--border)] cursor-pointer shrink-0 transition-colors",
            tab.active
              ? "bg-[var(--bg-primary)] text-[var(--text-primary)] border-b-2 border-b-[var(--brand)]"
              : "text-[var(--text-tertiary)] hover:text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)]",
          )}
        >
          <File size={12} />
          <span className="truncate max-w-[120px]">{tab.name}</span>
          {tab.kind === "doc" && (
            <button
              type="button"
              aria-label={`Close ${tab.name}`}
              onClick={(e) => { e.stopPropagation(); onClose(tab.path); }}
              className="p-0.5 rounded hover:bg-[var(--bg-tertiary)] hover:text-[var(--text-primary)] transition-colors"
            >
              <X size={10} />
            </button>
          )}
        </div>
      ))}
    </div>
  );
}
