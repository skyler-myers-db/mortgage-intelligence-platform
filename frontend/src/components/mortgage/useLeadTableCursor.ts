/**
 * useLeadTableCursor — the ranked-borrower table's active-row cursor (audit
 * tables-03).
 *
 * The verifier's correction applies: an active-row cursor on the NATIVE
 * table, not a role=grid rewrite and not a roving tabindex. Keyboard focus
 * stays on the `.tbl-wrap` scroll region (one tab stop) or on an in-row
 * control, which stay natively tabbable; the cursor is a row the J / K /
 * arrow keys move, drawn with the focus-ring tokens and announced through a
 * polite live region. A / R / X / Enter act on the cursor row.
 *
 * Virtualized tables (> LEAD_VIRTUALIZATION_THRESHOLD rows) keep only a
 * window of rows in the DOM: a cursor row outside it is brought in with the
 * virtualizer's `scrollToIndex`, then scrolled fully into view once rendered.
 */
import { useEffect, useState, type RefObject } from 'react';
import type { LeadSummary } from '../../types';
import { offerDisplayLabel } from '../../lib/offerLanguage';

export const LEAD_ROW_ATTR = 'data-borrower-row';

/** The main `<tr>` of one borrower (not its expanded preview row). */
export function leadRowSelector(borrowerId: string): string {
  return `tr[${LEAD_ROW_ATTR}="${borrowerId}"]`;
}

interface UseLeadTableCursorInput {
  sortedLeads: readonly LeadSummary[];
  tableWrapRef: RefObject<HTMLDivElement | null>;
  virtualized: boolean;
  scrollToIndex: (index: number) => void;
  /** Effective approval state of a row, for the announcement. */
  statusOf: (lead: LeadSummary) => string;
  /** A row the cursor may advance to after a decision. */
  isPending: (lead: LeadSummary) => boolean;
  /**
   * The cursor row at mount: a row restored from the URL (audit shell-03).
   * Read once; it moves nothing and fetches nothing.
   */
  initialCursorId?: string | null;
}

function findRow(scope: HTMLElement | null, borrowerId: string): HTMLElement | null {
  return scope?.querySelector<HTMLElement>(leadRowSelector(borrowerId)) ?? null;
}

export function useLeadTableCursor({
  sortedLeads,
  tableWrapRef,
  virtualized,
  scrollToIndex,
  statusOf,
  isPending,
  initialCursorId = null,
}: UseLeadTableCursorInput) {
  'use no memo';

  const [cursorId, setCursorId] = useState<string | null>(initialCursorId);
  const [announcement, setAnnouncement] = useState('');
  // A decision to advance from, resolved after the render that carries the
  // decided row's new state (so "next pending" reads fresh approvals).
  const [advanceFrom, setAdvanceFrom] = useState<string | null>(null);
  const cursorIndex = cursorId === null
    ? -1
    : sortedLeads.findIndex((lead) => lead.borrower_id === cursorId);

  function reveal(borrowerId: string, index: number) {
    const scope = tableWrapRef.current;
    const row = findRow(scope, borrowerId);
    if (row) {
      row.scrollIntoView?.({ block: 'nearest' });
      return;
    }
    if (!virtualized) return;
    scrollToIndex(index);
    requestAnimationFrame(() => findRow(tableWrapRef.current, borrowerId)?.scrollIntoView?.({ block: 'nearest' }));
  }

  function announce(lead: LeadSummary, index: number) {
    const offer = offerDisplayLabel(lead.recommended_offer_code, lead.recommended_offer);
    setAnnouncement(
      `Row ${index + 1} of ${sortedLeads.length}: ${lead.borrower_id}, ${lead.city}, ${lead.state}, `
      + `${offer}, score ${lead.opportunity_score}, ${statusOf(lead)}.`,
    );
  }

  /** Put the cursor on one row, scroll it into view and announce it. */
  function moveTo(borrowerId: string) {
    const index = sortedLeads.findIndex((lead) => lead.borrower_id === borrowerId);
    if (index < 0) return;
    setCursorId(borrowerId);
    reveal(borrowerId, index);
    announce(sortedLeads[index], index);
  }

  /**
   * J / K / the arrows. The first press lands on the first row (or on the
   * row a click already marked). Focus that sits on a control in another
   * row moves to the scroll region first: virtualization may unmount that
   * row, which would drop focus to <body> and take the keys out of scope.
   */
  function move(delta: 1 | -1) {
    if (sortedLeads.length === 0) return;
    const next = cursorIndex < 0
      ? 0
      : Math.min(sortedLeads.length - 1, Math.max(0, cursorIndex + delta));
    const scope = tableWrapRef.current;
    const active = document.activeElement;
    if (scope && active && active !== scope && scope.contains(active)) {
      scope.focus({ preventScroll: true });
    }
    moveTo(sortedLeads[next].borrower_id);
  }

  /** Scroll one row into view (through the virtualizer if needed); no announcement. */
  function revealRow(borrowerId: string) {
    const index = sortedLeads.findIndex((lead) => lead.borrower_id === borrowerId);
    if (index >= 0) reveal(borrowerId, index);
  }

  /** After an approve / reject write RETURNED ok: the next pending row. */
  function advanceAfter(decidedId: string) {
    setAdvanceFrom(decidedId);
  }

  // Runs after the render that carries the decision (its approval state and
  // the request land in one batch), so this render's closures are fresh.
  useEffect(() => {
    if (advanceFrom === null) return;
    const from = sortedLeads.findIndex((lead) => lead.borrower_id === advanceFrom);
    const order = from < 0
      ? sortedLeads
      : [...sortedLeads.slice(from + 1), ...sortedLeads.slice(0, from)];
    const next = order.find((lead) => lead.borrower_id !== advanceFrom && isPending(lead));
    // A one-shot request, consumed after the decision's render.
    setAdvanceFrom(null);
    if (next) moveTo(next.borrower_id);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on the one-shot request only
  }, [advanceFrom]);

  return {
    cursorId: cursorIndex >= 0 ? cursorId : null,
    cursorIndex,
    announcement,
    setCursorId,
    move,
    moveTo,
    revealRow,
    advanceAfter,
  };
}
