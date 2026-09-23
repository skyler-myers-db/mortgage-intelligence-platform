/**
 * On-screen copy and the small label maps the Decision receipt renders.
 * Kept beside the component so the fixture spec and unit test pin the same
 * strings the approver reads.
 */
import type { IconName } from '../Icon';
import type { DecisionOutcome } from '../../lib/apiTypes';
import { REJECT_REASONS } from './LeadTable.constants';

export const DECISION_RECEIPT_COPY = {
  title: 'Decision receipt',
  announceRecorded: 'Decision receipt recorded',
  recording: 'Recording decision…',
  recordingNote: 'Reading the ledger row back',
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
  noEvidenceAssets: 'No governed assets recorded for this offer branch.',
  scoreAtDecision: 'Score at decision',
  scoreNote: 'from the lead payload',
  copy: 'Copy audit id',
  copied: 'Copied audit id',
  copyFailed: 'Copy unavailable',
  print: 'Print receipt',
  retry: 'Retry read-back',
  openExplorer: 'Open in audit explorer',
} as const;

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

export function humanizeReasonCode(code: string): string {
  const known = REJECT_REASONS.find((reason) => reason.code === code);
  return known ? known.label : code.replace(/_/g, ' ');
}
