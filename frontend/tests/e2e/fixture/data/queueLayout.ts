/**
 * Lead Queue layout fixtures (wave 1b, lane queue-layout): the default
 * population with a few rows given the workflow and compliance states the
 * one-line Status cell has to rank (audit visual-01 / tables-04). Registered
 * per test with `registerQueueLayoutLeads(mockApi)`; the default registry is
 * untouched, so every other spec keeps the all-default queue.
 */
import type { LeadSummary } from '../../../../src/types';
import { json, type MockApi } from '../mockApi';
import { LEADS } from './borrowers';
import { TOTALS } from './reference';

/** Third ranked row: DNC plus an assignment, an aging warning and a logged call. */
export const DNC_LEAD: LeadSummary = {
  ...LEADS[2],
  dnc: true,
  marketing_eligible: false,
  eligibility_source: 'synthetic_seed',
  assigned_to_email: 'lo01@summit-mortgage.example',
  assigned_to_label: 'Summit LO 01',
  assignment_status: 'assigned',
  aging_days: 12,
  latest_disposition_outcome: 'called_no_answer',
  latest_disposition_at: '2026-07-13T14:05:00Z',
};

/** Fourth ranked row: Owner Link could not resolve the owner (suppressed). */
export const UNRESOLVED_OWNER_LEAD: LeadSummary = {
  ...LEADS[3],
  has_unresolved_owner: true,
  marketing_eligible: false,
  suppression_reason: 'unresolved_owner',
};

export const QUEUE_LAYOUT_LEADS: readonly LeadSummary[] = LEADS.map((lead, index) => {
  if (index === 2) return DNC_LEAD;
  if (index === 3) return UNRESOLVED_OWNER_LEAD;
  return lead;
});

/** Answer GET /api/leads with the layout population (any filter: layout specs do not filter). */
export function registerQueueLayoutLeads(mockApi: MockApi): void {
  mockApi.register<LeadSummary[]>('GET', '/api/leads', () =>
    json<LeadSummary[]>([...QUEUE_LAYOUT_LEADS], {
      headers: {
        'X-Total-Matching': String(TOTALS.contactable),
        'X-Returned-Rows': String(QUEUE_LAYOUT_LEADS.length),
      },
    }),
  );
}
