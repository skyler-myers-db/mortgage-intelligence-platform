/**
 * @vitest-environment happy-dom
 *
 * Queue context (audit 2026-09-21 shell-04): the Lead Queue's search string
 * and ordered masked ids, carried to the dossier through Link state with a
 * sessionStorage fallback and cleared on an actor change.
 */

import { beforeEach, describe, expect, it } from 'vitest';
import { clearActorScopedBrowserState } from './actorScopedBrowserState';
import { MASKED_BORROWER_ID_RE } from './routeMeta';
import {
  QUEUE_CONTEXT_STORAGE_KEY,
  QUEUE_MASKED_ID_RE,
  clearQueueContext,
  queueCrumbLabel,
  queueHref,
  resolveQueueContext,
} from './queueContext';
import { publishQueueContext, queueFilterLabel } from './queueContextPublish';
import { queuePosition } from './queuePosition';

const IDS = ['B-0000000000001', 'B-0000000000002', 'B-0000000000003'];

describe('queue context', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    clearQueueContext();
  });

  it('filters on the canonical masked-id shape (kept local to stay out of a chunk split)', () => {
    expect(QUEUE_MASKED_ID_RE.source).toBe(MASKED_BORROWER_ID_RE.source);
    expect(QUEUE_MASKED_ID_RE.flags).toBe(MASKED_BORROWER_ID_RE.flags);
  });

  it('keeps only masked borrower ids, in rank order', () => {
    const context = publishQueueContext({
      search: '?state=IL',
      label: 'IL',
      ids: ['B-0000000000001', 'jane@example.com', 'B-short', IDS[1], '<script>'],
    });
    expect(context?.ids).toEqual([IDS[0], IDS[1]]);
    const stored = JSON.parse(window.sessionStorage.getItem(QUEUE_CONTEXT_STORAGE_KEY) ?? '{}');
    expect(stored.ids).toEqual([IDS[0], IDS[1]]);
    expect(JSON.stringify(stored)).not.toContain('@');
  });

  it('prefers the history entry own context and falls back to the published queue that lists the borrower', () => {
    const first = publishQueueContext({ search: '?state=IL', label: 'IL', ids: IDS });
    const second = publishQueueContext({ search: '?state=TX', label: 'TX', ids: [IDS[2]] });
    expect(second?.epoch).toBe(first?.epoch);
    // Back to a dossier opened from the IL queue: its own entry wins.
    expect(resolveQueueContext({ queue: first }, IDS[0])?.search).toBe('?state=IL');
    // No state (reload without history state, a new tab): the stored queue,
    // only when it lists this borrower.
    expect(resolveQueueContext(null, IDS[2])?.search).toBe('?state=TX');
    expect(resolveQueueContext(null, IDS[0])).toBeNull();
    // A carried context that does not list the borrower is not its queue.
    expect(resolveQueueContext({ queue: first }, 'B-9999999999999')).toBeNull();
  });

  it('rejects malformed or foreign state', () => {
    const live = publishQueueContext({ search: '?state=IL', label: 'IL', ids: IDS });
    expect(resolveQueueContext({ queue: { ...live, search: 'javascript:alert(1)' } }, IDS[0])?.search).toBe('?state=IL');
    expect(resolveQueueContext({ queue: { ...live, epoch: 'another-session' } }, IDS[0])?.epoch).toBe(live?.epoch);
    expect(resolveQueueContext({ queue: { ...live, epoch: 'another-session' } }, 'B-9999999999999')).toBeNull();
  });

  it('an actor change clears the stored queue and invalidates the copies history entries still hold', () => {
    const before = publishQueueContext({ search: '?state=IL', label: 'IL', ids: IDS });
    clearActorScopedBrowserState();
    expect(window.sessionStorage.getItem(QUEUE_CONTEXT_STORAGE_KEY)).toBeNull();
    expect(resolveQueueContext({ queue: before }, IDS[0])).toBeNull();
    const after = publishQueueContext({ search: '', label: '', ids: IDS });
    expect(after?.epoch).not.toBe(before?.epoch);
    expect(resolveQueueContext({ queue: before }, IDS[0])?.epoch).toBe(after?.epoch);
  });

  it('positions a borrower and links back to the exact queue', () => {
    const context = publishQueueContext({ search: '?state=IL&segment=itm', label: queueFilterLabel(['IL', 'In the Money', null, '']), ids: IDS });
    if (!context) throw new Error('context not published');
    expect(queuePosition(context, IDS[1])).toEqual({ position: 2, total: 3, previous: IDS[0], next: IDS[2] });
    expect(queuePosition(context, IDS[0])).toMatchObject({ position: 1, previous: null });
    expect(queuePosition(context, IDS[2])).toMatchObject({ position: 3, next: null });
    expect(queuePosition(context, 'B-9999999999999')).toBeNull();
    expect(queueHref(context)).toBe('/lead-queue?state=IL&segment=itm');
    expect(queueCrumbLabel(context)).toBe('Lead Queue · IL · In the Money');
    expect(queueHref(null)).toBe('/lead-queue');
    expect(queueCrumbLabel(null)).toBe('Lead Queue');
  });
});
