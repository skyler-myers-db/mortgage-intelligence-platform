import { useEffect, useId, useRef, type SyntheticEvent } from 'react';
import { useFocusTrap } from '../../hooks/useFocusTrap';
import { Icon } from '../Icon';

/**
 * "Leave without saving?" — the confirm the unsaved-changes guard shows when
 * an in-app navigation would discard a page's unsaved work (2026-09-21 audit
 * states-05).
 *
 * A native modal `<dialog>` (top layer, inert page behind it, a real
 * `::backdrop`) with `useFocusTrap` on top for the shared Escape stack, Tab
 * wrap-around and focus return to the control that started the navigation.
 * Focus opens on Stay, the safe answer; Escape and the dialog's own cancel
 * also mean Stay.
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

  // Declared before the focus trap so the dialog is modal (and focusable)
  // before the trap moves focus to Stay.
  useEffect(() => {
    const dialog = dialogRef.current;
    if (dialog && !dialog.open) dialog.showModal();
    return () => {
      if (dialog?.open) dialog.close();
    };
  }, []);

  useFocusTrap({ open: true, containerRef: dialogRef, initialFocusRef: stayRef, onClose: onStay });

  const onCancel = (event: SyntheticEvent<HTMLDialogElement>) => {
    event.preventDefault();
    onStay();
  };

  return (
    <dialog
      ref={dialogRef}
      className="surface surface--elev unsaved-dialog"
      aria-labelledby={titleId}
      aria-describedby={messageId}
      onCancel={onCancel}
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
