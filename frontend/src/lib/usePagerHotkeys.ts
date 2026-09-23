import { useEffect, useRef } from 'react';

/**
 * usePagerHotkeys — J (next) / K (previous) through the Lead Queue from a
 * Borrower 360 dossier (audit 2026-09-21 `shell-04`).
 *
 * Scoped like the queue's row shortcuts (components/mortgage/
 * useLeadTableHotkeys.ts): a keystroke is ignored when the target or the
 * focused element is an input, textarea, select or contenteditable, and while
 * any dialog, drawer, listbox, menu or command palette is open. The dossier is
 * a whole page rather than a focusable region, so there is no region check;
 * a modifier chord, an auto-repeat (holding J would open, and audit, a
 * dossier per repeat) and an IME composition are ignored too.
 *
 * Deliberately a single small hook, not a second registry: the queue-keyboard
 * lane's keymap registry can absorb it at integration.
 */

const OPEN_OVERLAY_SELECTOR = [
  '[role="dialog"]',
  '[role="alertdialog"]',
  '[role="listbox"]',
  '[role="menu"]',
  'dialog[open]',
].join(',');

function isEditable(element: Element | null): boolean {
  if (!(element instanceof HTMLElement)) return false;
  const tag = element.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || element.isContentEditable;
}

function hasOpenOverlay(doc: Document): boolean {
  for (const element of doc.querySelectorAll(OPEN_OVERLAY_SELECTOR)) {
    if (element.closest('[aria-hidden="true"]') === null) return true;
  }
  return false;
}

/** True when a J / K keystroke may page the dossier. */
export function isPagerHotkeyInScope(event: KeyboardEvent, doc: Document = document): boolean {
  if (event.defaultPrevented || event.repeat || event.isComposing) return false;
  if (event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return false;
  const target = event.target instanceof Element ? event.target : null;
  if (isEditable(target) || isEditable(doc.activeElement)) return false;
  return !hasOpenOverlay(doc);
}

export interface PagerHotkeyHandlers {
  /** Open the previous borrower (K); null at the top of the queue. */
  previous: (() => void) | null;
  /** Open the next borrower (J); null at the end of the queue. */
  next: (() => void) | null;
}

export function usePagerHotkeys(handlers: PagerHotkeyHandlers): void {
  // Latest-handler ref: the listener binds once and always calls the freshest
  // closures (same pattern and reason as useLeadTableHotkeys).
  const handlersRef = useRef(handlers);
  useEffect(() => {
    handlersRef.current = handlers;
  });
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const key = event.key.toLowerCase();
      if (key !== 'j' && key !== 'k') return;
      if (!isPagerHotkeyInScope(event)) return;
      const action = key === 'j' ? handlersRef.current.next : handlersRef.current.previous;
      if (!action) return;
      event.preventDefault();
      action();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);
}
