import { useState, useRef, useCallback, useEffect, useLayoutEffect, type ReactNode } from "react";
import { ArrowUp, Square, Paperclip, Bot, Target } from "lucide-react";
import { cn } from "../lib/cn";

interface Props {
  sending: boolean;
  replaying: boolean;
  onSend: (text: string) => void;
  onAbort: () => void;
  onFileUpload: () => void;
  onFilePaste: (e: React.ClipboardEvent) => void;
  onDelegateOpen: () => void;
  showDelegate?: boolean;
  onGoalOpen?: () => void;
  hasGoal?: boolean;
  /** Pickers shown at the bottom right of the card (agent, model, effort) */
  toolbar?: ReactNode;
  /** Pending images and files, shown above the text box */
  attachments?: ReactNode;
  placeholder?: string;
  autoFocus?: boolean;
  /** Puts text into the box (e.g. a starter prompt); a new `key` replaces the text again */
  seed?: { text: string; key: number };
  /** Allow sending with an empty text box (e.g. only attachments) */
  canSendEmpty?: boolean;
}

const MAX_INPUT_HEIGHT = 240;

const iconButton = cn(
  "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg transition-colors",
  "text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] hover:text-[var(--text-primary)]",
  "disabled:pointer-events-none disabled:opacity-40",
);

export default function ChatInput({
  sending,
  replaying,
  onSend,
  onAbort,
  onFileUpload,
  onFilePaste,
  onDelegateOpen,
  showDelegate,
  onGoalOpen,
  hasGoal,
  toolbar,
  attachments,
  placeholder = "Reply to CrabAgent…",
  autoFocus,
  seed,
  canSendEmpty,
}: Props) {
  const [input, setInput] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const isComposingRef = useRef(false);

  // Grow with the text up to MAX_INPUT_HEIGHT, and shrink back after sending.
  // A hidden box reports scrollHeight 0; leave the CSS min-height in charge then.
  useLayoutEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    if (el.scrollHeight > 0) el.style.height = `${Math.min(el.scrollHeight, MAX_INPUT_HEIGHT)}px`;
    else el.style.height = "";
  }, [input]);

  useEffect(() => {
    if (!seed) return;
    setInput(seed.text);
    const el = inputRef.current;
    if (el) {
      el.focus();
      el.setSelectionRange(seed.text.length, seed.text.length);
    }
  }, [seed]);

  const handleSend = useCallback(() => {
    const text = input.trim();
    if (!text && !canSendEmpty) return;
    onSend(text);
    setInput("");
  }, [input, onSend, canSendEmpty]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key !== "Enter" || e.shiftKey) return;
      if (e.nativeEvent.isComposing || isComposingRef.current) return;
      e.preventDefault();
      handleSend();
    },
    [handleSend],
  );

  return (
    <div className="composer" onClick={(e) => { if (e.target === e.currentTarget) inputRef.current?.focus(); }}>
      {attachments}
      <textarea
        ref={inputRef}
        value={input}
        rows={1}
        onChange={(e) => setInput(e.target.value)}
        onCompositionStart={() => {
          isComposingRef.current = true;
        }}
        onCompositionEnd={() => {
          isComposingRef.current = false;
        }}
        onKeyDown={handleKeyDown}
        onPaste={onFilePaste}
        placeholder={placeholder}
        aria-label="Message"
        disabled={replaying}
        autoFocus={autoFocus}
        className="composer__input"
      />
      {/* Phones: pickers get their own row above the buttons; wider screens: one row */}
      <div className="flex min-w-0 flex-wrap items-center gap-1 px-2 pb-2 sm:flex-nowrap">
        <button
          type="button"
          onClick={onFileUpload}
          disabled={sending || replaying}
          title={"Attach file"}
          aria-label={"Attach file"}
          className={iconButton}
        >
          <Paperclip size={16} />
        </button>
        {onGoalOpen && (
          <button
            type="button"
            onClick={onGoalOpen}
            disabled={sending || replaying}
            title={hasGoal ? "Manage Goals" : "Create Goal"}
            aria-label={hasGoal ? "Manage Goals" : "Create Goal"}
            className={cn(iconButton, hasGoal && "text-[var(--brand)] hover:text-[var(--brand)]")}
          >
            <Target size={16} />
          </button>
        )}
        {showDelegate && (
          <button
            type="button"
            onClick={onDelegateOpen}
            disabled={sending || replaying}
            title={"Delegate to agent team"}
            aria-label={"Delegate to agent team"}
            className={iconButton}
          >
            <Bot size={16} />
          </button>
        )}
        {toolbar && (
          <div className="order-first flex w-full min-w-0 items-center gap-0.5 overflow-x-auto scrollbar-none sm:order-none sm:ml-auto sm:w-auto sm:justify-end">
            {toolbar}
          </div>
        )}
        <span className={cn("ml-auto", !!toolbar && "sm:hidden")} />
        {sending && (
          <button
            type="button"
            onClick={onAbort}
            title={"Stop"}
            aria-label={"Stop"}
            className={cn(
              "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg transition-colors",
              "border border-[var(--border-strong)] text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)]",
            )}
          >
            <Square size={12} fill="currentColor" />
          </button>
        )}
        <button
          type="button"
          onClick={handleSend}
          disabled={replaying || (!input.trim() && !canSendEmpty)}
          title={"Send"}
          aria-label={"Send"}
          className={cn(
            "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg transition-colors",
            "bg-[var(--brand)] text-[var(--text-on-brand)] hover:bg-[var(--brand-hover)] active:bg-[var(--brand-active)]",
            "disabled:cursor-not-allowed disabled:bg-[var(--brand)] disabled:opacity-40",
          )}
        >
          <ArrowUp size={16} strokeWidth={2.25} />
        </button>
      </div>
    </div>
  );
}
