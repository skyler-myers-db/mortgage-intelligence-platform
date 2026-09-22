import { useEffect, useRef, type RefObject } from 'react';
import { pushEscapeLayer } from '../../lib/escapeStack';

interface UseGeniePanelDismissalOptions {
  open: boolean;
  panelRef: RefObject<HTMLElement | null>;
  inputRef: RefObject<HTMLElement | null>;
  /** The panel's own launcher: the focus fallback when whatever opened the
   *  panel is no longer in the document. */
  fabRef: RefObject<HTMLElement | null>;
  onClose: () => void;
}

/**
 * Open/close focus handling and Escape-to-close for the floating Genie panel
 * (R5-12 dialog a11y, re-scoped by audit 2026-09-21 `runtime-v2`).
 *
 * The panel is a NON-modal dialog: no focus trap, the page behind stays
 * interactive. Its old `window` Escape listener therefore fired for every
 * Escape anywhere in the app — dismissing the evidence drawer, the command
 * palette or a filter menu also closed Genie. Escape now goes through the
 * shared topmost-layer stack, and the panel additionally DECLINES the key
 * unless focus is inside it: Escape closes Genie only when the user is
 * working in Genie and no other layer is open above it.
 */
export function useGeniePanelDismissal({
  open,
  panelRef,
  inputRef,
  fabRef,
  onClose,
}: UseGeniePanelDismissalOptions): void {
  const openerRef = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (open) {
      openerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      queueMicrotask(() => inputRef.current?.focus());
      return pushEscapeLayer(() => {
        const panel = panelRef.current;
        const active = document.activeElement;
        if (!panel || !active || !panel.contains(active)) return false;
        onCloseRef.current();
        return undefined;
      });
    }
    // On close, return focus to whatever opened the panel (the topbar toggle
    // or a launcher). The shell's pre-mount launcher is replaced by the
    // panel's own once the panel has mounted, so fall back to that one.
    const opener = openerRef.current;
    if (!opener) return undefined;
    openerRef.current = null;
    const target = document.contains(opener) ? opener : fabRef.current;
    target?.focus();
    return undefined;
  }, [fabRef, inputRef, open, panelRef]);
}
