// @vitest-environment happy-dom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement } from 'react';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { FollowupItem, FollowupList } from './apiClients/followUps';
import { mount } from '../test/render';

const listFetch = vi.hoisted(() => ({ fn: vi.fn() }));
vi.mock('./apiClients/followUps', () => ({
  fetchFollowupList: (windowDays: number, signal?: AbortSignal) => listFetch.fn(windowDays, signal),
  postLeadSnooze: () => Promise.reject(new Error('not used')),
}));

import { followUpsSummaryEnabled, followUpsSummaryQueryOptions, FOLLOW_UPS_POLL_MS } from './apiClients/followUpsSummary';
import { dueQueueHandoff, groupFollowups, QUEUE_HANDOFF_MAX, sectionFor, useFollowUpsList } from './followUps';
import { endOfLocalDay, hasDueToday, startOfLocalDay } from './followUpsDay';
import type { SessionResponse } from '../types';

declare const process: { env: Record<string, string | undefined> };

/**
 * Follow-ups due (D-shell-deviations-a; shell-09, wow-power-3): the rail's
 * summary poll options and gate, the tray list's open-only read, and the
 * viewer's local-day buckets, under a FIXED time zone so midnight is real.
 */

const TZ = 'America/Chicago';
let previousTz: string | undefined;
beforeAll(() => {
  previousTz = process.env.TZ;
  process.env.TZ = TZ;
});
afterAll(() => {
  process.env.TZ = previousTz;
});

/** 2026-10-07 21:30 in Chicago (CDT, UTC-5) = 2026-10-08T02:30Z: the UTC day is already tomorrow. */
const NOW = Date.parse('2026-10-08T02:30:00Z');

function item(overrides: Partial<FollowupItem>): FollowupItem {
  return {
    kind: 'callback',
    borrower_id: 'B-0000000000001',
    due_at: null,
    since_at: '2026-10-01T15:00:00Z',
    attempt_number: 1,
    owner_label: null,
    owner_state: null,
    contactable: true,
    contact_block_reason: null,
    earliest_recontact_at: null,
    woke_on_signal: false,
    ...overrides,
  };
}

const SESSION: SessionResponse = {
  can_access_admin: false, can_approve: false, can_read_audit: false, presenter_mode: false,
  actor_email: 'lo01@summit.example',
};

describe('the rail summary poll', () => {
  it('polls once a minute, never in the background or on focus, and never retries', () => {
    const options = followUpsSummaryQueryOptions(true);
    expect(options.queryKey).toEqual(['mip', 'followups', 'summary']);
    expect(FOLLOW_UPS_POLL_MS).toBe(60_000);
    expect(options.refetchInterval).toBe(60_000);
    expect(options.refetchIntervalInBackground).toBe(false);
    expect(options.refetchOnWindowFocus).toBe(false);
    expect(options.retry).toBe(false);
    expect(followUpsSummaryQueryOptions(false).enabled).toBe(false);
  });

  it('is gated on a session that names an actor and is not expired', () => {
    expect(followUpsSummaryEnabled(SESSION, 'online')).toBe(true);
    expect(followUpsSummaryEnabled(SESSION, undefined), 'no HealthProvider yet').toBe(true);
    expect(followUpsSummaryEnabled(SESSION, 'session_expired')).toBe(false);
    expect(followUpsSummaryEnabled({ ...SESSION, actor_email: null }, 'online')).toBe(false);
    expect(followUpsSummaryEnabled({ ...SESSION, actor_email: '' }, 'online')).toBe(false);
    expect(followUpsSummaryEnabled(undefined, 'online'), 'session pending or errored').toBe(false);
  });
});

describe('the viewer local day', () => {
  it('starts and ends at local midnight, not UTC midnight', () => {
    expect(new Date(startOfLocalDay(NOW)).toISOString()).toBe('2026-10-07T05:00:00.000Z');
    expect(new Date(endOfLocalDay(NOW)).toISOString()).toBe('2026-10-08T05:00:00.000Z');
  });

  it('shows the dot iff the next due time is before the next local midnight', () => {
    expect(hasDueToday('2026-10-08T04:59:00Z', NOW), '23:59 tonight').toBe(true);
    expect(hasDueToday('2026-10-08T05:00:00Z', NOW), 'midnight is tomorrow').toBe(false);
    expect(hasDueToday('2026-10-06T12:00:00Z', NOW), 'overdue').toBe(true);
    expect(hasDueToday(null, NOW)).toBe(false);
    expect(hasDueToday('garbage', NOW)).toBe(false);
  });
});

describe('the tray sections', () => {
  it('buckets by the local day across midnight and collapses past the 14-day horizon', () => {
    const at = (due: string) => sectionFor(item({ due_at: due }), NOW);
    expect(at('2026-10-07T05:30:00Z'), 'earlier today, local').toBe('today');
    expect(at('2026-10-08T04:00:00Z'), 'late tonight; already "tomorrow" in UTC').toBe('today');
    expect(at('2026-10-07T04:59:00Z'), 'last night, local').toBe('overdue');
    expect(at('2026-10-08T05:00:00Z'), 'local midnight').toBe('week');
    expect(at('2026-09-24T03:00:00Z'), 'inside 14 days').toBe('overdue');
    expect(at('2026-09-24T02:00:00Z'), 'more than 14 days ago').toBe('older');
  });

  it('routes requested, new and withheld rows to their own groups', () => {
    expect(sectionFor(item({ kind: 'requested_follow_up', due_at: '2026-10-01T00:00:00Z' }), NOW)).toBe('requested');
    expect(sectionFor(item({ kind: 'new_assignment' }), NOW)).toBe('assigned');
    for (const kind of ['callback', 'approval_follow_up', 'requested_follow_up', 'new_assignment'] as const) {
      expect(sectionFor(item({ kind, contactable: false, contact_block_reason: 'suppressed' }), NOW)).toBe('withheld');
    }
  });

  it('hands at most 20 Overdue and Today ids to the queue, contactable only', () => {
    const due = Array.from({ length: 25 }, (_, index) =>
      item({ borrower_id: `B-${String(index).padStart(13, '0')}`, due_at: '2026-10-07T20:00:00Z' }));
    const withheld = item({ borrower_id: 'B-WITHHELD00001', due_at: '2026-10-07T20:00:00Z', contactable: false, contact_block_reason: 'suppressed' });
    const week = item({ borrower_id: 'B-WEEK000000001', due_at: '2026-10-10T20:00:00Z' });

    const handoff = dueQueueHandoff(groupFollowups([withheld, week, ...due], NOW));

    expect(handoff.ids).toHaveLength(QUEUE_HANDOFF_MAX);
    expect(QUEUE_HANDOFF_MAX).toBe(20);
    expect(handoff.more).toBe(true);
    expect(handoff.ids).not.toContain('B-WITHHELD00001');
    expect(handoff.ids).not.toContain('B-WEEK000000001');
    expect(dueQueueHandoff(groupFollowups(due.slice(0, 3), NOW))).toEqual({ ids: due.slice(0, 3).map((row) => row.borrower_id), more: false });
  });
});

describe('the tray list read', () => {
  let client: QueryClient;
  beforeEach(() => {
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    listFetch.fn.mockReset();
    listFetch.fn.mockResolvedValue({ items: [], generated_at: '2026-10-08T02:30:00Z', truncated: false } satisfies FollowupList);
  });
  afterEach(() => client.clear());

  function Probe({ open }: { open: boolean }) {
    useFollowUpsList(open);
    return null;
  }

  it('reads only while the tray is open, with a 60 s staleness', async () => {
    await mount(createElement(QueryClientProvider, { client }, createElement(Probe, { open: false })));
    expect(listFetch.fn).not.toHaveBeenCalled();

    await mount(createElement(QueryClientProvider, { client }, createElement(Probe, { open: true })));
    await vi.waitFor(() => expect(listFetch.fn).toHaveBeenCalledTimes(1));
    expect(listFetch.fn.mock.calls[0][0]).toBe(7);
    const query = client.getQueryCache().find({ queryKey: ['mip', 'followups', 'list', 7] });
    expect((query?.options as { staleTime?: number }).staleTime).toBe(60_000);
  });
});
