/**
 * The Stop copy ruling (audit 2026-09-21 `genie-03`, 2026-09-30): every
 * outcome x status the server can answer maps to its exact note and to
 * whether it is announced; anything else keeps the unconfirmed note. And
 * which Stop sends a cancel at all (the W5c turn-keyed pre-cancel).
 */
import { describe, expect, it } from 'vitest';
import type { GenieJobStatusValue } from '../types/genieJobs';
import type { GenieTurnCancelOutcome } from './genieTurnCancel';
import {
  GENIE_STOP_CONFIRMED_REASON,
  GENIE_STOP_INCOMPLETE_REASON,
  GENIE_STOP_NOT_KEPT_REASON,
  GENIE_STOP_RECORDED_REASON,
  GENIE_STOP_RECORDING_REASON,
} from './genieTurnOutcome';
import { stopNoteFor, stopTargetFor } from './genieTurnStop';

const STATUSES: readonly GenieJobStatusValue[] = ['queued', 'running', 'succeeded', 'failed', 'expired', 'cancelled'];

type Row = [GenieTurnCancelOutcome, GenieJobStatusValue, string | null, boolean];

/** The ruling's table: every outcome x status. */
const TABLE: Row[] = STATUSES.flatMap((status): Row[] => [
  ['cancelled', status, GENIE_STOP_CONFIRMED_REASON, false],
  ['ended', status, GENIE_STOP_CONFIRMED_REASON, false],
  ['recorded', status, GENIE_STOP_RECORDED_REASON, true],
]).concat([
  ['recording', 'running', GENIE_STOP_RECORDING_REASON, true],
  ['recording', 'succeeded', GENIE_STOP_NOT_KEPT_REASON, true],
  ['recording', 'failed', GENIE_STOP_INCOMPLETE_REASON, true],
  ['recording', 'expired', GENIE_STOP_INCOMPLETE_REASON, true],
  ['recording', 'queued', null, false],
  ['recording', 'cancelled', null, false],
]);

describe('stopNoteFor', () => {
  it.each(TABLE)('%s / %s', (outcome, status, reason, announce) => {
    const note = stopNoteFor({ outcome, status });

    expect(note).toEqual(reason === null ? null : { reason, announce });
  });

  it('a failed or unknown cancel keeps the unconfirmed note', () => {
    expect(stopNoteFor(null)).toBeNull();
  });
});

describe('stopTargetFor', () => {
  const ids = { conversationId: 'conv-1', messageId: 'msg-1', progressToken: 'tok-1' };
  const label = '0123456789abcdef';
  const jobId = '0a1b2c3d-0000-4000-8000-000000000001';

  it('a job turn whose 202 named the job cancels that job', () => {
    expect(stopTargetFor({ asyncComplete: true, ids, jobId, questionHash: label })).toEqual({
      ids,
      jobId,
      questionHash: label,
    });
  });

  it('a job turn before its 202 (polling, or the complete held) cancels by turn', () => {
    expect(stopTargetFor({ asyncComplete: true, ids, questionHash: label })).toEqual({
      ids,
      jobId: null,
      questionHash: label,
    });
  });

  it.each([
    ['still submitting (no ids)', { asyncComplete: true, questionHash: label }],
    ['a non-job turn', { asyncComplete: false, ids, questionHash: label }],
    ['no usable label', { asyncComplete: true, ids }],
    ['no record', null],
  ] as const)('%s sends nothing', (_name, record) => {
    expect(stopTargetFor(record)).toBeNull();
  });
});

describe('the ruling copy', () => {
  it('the three too-late notes are the ruling text, verbatim', () => {
    expect(GENIE_STOP_RECORDING_REASON).toBe(
      'Too late to stop: the answer was already verified and was still being recorded. Check History in a moment; if it is not there, Ask again.',
    );
    expect(GENIE_STOP_NOT_KEPT_REASON).toBe(
      'Too late to stop: the answer was already complete, but this kind of answer is not kept in History. Ask again to see it.',
    );
    expect(GENIE_STOP_INCOMPLETE_REASON).toBe(
      'Stopped. The answer was verified, but recording it did not finish, so it may be missing from History. Ask again if you need it.',
    );
  });
});
