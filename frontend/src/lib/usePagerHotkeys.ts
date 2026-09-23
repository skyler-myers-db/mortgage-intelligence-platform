import { useEffect, useRef, type RefObject } from 'react';

/**
 * usePagerHotkeys — J (next) / K (previous) through the Lead Queue from a
 * Borrower 360 dossier (audit 2026-09-21 `shell-04`).
 *
 * Scoped exactly like the queue's row shortcuts (components/mortgage/
 * useLeadTableHotkeys.ts, audit a11y-09). A keystroke counts only when ALL
 * hold:
 *   1. the event target AND the focused element are inside the dossier's
 *      region: the `<main>` landmark around the pager (the queue's region is
 *      its `.tbl-wrap`). Focus in the topbar, the route nav, the Console or on
 *      an unfocused page (`body`) is out of scope, so these single-character
 *      keys are active only while the dossier has focus (WCAG 2.1.4, the same
 *      focus-scoped form the queue uses);
 *   2. neither the target nor the focused element is an input, textarea,
 *      select or contenteditable;
 *   3. no dialog, drawer, listbox, menu or command palette is open.
 * A modifier chord, an auto-repeat (holding J would open, and audit, a
 * dossier per repeat) and an IME composition are ignored too. A route change
 * focuses the page heading (hooks/useRouteAnnouncer), so J / K keep working
 * dossier after dossier; a dossier opened from a pasted URL needs one click
 * in the page first, as the queue does.
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

/** True when a J / K keystroke may page the dossier whose region is `region`. */
export function isPagerHotkeyInScope(
  event: KeyboardEvent,
  region: Element | null,
  doc: Document = document,
): boolean {
  if (!region) return false;
  if (event.defaultPrevented || event.repeat || event.isComposing) return false;
  if (event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return false;
  const target = event.target instanceof Element ? event.target : null;
  const active = doc.activeElement;
  if (!target || !region.contains(target)) return false;
  if (!active || !region.contains(active)) return false;
  if (isEditable(target) || isEditable(active)) return false;
  return !hasOpenOverlay(doc);
}

export interface PagerHotkeyHandlers {
  /** Open the previous borrower (K); null at the top of the queue. */
  previous: (() => void) | null;
  /** Open the next borrower (J); null at the end of the queue. */
  next: (() => void) | null;
}

/**
 * @param anchorRef an element of the dossier (the pager itself); the keys are
 *   scoped to the `<main>` landmark that contains it. Nothing mounted, no keys.
 */
export function usePagerHotkeys(handlers: PagerHotkeyHandlers, anchorRef: RefObject<HTMLElement | null>): void {
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
      const region = anchorRef.current?.closest('main') ?? null;
      if (!isPagerHotkeyInScope(event, region)) return;
      const action = key === 'j' ? handlersRef.current.next : handlersRef.current.previous;
      if (!action) return;
      event.preventDefault();
      action();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [anchorRef]);
}
