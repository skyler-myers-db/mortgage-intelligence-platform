/**
 * The Triage deck's pure state (D-approval-flow-a2): the order is a
 * snapshot, skips and back never write, cards locked outside the deck or
 * gone from the loaded rows drop out, N counts live cards plus cards decided
 * here, and "Review skipped" restarts with the skipped cards only.
 */
import { describe, expect, it } from 'vitest';
import {
  initialTriageState,
  triageReducer,
  triageView,
  type TriageAction,
  type TriageAvailability,
  type TriageState,
} from './useLeadTriage';

const ORDER = ['B-1', 'B-2', 'B-3', 'B-4'];
const all: TriageAvailability = () => true;

function run(state: TriageState, ...actions: TriageAction[]): TriageState {
  return actions.reduce(triageReducer, state);
}

describe('triage deck state', () => {
  it('starts on the first card of the snapshot', () => {
    const view = triageView(initialTriageState(ORDER), all);
    expect(view).toMatchObject({ currentId: 'B-1', position: 1, total: 4, approved: 0, rejected: 0, skipped: 0 });
    expect(view.previousId).toBeNull();
  });

  it('skip moves on without a decision; back returns; the summary follows the last card', () => {
    let state = run(initialTriageState(ORDER), { type: 'skip', available: all }, { type: 'skip', available: all });
    expect(triageView(state, all)).toMatchObject({ currentId: 'B-3', position: 3, skipped: 2 });
    state = run(state, { type: 'back', available: all });
    expect(triageView(state, all)).toMatchObject({ currentId: 'B-2', previousId: 'B-1' });
    state = run(state, { type: 'skip', available: all }, { type: 'skip', available: all }, { type: 'skip', available: all });
    const summary = triageView(state, all);
    expect(summary.currentId).toBeNull();
    expect(summary.position).toBe(5);
    expect(summary.previousId, 'K from the summary returns to the last card').toBe('B-4');
  });

  it('a decision advances past its card and counts; the decided card stays reachable with back', () => {
    let state = run(initialTriageState(ORDER), { type: 'decided', borrowerId: 'B-1', decision: 'approved', available: all });
    expect(triageView(state, all)).toMatchObject({ currentId: 'B-2', position: 2, total: 4, approved: 1 });
    state = run(state, { type: 'decided', borrowerId: 'B-2', decision: 'rejected', available: all });
    expect(triageView(state, all)).toMatchObject({ currentId: 'B-3', approved: 1, rejected: 1 });
    state = run(state, { type: 'back', available: all });
    expect(triageView(state, all).currentId).toBe('B-2');
    expect(state.decided['B-2']).toBe('rejected');
  });

  it('cards locked outside the deck or gone from the rows drop out; decided-here cards still count', () => {
    let state = run(initialTriageState(ORDER), { type: 'decided', borrowerId: 'B-1', decision: 'approved', available: all });
    // B-1 is now locked (approved), B-3 was decided in another tab, B-4 left the rows.
    const available: TriageAvailability = (id) => id === 'B-2';
    const view = triageView(state, available);
    expect(view.total, 'one live card plus one decided here').toBe(2);
    expect(view.currentId).toBe('B-2');
    state = run(state, { type: 'skip', available });
    expect(triageView(state, available).currentId, 'B-3 and B-4 are not shown').toBeNull();
    expect(state.order, 'the deck never re-snapshots').toEqual(ORDER);
  });

  it('the card on screen gone mid-view: the next card still in the deck is shown', () => {
    const state = run(initialTriageState(ORDER), { type: 'skip', available: all });
    expect(triageView(state, (id) => id !== 'B-2').currentId).toBe('B-3');
  });

  it('"Review skipped" restarts with the skipped cards in snapshot order and fresh counts', () => {
    let state = run(
      initialTriageState(ORDER),
      { type: 'skip', available: all },
      { type: 'decided', borrowerId: 'B-2', decision: 'approved', available: all },
      { type: 'skip', available: all },
      { type: 'skip', available: all },
    );
    expect(triageView(state, all)).toMatchObject({ currentId: null, approved: 1, skipped: 3 });
    // B-4 was decided elsewhere since: it is not reviewed again.
    state = run(state, { type: 'restart-skipped', available: (id) => id !== 'B-4' });
    expect(state.order).toEqual(['B-1', 'B-3']);
    expect(triageView(state, all)).toMatchObject({ currentId: 'B-1', position: 1, total: 2, approved: 0, skipped: 0 });
  });

  it('a skipped card decided later is no longer counted as skipped', () => {
    let state = run(initialTriageState(ORDER), { type: 'skip', available: all }, { type: 'back', available: all });
    expect(triageView(state, all).skipped).toBe(1);
    state = run(state, { type: 'decided', borrowerId: 'B-1', decision: 'rejected', available: all });
    expect(triageView(state, all)).toMatchObject({ skipped: 0, rejected: 1 });
  });
});
