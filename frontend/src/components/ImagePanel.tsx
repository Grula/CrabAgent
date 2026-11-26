import { useEffect, useMemo, useState } from "react";
import { X } from "lucide-react";
import { cn } from "../lib/cn";
import { getImageUrl } from "../api/files";

interface Props {
  /** Path as the tree handed it over (absolute or workspace-relative). */
  path: string;
  /** File name, used as the alt text and the tab label. */
  name: string;
  /** True when `path` is absolute, so the server resolves it outside the workspace. */
  absolute?: boolean;
  onClose?: () => void;
  className?: string;
}

/**
 * An image open in the Documents tab. Read-only: the panel shows the image the
 * authenticated files API serves and opens the same URL full-size in a new tab.
 * Auth lives in `getImageUrl`, so the panel needs no session id.
 */
export function ImagePanel({ path, name, absolute = false, onClose, className }: Props) {
  const url = useMemo(() => getImageUrl(path, absolute), [path, absolute]);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  // A different image in the same panel starts over.
  useEffect(() => {
    setLoading(true);
    setFailed(false);
  }, [url]);

  return (
    <div className={cn("flex flex-col h-full min-h-0 bg-[var(--bg-primary)]", className)}>
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-[var(--border)] bg-[var(--bg-secondary)] shrink-0">
        <span className="text-xs truncate text-[var(--text-secondary)]" title={path}>{name}</span>
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            title="Close"
            aria-label="Close"
            className="ml-auto p-1 rounded hover:bg-[var(--bg-tertiary)] text-[var(--text-tertiary)]"
          >
            <X size={14} />
          </button>
        )}
      </div>
      <div className="relative flex-1 min-h-0 flex items-center justify-center p-3 bg-[var(--bg-secondary)]">
        {failed ? (
          <p className="text-sm text-[var(--text-tertiary)]">{"Could not load this image."}</p>
        ) : (
          <>
            <img
              src={url}
              alt={name}
              title={`${name} — click to open full size`}
              onLoad={() => setLoading(false)}
              onError={() => { setLoading(false); setFailed(true); }}
              onClick={() => window.open(url, "_blank", "noopener")}
              className={cn(
                "max-w-full max-h-full object-contain cursor-zoom-in",
                loading && "opacity-0",
              )}
            />
            {loading && (
              <p className="absolute text-xs text-[var(--text-tertiary)]">{"Loading image…"}</p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
