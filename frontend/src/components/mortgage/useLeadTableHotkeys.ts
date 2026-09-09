/**
 * useLeadTableHotkeys — binds the ranked-borrower table's window-level
 * keydown listener exactly once while always invoking the freshest handler.
 * Extracted from LeadTable.tsx (file-size gate, plan item 2); the binding
 * semantics are pinned by LeadTable.hotkeys.test.
 */

import { useEffect, useRef } from 'react';

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
 *   already sees the new closure.
 */
export function useLeadTableHotkeys(handler: (e: KeyboardEvent) => void): void {
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
    const onKey = (e: KeyboardEvent) => keyHandlerRef.current(e);
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
}
