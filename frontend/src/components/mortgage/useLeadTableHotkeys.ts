/**
 * useLeadTableHotkeys — binds the ranked-borrower table's window-level
 * keydown listener exactly once while always invoking the freshest handler,
 * and only hands it keystrokes that are IN SCOPE for the table.
 * Extracted from LeadTable.tsx (file-size gate, plan item 2); the binding
 * and scope semantics are pinned by LeadTable.hotkeys.test.
 */

import { useEffect, useRef, type RefObject } from 'react';
import { isEditableTarget } from './LeadTable.logic';

/**
 * Anything that, while open, owns the keyboard: modal and non-modal dialogs
 * (evidence drawer, borrower proof drawer, command palette, the floating
 * Genie panel), listboxes (filter menus, topbar search results) and menus.
 * The always-mounted drawers and the Genie panel toggle `aria-hidden`, so a
 * closed one never matches.
 */
const OPEN_OVERLAY_SELECTOR = [
  '[role="dialog"]',
  '[role="alertdialog"]',
  '[role="listbox"]',
  '[role="menu"]',
  'dialog[open]',
].join(',');

function hasOpenOverlay(doc: Document): boolean {
  for (const el of doc.querySelectorAll(OPEN_OVERLAY_SELECTOR)) {
    if (el.closest('[aria-hidden="true"]') === null) return true;
  }
  return false;
}

/**
 * Scope gate for the single-key row shortcuts (A approve / R reject /
 * Shift+A bulk approve). Audit tables-v2 / a11y-09 (2026-09-21): the listener
 * is window-level and used to exempt only editable targets, so pressing A with
 * a row expanded while focus sat on a filter button, inside the evidence
 * drawer or on Genie chrome approved a borrower and wrote an audit row. WCAG
 * 2.1.4 (Level A) requires a single-character shortcut to be switchable off,
 * remappable, or active only on focus; this is the focus-scoped form.
 *
 * A keystroke is in scope only when ALL hold:
 *   1. the event target AND document.activeElement are inside `scope` (the
 *      `.tbl-wrap` region) — an unfocused page (`body`) is out of scope;
 *   2. no dialog, drawer, listbox, menu or command palette is open;
 *   3. neither the target nor the active element is an input, textarea,
 *      select or contenteditable.
 *
 * Clicking a row still arms the shortcuts: the region is `tabIndex=0`, so a
 * click on non-interactive row content focuses the region itself.
 */
export function isLeadTableHotkeyInScope(
  event: KeyboardEvent,
  scope: HTMLElement | null,
  doc: Document = document,
): boolean {
  if (!scope) return false;
  const target = event.target instanceof Element ? event.target : null;
  const active = doc.activeElement;
  if (!target || !scope.contains(target)) return false;
  if (!active || !scope.contains(active)) return false;
  if (isEditableTarget(target) || isEditableTarget(active)) return false;
  return !hasOpenOverlay(doc);
}

/**
 * The keydown logic closes over many per-render values (the expanded row,
 * approvals, the lead lookup, the approve/bulk-approve closures). The
 * original effect had NO dep array, so it removed+re-added the window
 * listener on EVERY render (re-audit #4 nit). A dep array can't fix that
 * cleanly — those closures have unstable identity, so the effect would still
 * re-bind every render (or go stale if deps are omitted). The latest-handler
 * ref binds the listener exactly once and always calls the freshest logic.
 *
 * @param handler the current render's keydown logic. Stored in a ref during
 *   render (not in an effect) so the very first keypress after a render
 *   already sees the new closure. Only called for in-scope keystrokes.
 * @param scopeRef the table scroll region the shortcuts are scoped to.
 */
export function useLeadTableHotkeys(
  handler: (e: KeyboardEvent) => void,
  scopeRef: RefObject<HTMLElement | null>,
): void {
  'use no memo';

  const keyHandlerRef = useRef<(e: KeyboardEvent) => void>(() => {});
  // The write IS the pattern: the listener is bound once, so the ref has to
  // carry the current render's closure. Assigning it in an effect instead
  // would leave the listener one commit stale, which is the staleness this
  // hook exists to avoid. Unchanged from the pre-split component — where the
  // same write sat inside LeadTable and was masked from this rule by the
  // useVirtualizer (`react-hooks/incompatible-library`) compiler bailout.
  // eslint-disable-next-line react-hooks/refs
  keyHandlerRef.current = handler;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!isLeadTableHotkeyInScope(e, scopeRef.current)) return;
      keyHandlerRef.current(e);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [scopeRef]);
}
