/**
 * useLeadTablePrint — the virtualized Lead Queue prints every LOADED row
 * (audit responsive-08, the W5d print deferral [9]).
 *
 * A virtualized table keeps only a window of rows in the DOM, so a Ctrl/Cmd-P
 * printed the window and blank spacer rows. On `beforeprint` the table
 * scroller's offset is saved and the window opens synchronously (flushSync,
 * before the browser lays out the print): the virtualizer is disabled and
 * every loaded row renders. On `afterprint` it re-virtualizes and restores
 * the offset through the virtualizer (the shell's LeadTableVirtualScroll).
 *
 * Code only: no read, no refetch, no prefetch and no audit row (a keyboard
 * print of the queue stays unaudited, as ruled at W5d; Export PDF is the
 * audited path where a surface offers one). The listeners live only while
 * the table is virtualized, and go on unmount.
 */
import { useEffect, useRef, useState, type RefObject } from 'react';
import { flushSync } from 'react-dom';
import type { LeadTableVirtualScroll } from './useLeadTableScroll';

export interface UseLeadTablePrintInput {
  /** The table is virtualized: only then does a print need the window opened. */
  virtualized: boolean;
  tableWrapRef: RefObject<HTMLDivElement | null>;
  /** The virtualizer the body publishes while it windows (null while printing). */
  virtualScrollRef: RefObject<LeadTableVirtualScroll | null>;
}

/** True between `beforeprint` and `afterprint`: render every loaded row. */
export function useLeadTablePrint({ virtualized, tableWrapRef, virtualScrollRef }: UseLeadTablePrintInput): boolean {
  const [printing, setPrinting] = useState(false);
  const savedOffsetRef = useRef(0);
  useEffect(() => {
    if (!virtualized) return undefined;
    const beforePrint = () => {
      savedOffsetRef.current = tableWrapRef.current?.scrollTop ?? 0;
      flushSync(() => setPrinting(true));
    };
    const afterPrint = () => {
      flushSync(() => setPrinting(false));
      // The re-enabled virtualizer is published in that commit's layout phase.
      virtualScrollRef.current?.scrollToOffset(savedOffsetRef.current);
    };
    window.addEventListener('beforeprint', beforePrint);
    window.addEventListener('afterprint', afterPrint);
    return () => {
      window.removeEventListener('beforeprint', beforePrint);
      window.removeEventListener('afterprint', afterPrint);
    };
  }, [virtualized, tableWrapRef, virtualScrollRef]);
  return printing;
}
