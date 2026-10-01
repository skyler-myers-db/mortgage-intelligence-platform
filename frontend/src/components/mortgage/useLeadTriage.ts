/**
 * The Triage deck's state (D-approval-flow-a2): a pure reducer plus the
 * selectors the deck reads. No reads and no writes live here.
 *
 *   - The order is snapshotted at entry and never re-snapshotted.
 *   - A card is LIVE while it is neither locked outside the deck (a decision
 *     latched, on the wire or recorded elsewhere) nor gone (no longer in the
 *     loaded rows: a filter change or a Refresh while the deck is open).
 *   - N = live cards plus cards decided in this deck; k = the position.
 *   - Counts: approved, rejected, skipped (skipped cards still undecided).
 *   - "Review skipped (s)" restarts with the skipped cards in snapshot order
 *     and fresh counts.
 *
 * `available(id)` answers "live outside the deck" at dispatch time; the
 * reducer itself never reads anything else.
 */
import { useReducer } from 'react';
import type { LeadSummary } from '../../types';

export type TriageDecision = 'approved' | 'rejected';

export interface TriageState {
  /** The snapshot, in the table's order at entry. */
  order: readonly string[];
  /** Decisions made in this deck. */
  decided: Readonly<Record<string, TriageDecision>>;
  /** Cards skipped in this deck (no write). */
  skipped: ReadonlySet<string>;
  /** The card on screen; null once past the last card (the summary). */
  currentId: string | null;
}

export type TriageAvailability = (borrowerId: string) => boolean;

export type TriageAction =
  | { type: 'decided'; borrowerId: string; decision: TriageDecision; available: TriageAvailability }
  | { type: 'skip'; available: TriageAvailability }
  | { type: 'back'; available: TriageAvailability }
  | { type: 'restart-skipped'; available: TriageAvailability };

export function initialTriageState(order: readonly string[]): TriageState {
  return { order, decided: {}, skipped: new Set(), currentId: order[0] ?? null };
}

/** The deck's cards right now: decided here, or still live. */
export function triageSequence(state: TriageState, available: TriageAvailability): string[] {
  return state.order.filter((id) => state.decided[id] !== undefined || available(id));
}

/** The card actually shown: the current one, or the next card after it that is still in the deck. */
export function triageCurrent(state: TriageState, available: TriageAvailability): string | null {
  if (state.currentId === null) return null;
  const sequence = new Set(triageSequence(state, available));
  const from = state.order.indexOf(state.currentId);
  for (let index = Math.max(0, from); index < state.order.length; index += 1) {
    if (sequence.has(state.order[index])) return state.order[index];
  }
  return null;
}

export interface TriageView {
  /** The card on screen, or null for the summary. */
  currentId: string | null;
  /** 1-based position of the card (N + 1 on the summary). */
  position: number;
  total: number;
  approved: number;
  rejected: number;
  skipped: number;
  /** The previous card in the deck (K / Left), if any. */
  previousId: string | null;
}

export function triageView(state: TriageState, available: TriageAvailability): TriageView {
  const sequence = triageSequence(state, available);
  const currentId = triageCurrent(state, available);
  const at = currentId === null ? sequence.length : sequence.indexOf(currentId);
  const decisions = Object.values(state.decided);
  return {
    currentId,
    position: at + 1,
    total: sequence.length,
    approved: decisions.filter((decision) => decision === 'approved').length,
    rejected: decisions.filter((decision) => decision === 'rejected').length,
    skipped: sequence.filter((id) => state.skipped.has(id) && state.decided[id] === undefined).length,
    previousId: at > 0 ? sequence[at - 1] : null,
  };
}

function nextAfter(state: TriageState, borrowerId: string, available: TriageAvailability): string | null {
  const sequence = triageSequence(state, available);
  const at = sequence.indexOf(borrowerId);
  return at >= 0 && at + 1 < sequence.length ? sequence[at + 1] : null;
}

export function triageReducer(state: TriageState, action: TriageAction): TriageState {
  switch (action.type) {
    case 'decided': {
      const decided = { ...state.decided, [action.borrowerId]: action.decision };
      const next = { ...state, decided };
      return { ...next, currentId: nextAfter(next, action.borrowerId, action.available) };
    }
    case 'skip': {
      const currentId = triageCurrent(state, action.available);
      if (currentId === null) return state;
      const skipped = state.decided[currentId] === undefined
        ? new Set([...state.skipped, currentId])
        : state.skipped;
      return { ...state, skipped, currentId: nextAfter(state, currentId, action.available) };
    }
    case 'back': {
      const { previousId } = triageView(state, action.available);
      return previousId === null ? state : { ...state, currentId: previousId };
    }
    case 'restart-skipped': {
      const order = state.order.filter((id) => (
        state.skipped.has(id) && state.decided[id] === undefined && action.available(id)
      ));
      return initialTriageState(order);
    }
    default:
      return state;
  }
}

/** The deck's order: two or more selected eligible rows, else every eligible row, in the table's sort. */
export function triageEntryOrder(
  sortedLeads: readonly LeadSummary[],
  eligibleIds: readonly string[],
  selectedIds: ReadonlySet<string>,
): readonly string[] {
  const eligible = new Set(eligibleIds);
  const ordered = sortedLeads.map((lead) => lead.borrower_id).filter((id) => eligible.has(id));
  const selected = ordered.filter((id) => selectedIds.has(id));
  return selected.length >= 2 ? selected : ordered;
}

/** The deck's reducer over an order snapshotted once, at mount. */
export function useLeadTriage(order: () => readonly string[]) {
  return useReducer(triageReducer, undefined, () => initialTriageState(order()));
}
