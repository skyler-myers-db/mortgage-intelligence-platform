/**
 * The team comparison's model (2026-09-21 audit critic-10 item 6b). Signed
 * changes go through lib/formatters signedCount / signedPct, so the expected
 * strings are built with them too (their minus sign is that module's).
 */
import { describe, expect, it } from 'vitest';
import { signedCount, signedPct } from '../lib/formatters';
import type { SalesConversionResponse } from '../types';
import { countDelta, rateDelta, teamTotals } from './analytics.sales-ops.team';

const row = (group_key: string, calls: number, contacts: number, starts: number): SalesConversionResponse['rows'][number] => ({
  group_key,
  calls_attempted: calls,
  contacts_reached: contacts,
  callbacks_scheduled: 0,
  applications_started: starts,
  unique_leads_contacted: calls,
  unique_contacts_reached: contacts,
  unique_application_starts: starts,
  application_start_rate: 0,
});

const response = (rows: SalesConversionResponse['rows']): SalesConversionResponse => ({
  from_date: '2026-07-08',
  to_date: '2026-07-14',
  group_by: 'cohort',
  rows,
});

describe('teamTotals', () => {
  it('reads the one all_cohorts row, a measured zero for an empty window, and null for a failed read', () => {
    expect(teamTotals(response([row('all_cohorts', 220, 74, 12)]))).toEqual({ calls: 220, contacts: 74, starts: 12, rate: 12 / 74 });
    expect(teamTotals(response([]))).toEqual({ calls: 0, contacts: 0, starts: 0, rate: null });
    expect(teamTotals(null)).toBeNull();
  });
});

describe('countDelta / rateDelta', () => {
  it('signs a change, says new from zero and no change when equal', () => {
    expect(countDelta(220, 180, 7)).toEqual({ text: `${signedCount(40)} vs previous 7 days`, dir: 'up' });
    expect(countDelta(150, 180, 7)).toEqual({ text: `${signedCount(-30)} vs previous 7 days`, dir: 'down' });
    expect(countDelta(12, 0, 7)).toEqual({ text: 'new vs previous 7 days', dir: 'up' });
    expect(countDelta(0, 0, 7)).toEqual({ text: 'no change vs previous 7 days', dir: 'flat' });
    expect(countDelta(5, 5, 1)).toEqual({ text: 'no change vs previous day', dir: 'flat' });
  });

  it('reads a rate change in points', () => {
    expect(rateDelta(0.162, 0.15, 30)).toEqual({ text: `${signedPct(1.2, 1).slice(0, -1)} pts vs previous 30 days`, dir: 'up' });
    expect(rateDelta(0.15, 0.162, 30)).toEqual({ text: `${signedPct(-1.2, 1).slice(0, -1)} pts vs previous 30 days`, dir: 'down' });
    expect(rateDelta(0.15, 0.15, 30)).toEqual({ text: 'no change vs previous 30 days', dir: 'flat' });
    expect(rateDelta(0.15, null, 30)).toEqual({ text: 'new vs previous 30 days', dir: 'up' });
    expect(rateDelta(null, 0.15, 30)).toBeNull();
  });
});
