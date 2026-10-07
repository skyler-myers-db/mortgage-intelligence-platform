/**
 * The ranked table's Triage deck wiring (D-approval-flow-a2): the
 * `?mode=triage` contract, the approver-only entry, the lazy deck chunk and
 * what the deck gets from the table's own flow. The deck itself
 * (TriageDeck, its own chunk) is code only: nothing here reads or writes.
 *
 *   - A definitive non-approver never sees the deck: the table strips the
 *     mode from the URL (replace). A failed session check keeps the mode
 *     (the deep link, brief 6.2) and says why the deck is shut, with a way
 *     back; a recovered check opens it.
 *   - The deck opens once the session says approver and the rows are the
 *     current filters' (not placeholder rows), then stays open for its
 *     session: a later filter change's placeholder rows never unmount it,
 *     so it never re-snapshots (LeadTable latches it).
 *   - The deck snapshots its order when it mounts (useLeadTriage's
 *     triageEntryOrder): two or more selected eligible rows, else every
 *     eligible row, in the table's current sort. Entry keeps the selection,
 *     so the click's order and the mount's are the same.
 *
 * Budget (LeadTable chunk): plain helpers the (uncompiled) table calls; the
 * table keeps the few state cells itself, so nothing here carries a
 * compiler memo cache.
 */
import type { ComponentType } from 'react';
import type { LeadSummary } from '../../types';
import { Button } from '../Primitives';
import type { LeadApproveReviewProps } from './LeadApproveReview';
import type { CampaignBinding } from './LeadTable.logic';
import type { LeadTableTriage } from './LeadTable.types';
import type { TriageDeckProps } from './TriageDeck';
import { lazyModule } from './useLazyModule';
import type { CampaignBindingState, useLeadApprovalActions } from './useLeadApprovalActions';
import type { useLeadTableKeyboardFlow } from './useLeadTableKeyboardFlow';

export const TRIAGE_CHUNK = lazyModule(() => import('./TriageDeck'));

type ApprovalActions = ReturnType<typeof useLeadApprovalActions>;
type KeyboardFlow = ReturnType<typeof useLeadTableKeyboardFlow>;
export type TriageApprovedSignal = { borrowerId: string; seq: number } | null;

export interface TriageEntry {
  label: string;
  /** Non-null: the button is aria-disabled and described by this reason. */
  disabledReason: string | null;
}

/** What the table holds for the deck (its own state cells). */
export interface LeadTableTriageState {
  triage: LeadTableTriage | null;
  /** The table is hidden behind the deck (or its loading line). */
  hideTable: boolean;
  /** The deck is open: approver session, settled rows at its first open. */
  active: boolean;
  Deck: ComponentType<TriageDeckProps> | null;
  failed: boolean;
  /** The approver check failed (no session): why the deck stays shut. */
  blocked: string | null;
  approvedSignal: TriageApprovedSignal;
  setApprovedSignal: (update: (previous: TriageApprovedSignal) => TriageApprovedSignal) => void;
  /** The last card shown (the row the table returns to). */
  lastShown: string | null;
  setLastShown: (borrowerId: string | null) => void;
}

/**
 * The header's entry: null when the table has no deck, the deck is open, or
 * the session says the actor is not an approver. A loading or failed check
 * keeps it visible and aria-disabled with the reason.
 */
export function triageEntry(
  state: LeadTableTriageState,
  approval: ApprovalActions,
  nonApprover: boolean,
  approverGate: string | null,
  campaignBindingState: CampaignBindingState,
  busy: boolean,
): TriageEntry | null {
  if (!state.triage || state.hideTable || nonApprover) return null;
  const selected = approval.selectedApprovalEligibleCount;
  const count = selected >= 2 ? selected : approval.approvalEligibleIds.length;
  return {
    label: selected >= 2 ? `Triage ${count} selected` : `Triage (${count})`,
    disabledReason: approverGate
      ?? (campaignBindingState === 'validating' || campaignBindingState === 'invalid' ? 'Campaign binding not verified' : null)
      ?? (count === 0 ? 'Nothing here awaits approval' : null)
      ?? (busy ? 'A decision is on the wire' : null),
  };
}

/** Enter (a new entry): an open review that is not on the wire is cancelled first, drafting nothing. */
export function enterTriage(state: LeadTableTriageState, entry: TriageEntry | null, cancelOpenReview: () => void) {
  if (!state.triage || !entry || entry.disabledReason !== null) return;
  cancelOpenReview();
  state.triage.onModeChange('triage');
}

/** The keyboard flow's triage input. */
export function triageFlowInput(state: LeadTableTriageState) {
  return {
    active: state.active,
    onApproved: (borrowerId: string) => state.setApprovedSignal((previous) => ({
      borrowerId,
      seq: (previous?.seq ?? 0) + 1,
    })),
    lastShown: () => state.lastShown,
  };
}

export interface TriageDeckRenderInput {
  sortedLeads: readonly LeadSummary[];
  flow: KeyboardFlow;
  approval: ApprovalActions;
  leadsById: ReadonlyMap<string, LeadSummary>;
  approvals: Record<string, 'approved' | 'rejected'>;
  ReviewInline: ComponentType<LeadApproveReviewProps> | null;
  actorEmail: string | null;
  campaignBinding: CampaignBinding | null;
  canAccessAdmin: boolean;
}

/** The deck where the table was: loading, failed (with a way back), or the deck itself. */
export function renderTriageDeck(state: LeadTableTriageState, input: TriageDeckRenderInput) {
  const { Deck, triage } = state;
  if (!state.hideTable) return null;
  const shut = state.blocked !== null
    ? `${state.blocked}, so the triage deck did not open.`
    : state.failed && !Deck ? 'The triage deck could not load.' : null;
  if (shut) {
    return (
      <div role="alert" className="table-error" data-testid="triage-deck-failed">
        {shut} Nothing was drafted or approved.{' '}
        <Button size="sm" variant="ghost" onClick={() => triage?.onModeChange(null)}>Back to table</Button>
      </div>
    );
  }
  if (!state.active || !Deck) {
    return <div role="status" className="table-neutral" data-testid="triage-deck-loading">Opening the triage deck…</div>;
  }
  return <Deck {...input} state={state} />;
}
