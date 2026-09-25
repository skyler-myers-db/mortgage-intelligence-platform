/**
 * useLeadTableScroll — the Lead Queue table scroller keeps its place per
 * history entry (audit runtime-08, the table half of shell-03).
 *
 * `.main` already resets on PUSH and restores on POP (useMainScroll); the
 * ranked table scrolls inside its own `.tbl-wrap`, which nothing restored:
 * open a dossier from row 140, press Back, and the table was at the top.
 *
 * Opt-in (`enabled`, the Lead Queue's `restoreScroll`): Segment Intelligence
 * embeds the same table and keeps today's behaviour.
 *
 * Rules, keyed on the history entry like `.main` (hooks/scrollOffsetStore):
 *   - POP restores the offset saved for that entry. A virtualized table
 *     (> 120 rows) is created at that offset (`useLeadTableInitialOffset`
 *     feeds the virtualizer's `initialOffset`, so its first window is the
 *     saved one), then `scrollToOffset` runs once `getTotalSize` and the
 *     rendered rows reach it; a short table sets `scrollTop` once its rows
 *     are tall enough. Both wait at most MAIN_SCROLL_DEADLINE_MS.
 *   - REPLACE (expand / collapse mints a new entry key) carries the current
 *     offset to the new key, so a later Back still restores it.
 *   - PUSH (a sort, a filter, a preset) starts at the top.
 *   - Reader intent (wheel, touch, pointer, any key incl. J / K) cancels a
 *     restore that is still waiting; the cursor's scrollToIndex then wins.
 *
 * Stored values are numbers only, keyed by the router's random entry key (or
 * a fingerprint of the first-load URL): never a URL, never a borrower id.
 * It never touches `.main`.
 */
import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react';
import { useLocation, useNavigationType } from 'react-router';
import { retryUntil } from '../../hooks/retryUntil';
import {
  readOffsets,
  rememberOffsetCapped,
  scrollStorageKey,
  writeOffsets,
} from '../../hooks/scrollOffsetStore';
import { MAIN_SCROLL_DEADLINE_MS } from '../../hooks/useMainScroll';

export const LEAD_TABLE_SCROLL_STORAGE_KEY = 'mip.leadTableScroll.v1';
const MAX_ENTRIES = 50;
const USER_INTENT_EVENTS = ['wheel', 'touchstart', 'pointerdown', 'keydown'] as const;

/** The two virtualizer methods a restore needs (TanStack Virtual). */
export interface LeadTableVirtualScroll {
  scrollToOffset: (offset: number) => void;
  getTotalSize: () => number;
}

export interface UseLeadTableScrollInput {
  enabled: boolean;
  tableWrapRef: RefObject<HTMLDivElement | null>;
  /** The virtualizer while the table is virtualized, else null. */
  virtualizer: LeadTableVirtualScroll | null;
}

function readTableOffsets(): Map<string, number> {
  return readOffsets(LEAD_TABLE_SCROLL_STORAGE_KEY, MAX_ENTRIES);
}

/** The table's offsets, read from storage the first time a mount needs them. */
function loadOffsets(ref: { current: Map<string, number> | null }): Map<string, number> {
  if (ref.current === null) ref.current = readTableOffsets();
  return ref.current;
}

/**
 * The offset a POP restores for the current entry, read once at mount for the
 * virtualizer's `initialOffset`; 0 otherwise.
 */
export function useLeadTableInitialOffset(enabled: boolean): number {
  const location = useLocation();
  const navigationType = useNavigationType();
  const [initialOffset] = useState(() => (
    enabled && navigationType === 'POP'
      ? readTableOffsets().get(scrollStorageKey(location)) ?? 0
      : 0
  ));
  return initialOffset;
}

export function useLeadTableScroll({ enabled, tableWrapRef, virtualizer }: UseLeadTableScrollInput): void {
  const location = useLocation();
  const navigationType = useNavigationType();
  const storageKey = scrollStorageKey(location);

  const offsetsRef = useRef<Map<string, number> | null>(null);
  const activeKeyRef = useRef<string | null>(null);
  /** True while a restore waits for rows: our own scrolls are not the reader's. */
  const restoringRef = useRef(false);
  const cancelPendingRef = useRef<(() => void) | null>(null);
  const virtualizerRef = useRef<LeadTableVirtualScroll | null>(null);

  // The latest virtualizer, for the restore attempts below (declared first,
  // so it is current when the restore effect runs in the same commit).
  useLayoutEffect(() => {
    virtualizerRef.current = virtualizer;
  });

  // Record the live offset against the current entry: on scroll, and at the
  // moments a navigation can begin while this table is still in the DOM (a
  // click, Back / Forward, a reload). The map is written to storage then and
  // on unmount, so the next mount (Back from a dossier) can read it.
  useEffect(() => {
    const wrap = tableWrapRef.current;
    if (!enabled || !wrap) return undefined;
    const map = loadOffsets(offsetsRef);
    const capture = () => {
      const key = activeKeyRef.current;
      if (key === null || restoringRef.current) return;
      rememberOffsetCapped(map, key, wrap.scrollTop, MAX_ENTRIES);
    };
    const captureAndStore = () => {
      capture();
      writeOffsets(LEAD_TABLE_SCROLL_STORAGE_KEY, map);
    };
    const onUserIntent = () => {
      cancelPendingRef.current?.();
    };
    wrap.addEventListener('scroll', capture, { passive: true });
    for (const type of USER_INTENT_EVENTS) wrap.addEventListener(type, onUserIntent, { passive: true });
    window.addEventListener('click', captureAndStore, { capture: true, passive: true });
    window.addEventListener('popstate', captureAndStore);
    window.addEventListener('pagehide', captureAndStore);
    return () => {
      wrap.removeEventListener('scroll', capture);
      for (const type of USER_INTENT_EVENTS) wrap.removeEventListener(type, onUserIntent);
      window.removeEventListener('click', captureAndStore, { capture: true });
      window.removeEventListener('popstate', captureAndStore);
      window.removeEventListener('pagehide', captureAndStore);
      writeOffsets(LEAD_TABLE_SCROLL_STORAGE_KEY, map);
    };
  }, [enabled, tableWrapRef]);

  useLayoutEffect(() => {
    const wrap = tableWrapRef.current;
    if (!enabled || !wrap) return undefined;
    const map = loadOffsets(offsetsRef);
    activeKeyRef.current = storageKey;

    const settle = () => {
      restoringRef.current = false;
      cancelPendingRef.current = null;
      rememberOffsetCapped(map, storageKey, wrap.scrollTop, MAX_ENTRIES);
    };

    let cancelRetry: () => void = () => undefined;
    const saved = navigationType === 'POP' ? map.get(storageKey) : undefined;
    if (saved !== undefined) {
      restoringRef.current = true;
      let finished = false;
      const finishOnce = () => {
        if (finished) return;
        finished = true;
        settle();
      };
      const scrollTo = (offset: number) => {
        const virtual = virtualizerRef.current;
        if (virtual) virtual.scrollToOffset(offset);
        else wrap.scrollTop = offset;
      };
      // Ready once the rendered rows reach the offset. A virtualized table's
      // first commit holds only the header (its window is computed after it
      // measures the scroller), and scrollToOffset clamps to the scroller's
      // own scroll range, so both paths wait on the DOM, not on getTotalSize.
      const attempt = () => {
        const virtual = virtualizerRef.current;
        if (virtual && virtual.getTotalSize() - wrap.clientHeight < saved) return false;
        if (wrap.scrollHeight - wrap.clientHeight < saved) return false;
        scrollTo(saved);
        finishOnce();
        return true;
      };
      cancelRetry = retryUntil(attempt, wrap, MAIN_SCROLL_DEADLINE_MS, () => {
        // The rows never grew tall enough: go as far as they allow.
        scrollTo(saved);
        finishOnce();
      });
      if (!finished) {
        cancelPendingRef.current = () => {
          cancelRetry();
          finishOnce();
        };
      }
    } else if (navigationType === 'PUSH') {
      virtualizerRef.current?.scrollToOffset(0);
      wrap.scrollTop = 0;
      settle();
    } else {
      // REPLACE (expand / collapse) or a first load with nothing saved: keep
      // the offset, now under this entry's key.
      settle();
    }

    return () => {
      cancelRetry();
      restoringRef.current = false;
      cancelPendingRef.current = null;
    };
  }, [enabled, tableWrapRef, storageKey, navigationType]);
}
