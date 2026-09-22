import { useEffect, useRef, type RefObject } from 'react';
import { pushEscapeLayer } from '../../lib/escapeStack';

/** The topbar's Genie toggle (`Topbar.tsx`): the launcher that is rendered at
 *  every width. The panel's own `.genie__fab` is `display: none` above 720px
 *  (06-genie-chat.css), so focusing it on desktop silently drops focus. */
const TOPBAR_TOGGLE_SELECTOR = 'button[aria-label="Toggle Genie chat"]';

interface UseGeniePanelDismissalOptions {
  open: boolean;
  panelRef: RefObject<HTMLElement | null>;
  inputRef: RefObject<HTMLElement | null>;
  /** The panel's own launcher: the LAST focus fallback when whatever opened
   *  the panel is no longer in the document and there is no topbar toggle. */
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
    // or a launcher). When the opener has left the document — a command
    // palette item, the shell's pre-mount launcher — prefer the topbar
    // toggle, which exists at every width; the panel's own FAB is the last
    // resort because it is hidden on desktop.
    const opener = openerRef.current;
    if (!opener) return undefined;
    openerRef.current = null;
    const target = document.contains(opener)
      ? opener
      : (document.querySelector<HTMLElement>(TOPBAR_TOGGLE_SELECTOR) ?? fabRef.current);
    target?.focus();
    return undefined;
  }, [fabRef, inputRef, open, panelRef]);
}
