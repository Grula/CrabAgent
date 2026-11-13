import { useState, useRef, useEffect, useMemo } from "react";
import { ChevronDown, Check, Search } from "lucide-react";
import { cn } from "../lib/cn";
import FloatingMenu from "./ui/FloatingMenu";
import { toggleParticipant, type ParticipantOption } from "./meetingParticipants";

interface Props {
  value: string[];
  options: ParticipantOption[];
  onChange: (next: string[]) => void;
  className?: string;
}

export function ParticipantPicker({ value, options, onChange, className }: Props) {
  const [open, setOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const disabled = options.length === 0;

  const filtered = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return options;
    return options.filter(
      (option) => option.label.toLowerCase().includes(q) || option.value.toLowerCase().includes(q),
    );
  }, [options, searchQuery]);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const handleClick = (e: MouseEvent) => {
      const target = e.target as Node;
      if (containerRef.current?.contains(target) || menuRef.current?.contains(target)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [open]);

  useEffect(() => {
    if (!open) setSearchQuery("");
  }, [open]);

  const selected = value.map(
    (item) => options.find((option) => option.value === item) ?? { value: item, label: item, icon: "🤖" },
  );

  return (
    <div ref={containerRef} className={cn("relative flex-1 min-w-0", className)}>
      <div className="flex flex-wrap items-center gap-1 text-[11px] text-[var(--text-tertiary)]">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          disabled={disabled}
          aria-expanded={open}
          aria-label={"Participants"}
          title={"Participants"}
          className={cn(
            "flex items-center gap-1 bg-transparent border-0 outline-none transition-colors",
            "text-[var(--text-tertiary)]",
            disabled && "opacity-50 cursor-not-allowed",
          )}
        >
          <span>
            {disabled ? "No agents" : value.length === 0 ? "Add participants…" : ""}
          </span>
          <ChevronDown size={12} className={cn("transition-transform", open && "rotate-180")} />
        </button>
        {selected.map((option) => (
          <span
            key={option.value}
            className="order-first flex items-center gap-1 rounded bg-[var(--bg-tertiary)] border border-[var(--border)] px-1 py-0.5 text-[var(--text-secondary)]"
          >
            <span>{option.icon}</span>
            <span className="truncate max-w-[120px]">{option.label}</span>
            <button
              type="button"
              title={"Remove"}
              aria-label={"Remove"}
              onClick={() => onChange(toggleParticipant(value, option.value))}
              className="shrink-0 hover:text-[var(--danger)] transition-colors"
            >
              ×
            </button>
          </span>
        ))}
      </div>

      {open && (
        <FloatingMenu
          open
          anchorRef={containerRef}
          menuRef={menuRef}
          placement="bottom-start"
          className={cn(
            "min-w-[220px] max-w-[300px]",
            "rounded-lg border border-[var(--border)] bg-[var(--bg-secondary)]",
            "shadow-[var(--shadow-lg)] flex flex-col",
          )}
        >
          <div className="p-1.5 border-b border-[var(--border)]">
            <div className="flex items-center gap-1.5 px-2 h-7 rounded-md bg-[var(--bg-tertiary)] border border-[var(--border)] focus-within:border-[var(--brand)]">
              <Search size={12} className="shrink-0 text-[var(--text-tertiary)]" />
              <input
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder={"Search agents…"}
                className="flex-1 bg-transparent text-xs text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)] outline-none"
              />
            </div>
          </div>
          <div className="overflow-y-auto max-h-[260px] py-1">
            {filtered.length === 0 ? (
              <div className="px-3 py-3 text-xs text-[var(--text-tertiary)] text-center">
                {"No matching agents"}
              </div>
            ) : (
              filtered.map((option) => {
                const isSelected = value.includes(option.value);
                return (
                  <button
                    key={option.value}
                    type="button"
                    aria-selected={isSelected}
                    onClick={() => onChange(toggleParticipant(value, option.value))}
                    className={cn(
                      "w-full flex items-center gap-2 px-3 py-1.5 text-xs text-left transition-colors",
                      isSelected
                        ? "bg-[var(--brand-bg)] text-[var(--brand)]"
                        : "text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)]",
                    )}
                  >
                    <span>{option.icon}</span>
                    <span className="flex-1 truncate">{option.label}</span>
                    {isSelected && <Check size={13} className="shrink-0 text-[var(--brand)]" />}
                  </button>
                );
              })
            )}
          </div>
        </FloatingMenu>
      )}
    </div>
  );
}
