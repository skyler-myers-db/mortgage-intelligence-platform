import { useEffect, useEffectEvent, type RefObject } from 'react';
import { hasOpenOverlay, isEditableElement, registerKeyBinding } from './keymap';

/**
 * usePagerHotkeys — J (next) / K (previous) through the Lead Queue from a
 * Borrower 360 dossier or an offer (audit 2026-09-21 `shell-04`), on the
 * shared keymap (audit wow-power-4): the two keys are bindings in their own
 * `queue-pager` scope, so the `?` sheet lists them and the Console's
 * single-key switch turns them off like every other single-character key.
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
 *   2. neither the target nor the focused element is a field where typing
 *      wins (the keymap's own editable test, which the dispatcher applies too);
 *   3. no dialog, drawer, listbox, menu or command palette is open.
 * A modifier chord, an auto-repeat (holding J would open, and audit, a
 * dossier per repeat) and an IME composition are ignored too. A route change
 * focuses the page heading (hooks/useRouteAnnouncer), so J / K keep working
 * dossier after dossier; a dossier opened from a pasted URL needs one click
 * in the page first, as the queue does.
 *
 * Each key is registered only while its handler exists (no previous borrower
 * at the top of the queue, no next at its end, and neither while a form or a
 * write owns the page), so the sheet lists exactly what works.
 */

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
  if (isEditableElement(target) || isEditableElement(active)) return false;
  return !hasOpenOverlay(doc);
}

export interface PagerHotkeyHandlers {
  /** Open the previous borrower (K); null at the top of the queue. */
  previous: (() => void) | null;
  /** Open the next borrower (J); null at the end of the queue. */
  next: (() => void) | null;
}

export const QUEUE_PAGER_NEXT_BINDING = 'queue-pager-next';
export const QUEUE_PAGER_PREVIOUS_BINDING = 'queue-pager-previous';

/**
 * @param anchorRef an element of the dossier (the pager itself); the keys are
 *   scoped to the `<main>` landmark that contains it. Nothing mounted, no keys.
 */
export function usePagerHotkeys(handlers: PagerHotkeyHandlers, anchorRef: RefObject<HTMLElement | null>): void {
  const hasNext = handlers.next !== null;
  const hasPrevious = handlers.previous !== null;
  // The registrations live as long as a handler exists; each keystroke reads
  // the freshest closures (an Effect Event, never a render-time ref write).
  const inScope = useEffectEvent((event: KeyboardEvent) => (
    !event.repeat && isPagerHotkeyInScope(event, anchorRef.current?.closest('main') ?? null)
  ));
  const runNext = useEffectEvent(() => {
    if (!handlers.next) return false;
    handlers.next();
    return true;
  });
  const runPrevious = useEffectEvent(() => {
    if (!handlers.previous) return false;
    handlers.previous();
    return true;
  });

  useEffect(() => {
    if (!hasNext) return undefined;
    return registerKeyBinding({
      id: QUEUE_PAGER_NEXT_BINDING,
      scope: 'queue-pager',
      keys: ['j'],
      description: 'Next borrower in the queue',
      when: (event) => inScope(event),
      run: () => runNext(),
    });
  }, [hasNext]);

  useEffect(() => {
    if (!hasPrevious) return undefined;
    return registerKeyBinding({
      id: QUEUE_PAGER_PREVIOUS_BINDING,
      scope: 'queue-pager',
      keys: ['k'],
      description: 'Previous borrower in the queue',
      when: (event) => inScope(event),
      run: () => runPrevious(),
    });
  }, [hasPrevious]);
}
