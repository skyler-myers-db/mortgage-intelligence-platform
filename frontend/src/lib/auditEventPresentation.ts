/**
 * Audit event presentation shared by the Console's "My recent activity" and
 * the Borrower 360 / Offer decision history (audit flow-04 phase 2,
 * tables-10): one human label per event code, so the two surfaces never word
 * the same ledger row differently. Moved verbatim out of
 * components/layout/Console.tsx.
 */
import type { IconName } from '../components/Icon';
import type { BorrowerDecisionEvent } from './apiClients/borrowerDecisions';
import type { ActorAuditEventSummary } from './apiTypes';

export const ACTIVITY_LABELS: Record<string, string> = {
  APPROVE: 'Outreach approved',
  OUTREACH_APPROVE: 'Outreach approved',
  OUTREACH_REJECT: 'Outreach rejected',
  REJECT: 'Outreach rejected',
  DRAFT_OUTREACH: 'Outreach draft created',
  SAVE_DRAFT: 'Outreach draft saved',
  DELETE_DRAFT: 'Outreach draft removed',
  SAVE_LEAD: 'Lead saved',
  UNSAVE_LEAD: 'Lead removed',
  LEAD_ASSIGN: 'Lead assigned',
  LEAD_DISTRIBUTE: 'Leads distributed',
  CALL_DISPOSITION: 'Call disposition recorded',
  LEAD_OUTCOME: 'Lead outcome recorded',
  LEAD_OUTCOME_RECORDED: 'Lead outcome recorded',
  PORTFOLIO_CREATE: 'Portfolio created',
  RECOMMEND_OFFER: 'Offer recommended',
  RUN_GENIE: 'Genie analysis run',
  VIEW_BORROWER: 'Borrower reviewed',
  VIEW_LEADS: 'Lead queue reviewed',
  VIEW_AUDIT_LEDGER: 'Audit ledger read',
  // W5b / W5c event types (D-audit-reads-c2, D-audit-reads-d). Later lanes
  // append theirs below.
  LEAD_ASSIGNMENT_STATUS: 'Lead assignment status changed',
  ACTIVATION_STAGE: 'Activation staged',
  SUPPRESS_CONTACT: 'Contact blocked',
  OUTREACH_REVOKE: 'Outreach approval revoked',
  APPROVAL_REQUESTED: 'Approval requested',
  APPROVAL_REQUEST_REFUSED: 'Approval request refused',
  APPROVAL_REQUEST_WITHDRAWN: 'Approval request withdrawn',
  LEAD_UNASSIGN: 'Lead assignment released',
  VIEW_REFUSAL_REPORT_TEXT: 'Refusal question read',
  GENIE_SECTION_REVEALED: 'Genie verified section made available',
};

export type DecisionTone = 'green' | 'amber' | 'red';

/**
 * The decision history row's icon tone. The row's label always carries the
 * outcome word too, so the tone is never the only signal (WCAG 1.4.1).
 */
export function decisionTone(outcome: BorrowerDecisionEvent['outcome']): DecisionTone {
  if (outcome === 'approved' || outcome === 'assigned') return 'green';
  if (outcome === 'rejected' || outcome === 'revoked' || outcome === 'contact_blocked') return 'red';
  return 'amber';
}

export function recentActivityPresentation(event: ActorAuditEventSummary): {
  label: string;
  icon: IconName;
  tone: string;
  context: string;
} {
  const fallback = event.event_type
    .toLowerCase()
    .split('_')
    .filter(Boolean)
    .map((word, index) => index === 0 ? `${word.charAt(0).toUpperCase()}${word.slice(1)}` : word)
    .join(' ');
  const rejected = event.event_type.includes('REJECT');
  const approved = event.event_type.includes('APPROVE');
  const icon: IconName = rejected
    ? 'cross'
    : approved
      ? 'check'
      : event.event_type.includes('GENIE')
        ? 'sparkle'
        : 'audit';
  const entity = event.entity_type.replace(/_/g, ' ');
  return {
    label: ACTIVITY_LABELS[event.event_type] ?? (fallback || 'Activity recorded'),
    icon,
    tone: rejected ? 'red' : approved ? 'green' : '',
    context: event.subject_id ?? entity,
  };
}
