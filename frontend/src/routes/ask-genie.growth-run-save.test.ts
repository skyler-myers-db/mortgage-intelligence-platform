/**
 * Save as watchlist failure copy and eligibility (audit 2026-09-21 `genie-09`
 * part 1). The card never prints an error's own text: a 422 body names schema
 * fields and patterns. The rendered behaviour is pinned in
 * ask-genie.growth-run-card.test.tsx and ask-genie.growth-agent.saved-watchlists.test.tsx.
 */
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads the shared case table under Vitest only.
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { ApiError } from '../lib/api';
import { CLIENT_FAILURE_MESSAGES } from '../lib/apiFailure';
import {
  RUN_SAVE_FAILED_MESSAGE,
  RUN_SAVE_REFUSED_MESSAGE,
  canonicalWatchlistName,
  isSavableGrowthRun,
  runSaveFailureMessage,
} from './ask-genie.growth-run-save';
import type { GrowthAgentRunResponse } from '../types';

type SaveFields = Pick<GrowthAgentRunResponse, 'workflow' | 'tool_result_hash' | 'audit_event_id'>;

const RUN: SaveFields = {
  workflow: {
    id: 'daily_refi_brief',
    title: 'Daily Refi Opportunity Brief',
    objective: 'Find borrowers with rate-spread economics worth reviewing today.',
    trigger_label: 'In the Money',
    action_label: 'Open eligible refi subset',
    source_assets: ['mip.gold.borrower_360'],
    default_route: '/lead-queue?segment=itm',
    proof_points: [],
    cadence_options: ['daily', 'weekly'],
  },
  tool_result_hash: 'a'.repeat(64),
  audit_event_id: 'audit-11111111-1111-4111-8111-111111111111',
};

const PATH = '/api/growth-agent/runs/11111111-1111-4111-8111-111111111111/monitors';

describe('runSaveFailureMessage', () => {
  it.each([400, 403, 404, 409, 422, 429])('a %i is the refused sentence', (status) => {
    const error = new ApiError(`server text for ${status}: tool_result_hash`, { path: PATH, status });
    expect(runSaveFailureMessage(error)).toBe(RUN_SAVE_REFUSED_MESSAGE);
  });

  it('an ended session keeps the transport copy, not the refused sentence', () => {
    const error = new ApiError(CLIENT_FAILURE_MESSAGES.session_expired, {
      path: PATH,
      status: 401,
      reason: 'session_expired',
    });
    expect(runSaveFailureMessage(error)).toBe(CLIENT_FAILURE_MESSAGES.session_expired);
  });

  it('an unreachable server keeps the transport copy', () => {
    const error = new ApiError(CLIENT_FAILURE_MESSAGES.unreachable, { path: PATH, status: null, reason: 'unreachable' });
    expect(runSaveFailureMessage(error)).toBe(CLIENT_FAILURE_MESSAGES.unreachable);
  });

  it.each([
    ['a 503', new ApiError('Lakebase detail', { path: PATH, status: 503, dependency: 'lakebase' })],
    ['a 500', new ApiError('Internal Server Error', { path: PATH, status: 500 })],
    ['a plain Error', new Error('boom')],
    ['a non-error', 'boom'],
  ])('%s is the fixed retry sentence, never its own text', (_label, error) => {
    expect(runSaveFailureMessage(error)).toBe(RUN_SAVE_FAILED_MESSAGE);
  });
});

describe('isSavableGrowthRun', () => {
  it('accepts a ledger-backed reviewed-workflow run', () => {
    expect(isSavableGrowthRun(RUN)).toBe(true);
  });

  it.each([
    ['live analysis', { workflow: { ...RUN.workflow, id: 'live_analysis' } }],
    ['an unknown workflow', { workflow: { ...RUN.workflow, id: 'something_else' } }],
    ['a 32-hex hash', { tool_result_hash: 'c'.repeat(32) }],
    ['an upper-case hash', { tool_result_hash: 'A'.repeat(64) }],
    ['no audit row', { audit_event_id: null }],
  ])('refuses %s', (_label, overrides) => {
    // 'live_analysis' is in the server's workflow Literal but not the TS union.
    expect(isSavableGrowthRun({ ...RUN, ...overrides } as unknown as SaveFields)).toBe(false);
  });
});

interface WatchlistNameCase {
  id: string;
  title: string;
  states: string[];
  segment_codes: string[] | null;
  segment_mode: string | null;
  expected: string;
}

// W5d growth-watchlists cut 4a: the same table backend default_watchlist_name
// reads (tests/unit/test_watchlist_canonical_name.py).
const NAME_CASES = (JSON.parse(
  readFileSync(new URL('../../../tests/fixtures/watchlist_name_cases.json', import.meta.url), 'utf8'),
) as { cases: WatchlistNameCase[] }).cases;

describe('canonicalWatchlistName', () => {
  it.each(NAME_CASES.map((entry) => [entry.id, entry] as const))('%s', (_id, entry) => {
    const custom = entry.segment_codes
      ? { mode: entry.segment_mode ?? 'any', segmentCodes: entry.segment_codes }
      : undefined;
    expect(canonicalWatchlistName(entry.title, entry.states, custom)).toBe(entry.expected);
  });

  it('never reorders the caller\'s arrays', () => {
    const states = ['TX', 'IL'];
    const segmentCodes = ['listed', 'itm'];
    canonicalWatchlistName('Custom Segment Workflow', states, { mode: 'any', segmentCodes });
    expect(states).toEqual(['TX', 'IL']);
    expect(segmentCodes).toEqual(['listed', 'itm']);
  });
});
