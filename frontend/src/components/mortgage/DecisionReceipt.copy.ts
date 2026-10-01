/**
 * On-screen copy and the small label maps the Decision receipt renders.
 * Kept beside the component so the fixture spec and unit test pin the same
 * strings the approver reads.
 */
import type { IconName } from '../Icon';
import type { DecisionOutcome } from '../../lib/apiTypes';
import { formatDate, parseBackendTimestamp } from '../../lib/time';
import { REJECT_REASONS } from './LeadTable.constants';

export const DECISION_RECEIPT_COPY = {
  title: 'Decision receipt',
  announceRecorded: 'Decision receipt recorded',
  recording: 'Recording decision…',
  recordingNote: 'Reading the ledger row back',
  /** A passive read of an earlier decision (page load, "Latest decision"). */
  reading: 'Reading decision receipt…',
  readingNote: 'From the Lakebase audit ledger',
  readBackNote: 'Read back from the Lakebase audit ledger',
  recorded: 'Recorded',
  unavailableTitle: 'Recorded; receipt unavailable',
  unavailableScoped:
    'The decision is in the audit ledger. This receipt is scoped to the approver who wrote it or an admin.',
  unavailableError: "Couldn't read the ledger row back:",
  unconfirmed: 'Unconfirmed',
  notFoundTitle: 'Ledger row not found',
  unavailableNotFound:
    'The write returned this audit id, but the ledger read-back did not find a decision row for it.',
  /** 404 on a passive read: the id came from the decision record, not a write made here. */
  unavailableNotFoundRecord:
    'The decision record points at this audit id, but the ledger read-back did not find a decision row for it.',
  evidence: 'Evidence cited',
  /**
   * The chips are the Unity Catalog assets of the offer branch the row
   * recorded (derived server-side from its stored offer code and decision
   * inputs), not a list the decision write stored.
   */
  evidenceAssetsNote: 'Unity Catalog assets of the recorded offer branch',
  noEvidenceAssets: 'No governed assets recorded for this offer branch.',
  scoreAtDecision: 'Score at decision',
  scoreNote: 'from the lead payload',
  copy: 'Copy audit id',
  copied: 'Copied audit id',
  copyFailed: 'Copy unavailable',
  copiedNotice: 'Audit id copied',
  copyFailedNotice: 'Audit id could not be copied',
  print: 'Print receipt',
  retry: 'Retry read-back',
  openExplorer: 'Open in audit explorer',
  routingPrefix: 'Routing:',
  /** The routing line is the approve response's, not the ledger row's (no staff email enters the read-back). */
  routingSource: '(from the approval response)',
} as const;

/**
 * Audit event types and actions whose ledger row is a decision a receipt can
 * be read back for. Mirrors backend/services/audit_store_receipt.py
 * `_DECISION_BY_EVENT_TYPE` / `_DECISION_BY_ACTION`, and normalizes the way
 * `decision_outcome_for` does; tests/unit/test_decision_receipt_event_parity.py
 * pins the parity by reading this file.
 */
export const DECISION_RECEIPT_EVENT_TYPES = [
  'APPROVE',
  'OUTREACH_APPROVE',
  'OUTREACH_REJECT',
  'REJECT',
  'OUTREACH_REVOKE',
  'OUTREACH_HOLD',
  'HOLD',
] as const;
export const DECISION_RECEIPT_ACTIONS = ['outreach.approve', 'outreach.reject', 'outreach.revoke', 'outreach.hold'] as const;

export function isDecisionReceiptEvent(event: { event_type?: string | null; action?: string | null }): boolean {
  const eventType = (event.event_type ?? '').trim().toUpperCase();
  if ((DECISION_RECEIPT_EVENT_TYPES as readonly string[]).includes(eventType)) return true;
  const action = (event.action ?? '').trim().toLowerCase();
  return (DECISION_RECEIPT_ACTIONS as readonly string[]).includes(action);
}

/** Where an approval was routed, as the approve response returned it (carryover #12). */
export interface DecisionRouting {
  assignedTo: string | null;
  followUpAt: string | null;
}

/**
 * "Routing: Assigned to X · follow-up Jul 19 (from the approval response)",
 * or null when the approval was not routed (the same rule as the toast).
 */
export function decisionRoutingLine(routing: DecisionRouting | null | undefined): string | null {
  if (!routing || (!routing.assignedTo && !routing.followUpAt)) return null;
  const followUp = parseBackendTimestamp(routing.followUpAt);
  const parts = [routing.assignedTo ? `Assigned to ${routing.assignedTo}` : 'Unassigned'];
  if (followUp) parts.push(`follow-up ${formatDate(followUp, { withYear: false })}`);
  return `${DECISION_RECEIPT_COPY.routingPrefix} ${parts.join(' · ')} ${DECISION_RECEIPT_COPY.routingSource}`;
}

/**
 * The decision a durable lifecycle / approval status states, for a caller
 * that passes the known outcome to the receipt (Borrower 360). `pending`
 * has made no decision, so it maps to none.
 */
export function decisionOutcomeForStatus(status: string | null | undefined): DecisionOutcome | undefined {
  if (status === 'approved') return 'approved';
  if (status === 'rejected') return 'rejected';
  if (status === 'hold') return 'held';
  return undefined;
}

export function decisionChip(decision: DecisionOutcome): {
  variant: 'success' | 'danger' | 'warning';
  icon: IconName;
  label: string;
} {
  if (decision === 'rejected') return { variant: 'danger', icon: 'cross', label: 'Rejected' };
  if (decision === 'held') return { variant: 'warning', icon: 'shield', label: 'Held' };
  // A revoke (audit flow-v2) supersedes an approval; never read it as one.
  if (decision === 'revoked') return { variant: 'warning', icon: 'cross', label: 'Revoked' };
  return { variant: 'success', icon: 'check', label: 'Approved' };
}

const CHANNEL_LABELS: Readonly<Record<string, string>> = {
  email: 'Email',
  sms: 'SMS',
  direct_mail: 'Direct mail',
};

export function receiptChannelLabel(channel: string): string {
  return CHANNEL_LABELS[channel] ?? channel.replace(/_/g, ' ');
}

/**
 * The receipt's review line (audit flow-03; deviation:receipt-review-mode-line):
 * how the approver saw the copy the row certifies, read back from the
 * ledger's review_mode, or the bulk rejection run a reject belonged to.
 * Null when the row records neither (no line is shown).
 */
export function receiptReviewLine(receipt: {
  decision: DecisionOutcome;
  review_mode: string | null;
  bulk_id: string | null;
}): string | null {
  const run = receipt.bulk_id ? ` ${receipt.bulk_id.slice(0, 8)}` : '';
  if (receipt.decision === 'rejected') {
    return receipt.bulk_id ? `Bulk rejection${run} · shared note recorded` : null;
  }
  switch (receipt.review_mode) {
    case 'individual':
      return 'Copy shown to the approver before approval';
    case 'triage':
      return 'Copy shown to the approver before approval (Triage)';
    case 'bulk_sample':
      return `Bulk run${run} · this borrower's copy was previewed`;
    case 'bulk_cohort':
      return `Bulk run${run} · approved under the shared rationale; copy not individually shown`;
    case 'undeclared':
      return 'Review mode not recorded (earlier client)';
    default:
      return null;
  }
}

export function humanizeReasonCode(code: string): string {
  const known = REJECT_REASONS.find((reason) => reason.code === code);
  return known ? known.label : code.replace(/_/g, ' ');
}
