/**
 * useLeadTableHotkeys — registers the ranked-borrower table's shortcuts in
 * the shared keymap (lib/keymap.ts, scope `lead-queue`) and keeps them
 * focus-scoped. Before wave 1c this hook bound its own `window` listener;
 * the registry now owns dispatch, the single-key switch and the `?` sheet,
 * and this hook owns only the table's scope rule.
 */

import { useEffect, useRef, type RefObject } from 'react';
import { hasOpenOverlay, registerKeyBinding } from '../../lib/keymap';
import { leadTableHotkeys, type LeadTableKeymapActions } from './LeadTable.keymap';
import { isEditableTarget } from './LeadTable.logic';

/**
 * Scope gate for the table's single-key shortcuts (J / K / arrows / Enter /
 * X / A / R / Shift+A). Audit tables-v2 / a11y-09 (2026-09-21): the listener
 * used to be window-level with only an editable-target exemption, so A with a
 * row expanded approved a borrower from a filter button, the evidence drawer
 * or Genie chrome. WCAG 2.1.4 (Level A) requires a single-character shortcut
 * to be switchable off, remappable, or active only on focus; this is the
 * focus-scoped form (the Console switch is the off form).
 *
 * A keystroke is in scope only when ALL hold:
 *   1. the event target AND document.activeElement are inside `scope` (the
 *      `.tbl-wrap` region) — an unfocused page (`body`) is out of scope;
 *   2. no dialog, drawer, listbox, menu or command palette is open (the
 *      approve-review dialog included);
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

export interface LeadTableHotkey {
  id: string;
  keys: readonly string[];
  /** What the `?` sheet says the key does. */
  description: string;
  /**
   * Return `false` to decline (the key keeps its native behaviour).
   * `scope` is the table region the key fired in.
   */
  run: (event: KeyboardEvent, scope: HTMLElement | null) => boolean | void;
}

/**
 * The handlers close over per-render values (the cursor, approvals, the
 * review), so they are read through a latest-value ref: the bindings are
 * registered once per distinct key set and always call the freshest logic.
 * The key set changes when the approver gate does (A / R / Shift+A are
 * registered only for an approver), and that re-registers.
 *
 * @param actions this render's row actions (LeadTable.keymap builds the
 *   keys from them). Stored in a ref during render (not in an effect) so
 *   the first keypress after a render already sees them.
 * @param scopeRef the table scroll region the shortcuts are scoped to.
 */
export function useLeadTableHotkeys(
  actions: LeadTableKeymapActions,
  scopeRef: RefObject<HTMLElement | null>,
): void {
  'use no memo';

  const hotkeys = leadTableHotkeys(actions);
  const latestRef = useRef<readonly LeadTableHotkey[]>(hotkeys);
  // The write IS the pattern: see the doc comment above.
  // eslint-disable-next-line react-hooks/refs
  latestRef.current = hotkeys;
  const signature = hotkeys.map((hotkey) => `${hotkey.id}=${hotkey.keys.join('+')}`).join('|');
  useEffect(() => {
    const offs = latestRef.current.map((hotkey) => registerKeyBinding({
      id: `lead-table-${hotkey.id}`,
      scope: 'lead-queue',
      keys: hotkey.keys,
      description: hotkey.description,
      when: (event) => isLeadTableHotkeyInScope(event, scopeRef.current),
      run: (event) => {
        const current = latestRef.current.find((candidate) => candidate.id === hotkey.id);
        return current ? current.run(event, scopeRef.current) : false;
      },
    }));
    return () => offs.forEach((off) => off());
  }, [signature, scopeRef]);
}
