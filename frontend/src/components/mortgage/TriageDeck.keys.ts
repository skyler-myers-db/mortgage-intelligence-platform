/**
 * The Triage deck's keys (D-approval-flow-a2), scope 'triage': A reviews
 * the draft, R rejects, J / Right skips, K / Left goes back, O opens the
 * offer builder. Escape is the escape stack's (Back to table).
 *
 * Its own module beside the deck (the brief placed them in
 * LeadTable.keymap.ts): only the lazy TriageDeck chunk imports them, so the
 * shared LeadTable chunk carries none of it.
 */
import type { LeadTableHotkey } from './useLeadTableHotkeys';

export const TRIAGE_KEYS = {
  approve: ['a'],
  reject: ['r'],
  skip: ['j', 'ArrowRight'],
  back: ['k', 'ArrowLeft'],
  offer: ['o'],
} as const satisfies Record<string, readonly string[]>;

export interface TriageKeymapActions {
  review: () => void;
  reject: () => void;
  skip: () => void;
  back: () => void;
  openOffer: () => void;
}

/** The deck's `?` sheet: four rows at most (J / Right and K / Left share one). */
export function triageHotkeys(actions: TriageKeymapActions): LeadTableHotkey[] {
  return [
    {
      id: 'triage-approve',
      keys: TRIAGE_KEYS.approve,
      description: 'Review the draft, then approve (Enter confirms)',
      run: () => actions.review(),
    },
    { id: 'triage-reject', keys: TRIAGE_KEYS.reject, description: 'Reject with a reason', run: () => actions.reject() },
    {
      id: 'triage-move',
      keys: [...TRIAGE_KEYS.skip, ...TRIAGE_KEYS.back],
      description: 'Skip to the next borrower; K goes back',
      run: (event) => (event.key === 'ArrowLeft' || event.key.toLowerCase() === 'k' ? actions.back() : actions.skip()),
    },
    { id: 'triage-offer', keys: TRIAGE_KEYS.offer, description: 'Open the offer builder', run: () => actions.openOffer() },
  ];
}
