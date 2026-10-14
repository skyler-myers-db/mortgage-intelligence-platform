/**
 * The decision history row's closed qualifiers (audit flow-04 phase 2).
 *
 * Its own small label maps on purpose: importing LeadTable.constants.ts would
 * split a shared chunk out of the LeadTable chunk. Every value the server
 * sends is already a closed vocabulary (BorrowerDecisionEvent), so a code
 * with no label here is simply left out, never printed raw.
 */
import type { BorrowerDecisionEvent } from '../../lib/apiClients/borrowerDecisions';
import { ACTIVITY_LABELS } from '../../lib/auditEventPresentation';
import { offerDisplayLabel } from '../../lib/offerLanguage';

type Item = BorrowerDecisionEvent;

const CHANNEL_LABELS: Record<NonNullable<Item['channel']>, string> = {
  email: 'Email',
  sms: 'SMS',
  direct_mail: 'Direct mail',
};

const STATUS_LABELS: Record<NonNullable<Item['to_status']>, string> = {
  assigned: 'Assigned',
  contact_drafted: 'Contact drafted',
  approved: 'Approved',
  actioned: 'Actioned',
  outcome_recorded: 'Outcome recorded',
};

const DISPOSITION_LABELS: Record<NonNullable<Item['disposition_outcome']>, string> = {
  called_no_answer: 'No answer',
  called_left_voicemail: 'Left voicemail',
  connected: 'Connected',
  callback_scheduled: 'Callback scheduled',
  application_started: 'Application started',
  not_interested: 'Not interested',
  not_now: 'Not now',
  dead: 'Dead lead',
};

const LEAD_OUTCOME_LABELS: Record<NonNullable<Item['lead_outcome_type']>, string> = {
  application_submitted: 'Application submitted',
  closed_funded: 'Closed and funded',
  lost_to_competitor: 'Lost to a competitor',
  withdrawn: 'Withdrawn',
  not_qualified: 'Not qualified',
};

const ACTIVATION_LABELS: Record<NonNullable<Item['activation_status']>, string> = {
  dry_run: 'Dry run',
  staged: 'Staged',
  delivered: 'Delivered',
  failed: 'Failed',
  cancelled: 'Cancelled',
};

/** "Outreach approved": the label carries the outcome word, so tone is never colour-only. */
export function decisionWhat(item: Item): string {
  return ACTIVITY_LABELS[item.event_type] ?? 'Decision recorded';
}

/** Who decided ("· you", "· bulk"), then the row's closed qualifiers. */
export function decisionWho(item: Item): string {
  const actor = [item.actor_display, item.is_own ? 'you' : null, item.bulk ? 'bulk' : null];
  const status = item.from_status && item.to_status
    ? `${STATUS_LABELS[item.from_status]} → ${STATUS_LABELS[item.to_status]}`
    : item.to_status ? `Now ${STATUS_LABELS[item.to_status]}` : null;
  const qualifiers = [
    item.offer_code ? offerDisplayLabel(item.offer_code) : null,
    item.channel ? CHANNEL_LABELS[item.channel] : null,
    item.rationale_label ? `Reason: ${item.rationale_label}` : null,
    item.contact_block_label ? `Contact blocked: ${item.contact_block_label}` : null,
    item.assigned_to_display ? `Assignee: ${item.assigned_to_display}` : null,
    status,
    item.disposition_outcome ? DISPOSITION_LABELS[item.disposition_outcome] : null,
    item.lead_outcome_type ? LEAD_OUTCOME_LABELS[item.lead_outcome_type] : null,
    item.activation_status ? ACTIVATION_LABELS[item.activation_status] : null,
  ];
  return [...actor, ...qualifiers].filter((part): part is string => Boolean(part)).join(' · ');
}

/** The receipt a decision row opens; the server offers one only for these three. */
export function receiptDecision(item: Item): 'approved' | 'rejected' | 'revoked' | null {
  if (item.event_type === 'APPROVE') return 'approved';
  if (item.event_type === 'OUTREACH_REJECT') return 'rejected';
  if (item.event_type === 'OUTREACH_REVOKE') return 'revoked';
  return null;
}
