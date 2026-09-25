import { useId, useRef } from 'react';
import { useModalDialog } from '../../hooks/useModalDialog';
import { Icon } from '../Icon';

/**
 * "Leave without saving?" — the confirm the unsaved-changes guard shows when
 * an in-app navigation would discard a page's unsaved work (2026-09-21 audit
 * states-05).
 *
 * A native modal `<dialog>` (top layer, inert page behind it, a real
 * `::backdrop`) opened through `useModalDialog`, which adds the shared
 * Escape stack and Tab wrap-around. Focus opens on Stay, the safe answer;
 * Escape and the dialog's own cancel also mean Stay. Closing hands focus
 * back to the control that started the navigation (WCAG 2.4.3): the hook
 * reads it before showModal() and restores it after close().
 *
 * The content is the prototype's `.approval` banner (design_files/index.html
 * `.approval`: icon, title, sub, actions) inside a `.unsaved-dialog` shell
 * (32-feedback.css). The prototype has no confirm dialog; this is a declared
 * extension built from the `.approval` tokens. Buttons are plain `.btn`
 * markup so the shell does not pull the lazy Primitives chunk.
 */

export const UNSAVED_DIALOG_TITLE = 'Leave without saving?';

interface UnsavedChangesDialogProps {
  message: string;
  onStay: () => void;
  onLeave: () => void;
}

export function UnsavedChangesDialog({ message, onStay, onLeave }: UnsavedChangesDialogProps) {
  const dialogRef = useRef<HTMLDialogElement | null>(null);
  const stayRef = useRef<HTMLButtonElement | null>(null);
  const titleId = useId();
  const messageId = useId();

  useModalDialog({ open: true, dialogRef, initialFocusRef: stayRef, onDismiss: onStay, backdrop: false });

  return (
    <dialog
      ref={dialogRef}
      className="surface surface--elev unsaved-dialog"
      aria-labelledby={titleId}
      aria-describedby={messageId}
    >
      <div className="approval">
        <div className="approval__ico" aria-hidden="true">
          <Icon name="info" size={16} />
        </div>
        <div className="approval__body">
          <div className="approval__title" id={titleId}>{UNSAVED_DIALOG_TITLE}</div>
          <div className="approval__sub" id={messageId}>{message}</div>
        </div>
        <div className="approval__actions">
          <button ref={stayRef} type="button" className="btn btn--primary btn--sm" onClick={onStay}>
            Stay
          </button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={onLeave}>
            Leave
          </button>
        </div>
      </div>
    </dialog>
  );
}
