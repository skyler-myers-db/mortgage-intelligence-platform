/**
 * Follow-ups due for an ELIGIBLE fixture actor (D-shell-deviations-a;
 * shell-09, wow-power-3): the rail summary with something due, the tray list
 * (an overdue callback, one due today, one this week, a follow-up the actor
 * requested, a new assignment, an older callback and three withheld rows) and
 * the snooze answers. Every time is relative to FIXTURE_NOW (2026-07-14
 * 15:00Z, 11:00 in the harness's America/New_York), so the local-day sections
 * are stable. The default fixture actor is NOT eligible (data/shell.ts).
 */
import type { FollowupItem, FollowupList, LeadSnoozeResponse } from '../../../../src/lib/apiClients/followUps';
import type { FollowupSummary } from '../../../../src/lib/apiClients/followUpsSummary';
import type { ContractSample } from '../contractSamples';
import { json, type MockApi } from '../mockApi';
import { LEADS } from './borrowers';
import { FIXTURE_NOW } from './reference';

const NOW = Date.parse(FIXTURE_NOW);
const HOUR = 60 * 60 * 1000;
const at = (offsetHours: number) => new Date(NOW + offsetHours * HOUR).toISOString().replace('.000Z', 'Z');

const id = (index: number) => LEADS[index].borrower_id;

export const FOLLOWUP_IDS = {
  overdue: id(0),
  today: id(1),
  week: id(2),
  requested: id(3),
  assigned: id(4),
  older: id(5),
  capped: id(6),
  suppressed: id(7),
  outOfCoverage: id(8),
} as const;

function item(overrides: Partial<FollowupItem> & Pick<FollowupItem, 'kind' | 'borrower_id'>): FollowupItem {
  return {
    due_at: null,
    since_at: at(-72),
    attempt_number: null,
    owner_label: null,
    owner_state: null,
    contactable: true,
    contact_block_reason: null,
    earliest_recontact_at: null,
    woke_on_signal: false,
    ...overrides,
  };
}

export const FOLLOWUP_ITEMS: readonly FollowupItem[] = [
  item({ kind: 'callback', borrower_id: FOLLOWUP_IDS.overdue, due_at: at(-49), since_at: at(-96), attempt_number: 2 }),
  item({ kind: 'approval_follow_up', borrower_id: FOLLOWUP_IDS.today, due_at: at(4.5), since_at: at(-72) }),
  item({ kind: 'callback', borrower_id: FOLLOWUP_IDS.week, due_at: at(47), since_at: at(-24), attempt_number: 1, woke_on_signal: true }),
  item({ kind: 'requested_follow_up', borrower_id: FOLLOWUP_IDS.requested, due_at: at(-25), since_at: at(-120), owner_state: 'assigned', owner_label: 'Summit LO 02' }),
  item({ kind: 'new_assignment', borrower_id: FOLLOWUP_IDS.assigned, since_at: at(-2) }),
  item({ kind: 'callback', borrower_id: FOLLOWUP_IDS.older, due_at: at(-24 * 24), since_at: at(-24 * 26), attempt_number: 3 }),
  item({
    kind: 'callback', borrower_id: FOLLOWUP_IDS.capped, due_at: at(2), since_at: at(-48), attempt_number: 1,
    contactable: false, contact_block_reason: 'frequency_cap', earliest_recontact_at: at(24 * 14),
  }),
  item({ kind: 'approval_follow_up', borrower_id: FOLLOWUP_IDS.suppressed, due_at: at(-3), contactable: false, contact_block_reason: 'suppressed' }),
  item({ kind: 'new_assignment', borrower_id: FOLLOWUP_IDS.outOfCoverage, since_at: at(-5), contactable: false, contact_block_reason: 'not_in_coverage' }),
];

export const FOLLOWUP_SUMMARY: FollowupSummary = { eligible: true, next_due_at: at(-49) };
export const FOLLOWUP_LIST: FollowupList = { items: [...FOLLOWUP_ITEMS], generated_at: FIXTURE_NOW, truncated: false };

export function snoozedResponse(borrowerId: string, days = 3): LeadSnoozeResponse {
  return {
    borrower_id: borrowerId,
    wake_at: at(24 * days),
    wake_clamped: false,
    audit_event_id: '0f0f0f0f-0000-4000-8000-00000000f011',
  };
}

export const SNOOZE_NOT_CONTACTABLE_409 = { detail: 'not_contactable' } as const;

/**
 * Register the eligible actor's summary, list and a default snooze answer.
 * A snoozed row leaves the next list read (the server would hide it).
 */
export function registerFollowUps(mockApi: MockApi, items: readonly FollowupItem[] = FOLLOWUP_ITEMS): { snoozed: Set<string> } {
  const snoozed = new Set<string>();
  mockApi.register<FollowupSummary>('GET', '/api/sales/followups/summary', () => json(FOLLOWUP_SUMMARY));
  mockApi.register<FollowupList>('GET', '/api/sales/followups', () =>
    json({ ...FOLLOWUP_LIST, items: items.filter((row) => !snoozed.has(row.borrower_id)) }));
  mockApi.register<LeadSnoozeResponse>('POST', '/api/leads/:id/snooze', (request) => {
    snoozed.add(request.params.id);
    const days = (request.body as { snooze_days?: number } | null)?.snooze_days ?? 3;
    return json(snoozedResponse(request.params.id, days));
  });
  return { snoozed };
}

/** Every body above, for the fixture contract (tests/unit/test_e2e_fixture_contract.py). */
export function contractSamples(): ContractSample[] {
  return [
    { source: 'data/followUps.ts#FOLLOWUP_SUMMARY', method: 'GET', pattern: '/api/sales/followups/summary', body: FOLLOWUP_SUMMARY },
    { source: 'data/followUps.ts#FOLLOWUP_LIST', method: 'GET', pattern: '/api/sales/followups', query: 'window_days=7', body: FOLLOWUP_LIST },
    {
      source: 'data/followUps.ts#snoozedResponse',
      method: 'POST',
      pattern: '/api/leads/:id/snooze',
      path: `/api/leads/${FOLLOWUP_IDS.today}/snooze`,
      body: snoozedResponse(FOLLOWUP_IDS.today),
    },
    {
      source: 'data/followUps.ts#SNOOZE_NOT_CONTACTABLE_409',
      method: 'POST',
      pattern: '/api/leads/:id/snooze',
      path: `/api/leads/${FOLLOWUP_IDS.today}/snooze`,
      status: 409,
      body: SNOOZE_NOT_CONTACTABLE_409,
    },
  ];
}
