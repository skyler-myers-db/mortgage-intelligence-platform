/**
 * Human labels for audit ledger event types (audit flow-04 phase 1 / tables-10).
 *
 * The explorer used to print raw codes twice ('APPROVE_OUTREACH /
 * APPROVE_OUTREACH'); compliance users had to memorise them to filter. Each
 * row now reads as a sentence fragment and keeps the raw code beside it in a
 * mono chip, so nothing an auditor greps for disappears.
 *
 * Every label is unique so the event-type filter can offer one option per
 * code (the API filters on one exact `event_type`). A code missing here still
 * renders: `auditEventLabel` falls back to a sentence-cased form of the code.
 * `tests/unit/test_audit_event_label_parity.py` fails when a server-owned
 * event type in `backend/services/audit_event_types.py` has no label here.
 */

export const AUDIT_EVENT_TYPE_LABELS: Readonly<Record<string, string>> = {
  ACTIVATION_STAGE: 'Activation staged',
  ADMIN_OPERATION_CONFLICT: 'Admin job refused (already running)',
  ADMIN_OPERATION_COOLDOWN: 'Admin job refused (cooldown)',
  ADMIN_OPERATION_FAILED: 'Admin job failed',
  ADMIN_OPERATION_REQUESTED: 'Admin job requested',
  ADMIN_OPERATION_RUN: 'Admin job started',
  APPROVE: 'Outreach approved',
  CALL_DISPOSITION: 'Call disposition recorded',
  CAMPAIGN_STATUS_UPDATE: 'Campaign status changed',
  DELETE_DRAFT: 'Outreach draft removed',
  DRAFT_OUTREACH: 'Outreach draft created',
  FORCE_DEGRADED: 'Degraded mode forced',
  GENIE_ACTION_CREATE_DRAFT_CAMPAIGN: 'Genie drafted a campaign',
  GENIE_ANSWER_EXPORT: 'Genie answer exported',
  GENIE_FEEDBACK: 'Genie feedback submitted',
  GENIE_FEEDBACK_INTENT: 'Genie feedback started',
  GENIE_REFUSAL_REPORT: 'Genie refusal reported',
  GENIE_TURN_CANCELLED: 'Genie turn cancelled',
  GROWTH_AGENT_COMPOSE: 'Growth agent composed a draft',
  GROWTH_AGENT_NOTIFICATION_DRAFT: 'Growth agent notification drafted',
  GROWTH_AGENT_PLAN_STEP: 'Growth agent plan step',
  GROWTH_AGENT_RUN: 'Growth agent run',
  HOLD: 'Outreach held (legacy code)',
  LEAD_ASSIGN: 'Lead assigned',
  LEAD_ASSIGNMENT_STATUS: 'Lead assignment status changed',
  LEAD_DISTRIBUTE: 'Leads distributed',
  LEAD_EXPORT: 'Lead list exported',
  LEAD_OUTCOME: 'Lead outcome recorded',
  LEAD_OUTCOME_RECORDED: 'Loan officer outcome recorded',
  OUTREACH_APPROVE: 'Outreach approved (legacy code)',
  OUTREACH_HOLD: 'Outreach held',
  OUTREACH_REJECT: 'Outreach rejected',
  PORTFOLIO_CREATE: 'Portfolio created',
  PROPERTY_LOOKUP: 'Property looked up',
  RECOMMEND_OFFER: 'Offer recommended',
  REJECT: 'Outreach rejected (legacy code)',
  RUN_GENIE: 'Genie analysis run',
  SAVE_DRAFT: 'Outreach draft saved',
  SAVE_LEAD: 'Lead saved',
  UNSAVE_LEAD: 'Lead removed from saved',
  VIEW_BORROWER: 'Borrower reviewed',
  VIEW_BORROWER_PROOF: 'Borrower proof reviewed',
  VIEW_LEADS: 'Lead queue reviewed',
};

/** The no-op option of the event-type filter. */
export const ALL_EVENT_TYPES_OPTION = 'All event types';

const EVENT_CODE_RE = /^[A-Z][A-Z0-9_]{0,127}$/;

/** `DRAFT_OUTREACH` -> `Draft outreach`; the fallback for an unlabelled code. */
export function sentenceCaseCode(code: string): string {
  const words = code.trim().toLowerCase().split(/[_.\s-]+/).filter(Boolean);
  if (words.length === 0) return '';
  const [first, ...rest] = words;
  return [`${first.charAt(0).toUpperCase()}${first.slice(1)}`, ...rest].join(' ');
}

/** The raw code a row is filed under: its event type, else its action. */
export function auditEventCode(event: { event_type?: string | null; action: string }): string {
  const eventType = (event.event_type ?? '').trim();
  return eventType || event.action.trim();
}

/** The human label for a ledger row (never empty). */
export function auditEventLabel(event: { event_type?: string | null; action: string }): string {
  const eventType = (event.event_type ?? '').trim().toUpperCase();
  const known = AUDIT_EVENT_TYPE_LABELS[eventType];
  if (known) return known;
  return sentenceCaseCode(eventType || event.action) || 'Activity recorded';
}

/** The label for one event-type code, as the filter shows it. */
export function eventTypeCodeLabel(code: string): string {
  return AUDIT_EVENT_TYPE_LABELS[code] ?? (sentenceCaseCode(code) || code);
}

/**
 * Event-type filter options: the no-op first, then every labelled code in
 * label order. An applied code the registry does not know (a hand-edited or
 * older deep link) is appended so the control can still show it.
 */
export function eventTypeFilterOptions(appliedCode: string | null): string[] {
  const labels = Object.values(AUDIT_EVENT_TYPE_LABELS).sort((a, b) => a.localeCompare(b));
  const options = [ALL_EVENT_TYPES_OPTION, ...labels];
  if (appliedCode && !(appliedCode in AUDIT_EVENT_TYPE_LABELS)) {
    options.push(eventTypeCodeLabel(appliedCode));
  }
  return options;
}

/** The code an event-type filter option stands for, or null for the no-op. */
export function eventTypeCodeForOption(option: string, appliedCode: string | null): string | null {
  if (option === ALL_EVENT_TYPES_OPTION) return null;
  const hit = Object.entries(AUDIT_EVENT_TYPE_LABELS).find(([, label]) => label === option);
  if (hit) return hit[0];
  if (appliedCode && eventTypeCodeLabel(appliedCode) === option) return appliedCode;
  return null;
}

/** Whether a string can be an event-type code (URL parse guard). */
export function isAuditEventCode(value: string): boolean {
  return EVENT_CODE_RE.test(value);
}
