import { Modal } from "./ui";

interface Props {
  /** Name shown in the title; the dialog never edits it. */
  fileName: string;
  description: string;
  /** Body line under the description. */
  body: string;
  /** test id on the confirm button, so a test can target one call site. */
  confirmTestId: string;
  onCancel: () => void;
  onDiscard: () => void;
}

/**
 * The one "unsaved edits are about to be thrown away" confirmation: closing a
 * dirty tab and switching the Documents tab away from a dirty code panel both
 * use it, so the wording and the Cancel/Discard pairing stay identical.
 *
 * Render it only while a confirmation is pending; every way of dismissing it
 * (Cancel, Escape, backdrop, close) counts as Cancel.
 */
export function DiscardChangesDialog({ fileName, description, body, confirmTestId, onCancel, onDiscard }: Props) {
  return (
    <Modal
      open
      onOpenChange={(open) => { if (!open) onCancel(); }}
      title={`Discard changes to ${fileName}?`}
      description={description}
      footer={(
        <>
          <button
            onClick={onCancel}
            className="px-2.5 py-1 rounded text-[11px] border border-[var(--border)] text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] transition-colors"
          >
            Cancel
          </button>
          <button
            data-testid={confirmTestId}
            onClick={onDiscard}
            className="px-2.5 py-1 rounded text-[11px] bg-[var(--danger)] text-white hover:bg-[var(--danger-hover)] transition-colors"
          >
            Discard
          </button>
        </>
      )}
    >
      <p className="text-xs text-[var(--text-secondary)]">{body}</p>
    </Modal>
  );
}
