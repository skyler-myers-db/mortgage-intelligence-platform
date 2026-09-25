import { useEffectEvent, useLayoutEffect, useRef, type RefObject } from 'react';
import { dismissTopLayer } from '../lib/escapeStack';
import { pushModalLayer } from '../lib/modalLayers';
import { useFocusTrap } from './useFocusTrap';

/**
 * useModalDialog — the ONE way a surface becomes modal (audit 2026-09-21
 * `stack-05` dialog half, `a11y-07` step 2, `css-03` slice 2).
 *
 * Every modal surface renders a native `<dialog>` and hands it to this hook,
 * which opens it with `showModal()`: the platform top layer paints it above
 * every stacking context (no z-index tiers), makes everything outside it
 * inert (a virtual cursor and a pointer can no longer reach the page behind)
 * and draws a real `::backdrop`. The dialog carries no `role` and no
 * `aria-modal`: both are implicit.
 *
 * What stays in `useFocusTrap`: the Tab wrap, the shared Escape layer and the
 * initial focus with its retry frames. What this hook adds:
 *
 *   - Opening (a layout effect, so the dialog is modal in the frame it
 *     opens): the opener (`document.activeElement`) and its region are
 *     recorded BEFORE `showModal()` moves focus into the dialog; then the
 *     dialog is pushed on lib/modalLayers (the Toaster re-hosts into the
 *     topmost layer).
 *   - Closing (`open` goes false) and unmounting while open: `close()` FIRST,
 *     then the layer pops, THEN focus returns, because an opener behind an
 *     open modal is inert and refuses focus. Fallbacks when the opener is gone
 *     (a re-rendered row, a navigation): the recorded region's first
 *     `h2[tabindex]` / `h3[tabindex]`, else the region itself, then the page
 *     `h1[tabindex]`, then `#main-content`, all without scrolling.
 *     A close runs in the layout phase of the commit that sets `open` false,
 *     not in an effect cleanup: React DOM re-focuses the element that held
 *     focus before a commit once its mutation phase ends, which would put
 *     focus straight back inside a dialog that stays mounted for its exit.
 *   - Native `<dialog>` event handling, attached with `addEventListener` on
 *     the dialog (the element-level equivalent of the keyboard paths above):
 *       - `cancel` is always `preventDefault()`ed, so the browser never closes
 *         a dialog behind React's back. A keyboard Escape already went through
 *         the shared Escape stack; a `cancel` that did not (the Android back
 *         gesture, a CloseWatcher) runs the topmost layer once
 *         (`dismissTopLayer`), exactly as Escape would.
 *       - `close` while the dialog should be open is a browser-forced close:
 *         a dismissible dialog reports it (`onDismiss`); a blocking one
 *         (`dismissible: false`, the session dialog) shows itself again. The
 *         listener comes off before this hook's own `close()`, so its own
 *         closes are never reported.
 *       - Backdrop dismissal (`closedby` is not Baseline): a press that both
 *         starts and ends on the dialog element itself. `'outside'` also
 *         requires the point to be outside the dialog's box, for panel
 *         dialogs whose own padding is the dialog element; `'self'` is for a
 *         full-viewport dialog (the command palette) whose own area IS the
 *         backdrop. `false` never dismisses on a press.
 *
 * Where `showModal` is missing (old engines, some DOM test environments) the
 * dialog only gets its `open` attribute.
 */

export type ModalBackdrop = 'outside' | 'self' | false;

export interface UseModalDialogOptions<TInitial extends HTMLElement> {
  open: boolean;
  dialogRef: RefObject<HTMLDialogElement | null>;
  initialFocusRef?: RefObject<TInitial | null>;
  /** Escape, a backdrop press and a browser-forced close all come here. */
  onDismiss: () => void;
  /** False for a blocking dialog: a forced close re-opens it. Default true. */
  dismissible?: boolean;
  backdrop: ModalBackdrop;
}

/** Where focus goes back to when the opener is gone. */
const OPENER_REGION_SELECTOR = '.tbl-wrap, [role="region"], section';

function takesFocus(target: HTMLElement | null | undefined, preventScroll: boolean): boolean {
  if (!target?.isConnected) return false;
  target.focus({ preventScroll });
  return document.activeElement === target;
}

/**
 * Give focus back after a modal closed: the opener when it can still take
 * it, else the fallback chain (see the module note).
 */
export function restoreModalFocus(opener: HTMLElement | null, region: HTMLElement | null): void {
  if (takesFocus(opener, false)) return;
  if (region?.isConnected) {
    const heading = region.querySelector<HTMLElement>('h2[tabindex], h3[tabindex]');
    if (takesFocus(heading, true) || takesFocus(region, true)) return;
  }
  const main = document.getElementById('main-content');
  if (takesFocus(main?.querySelector<HTMLElement>('h1[tabindex]'), true)) return;
  takesFocus(main, true);
}

function showAsModal(dialog: HTMLDialogElement): void {
  if (dialog.open) return;
  if (typeof dialog.showModal === 'function') {
    try {
      dialog.showModal();
      return;
    } catch {
      // Not connected yet, or already open non-modally: fall through.
    }
  }
  dialog.setAttribute('open', '');
}

function closeDialog(dialog: HTMLDialogElement): void {
  if (!dialog.open) return;
  if (typeof dialog.close === 'function') dialog.close();
  else dialog.removeAttribute('open');
}

function pressIsOnBackdrop(dialog: HTMLDialogElement, event: MouseEvent, backdrop: ModalBackdrop): boolean {
  if (!backdrop || event.target !== dialog) return false;
  if (backdrop === 'self') return true;
  const box = dialog.getBoundingClientRect();
  return event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom;
}

/** The props a session reads when a native event fires: always the latest. */
interface ModalHandlers {
  dismiss: () => void;
  dismissible: boolean;
  backdrop: ModalBackdrop;
}

/**
 * One open period of the dialog: record the opener, showModal(), push the
 * layer and listen for the native events. Returns the close, which undoes
 * all of it in order (listeners off, close(), pop, then focus).
 */
function startModalSession(dialog: HTMLDialogElement, handlers: () => ModalHandlers): () => void {
  const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const region = opener?.closest<HTMLElement>(OPENER_REGION_SELECTOR) ?? null;
  showAsModal(dialog);
  const popLayer = pushModalLayer(dialog);

  let pressStartedOnBackdrop = false;
  const onBackdrop = (event: MouseEvent) => {
    const { dismissible, backdrop } = handlers();
    return dismissible && pressIsOnBackdrop(dialog, event, backdrop);
  };
  const listeners: Array<[string, (event: Event) => void]> = [
    ['cancel', (event) => {
      event.preventDefault();
      dismissTopLayer();
    }],
    // Only a close this session did not make reaches here: the listeners come
    // off before its own close(), whether `close` fires in the same task (DOM
    // test environments) or later (browsers).
    ['close', () => {
      if (dialog.open) return;
      const { dismissible, dismiss } = handlers();
      if (dismissible) dismiss();
      else showAsModal(dialog);
    }],
    ['pointerdown', (event) => {
      pressStartedOnBackdrop = onBackdrop(event as MouseEvent);
    }],
    ['click', (event) => {
      const started = pressStartedOnBackdrop;
      pressStartedOnBackdrop = false;
      if (started && onBackdrop(event as MouseEvent)) handlers().dismiss();
    }],
  ];
  for (const [type, listener] of listeners) dialog.addEventListener(type, listener);
  return () => {
    for (const [type, listener] of listeners) dialog.removeEventListener(type, listener);
    closeDialog(dialog);
    popLayer();
    restoreModalFocus(opener, region);
  };
}

export function useModalDialog<TInitial extends HTMLElement = HTMLElement>({
  open,
  dialogRef,
  initialFocusRef,
  onDismiss,
  dismissible = true,
  backdrop,
}: UseModalDialogOptions<TInitial>): void {
  const endSessionRef = useRef<(() => void) | null>(null);
  // The latest props, read when a native event fires (never a dependency,
  // so a caller's new inline onDismiss never closes and re-opens the dialog).
  const handlers = useEffectEvent((): ModalHandlers => ({ dismiss: onDismiss, dismissible, backdrop }));

  // Open and close in the layout phase (see the module note on focus).
  useLayoutEffect(() => {
    const dialog = dialogRef.current;
    if (open && dialog && !endSessionRef.current) {
      endSessionRef.current = startModalSession(dialog, () => handlers());
    } else if (!open && endSessionRef.current) {
      endSessionRef.current();
      endSessionRef.current = null;
    }
  }, [dialogRef, open]);

  // Unmounted while open: the cleanup runs while the dialog is still in the
  // document, so close() and the layer pop still apply.
  useLayoutEffect(() => () => {
    endSessionRef.current?.();
    endSessionRef.current = null;
  }, []);

  useFocusTrap({ open, containerRef: dialogRef, initialFocusRef, onClose: onDismiss, restoreFocus: false });
}
