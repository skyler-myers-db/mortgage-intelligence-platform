/**
 * The verified-sections reveal store (audit 2026-09-21 genie-01 phase 1b):
 * begin, publish and the floor, keeping sections on a null re-send, the
 * deferred clear on settle and the immediate clear on end, ignoring a stale
 * job, and the actor reset (the w5-identity-reset document model: a new
 * document is pending, then its first observation; every reason except
 * 'opened' clears).
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { GenieCompletionJobStatus } from '../types/genieJobs';
import { _resetActorScopeForTests, _setResetDocumentForTests, NOBODY, observeActor, subscribeActorScope } from './actorScope';
import {
  GENIE_REVEAL_FLOOR,
  beginGenieReveal,
  endGenieReveal,
  genieRevealSnapshot,
  publishGenieReveal,
} from './genieVerifiedReveal';

const JOB = '0a1b2c3d-0000-4000-8000-0000000000aa';
const section = (n: number) => ({ title: `Part ${n}`, question: `Part ${n}?`, answer: 'Illinois leads.' });

function status(partial: Partial<GenieCompletionJobStatus> = {}): GenieCompletionJobStatus {
  return {
    kind: 'genie_completion_job',
    job_id: JOB,
    status: 'running',
    stage: 'researching',
    stage_label: 'Running governed sub-analyses',
    parts_done: 2,
    parts_planned: 7,
    terminal: false,
    failed: false,
    error_hint: null,
    response: null,
    ...partial,
  };
}

afterEach(() => {
  endGenieReveal(JOB, 'ended');
  vi.useRealTimers();
});

describe('genieVerifiedReveal', () => {
  it('counts below the floor and shows sections from it, keeping them on a null re-send', () => {
    beginGenieReveal(JOB);
    expect(genieRevealSnapshot()).toMatchObject({ jobId: JOB, verified: 0, sections: null, rev: null });

    expect(publishGenieReveal(status({ verified_sections: 2, sections_rev: 1 }))).toBe(1);
    expect(genieRevealSnapshot()).toMatchObject({ verified: 2, partsPlanned: 7, sections: null });

    const three = [0, 1, 2].map(section);
    expect(publishGenieReveal(status({ verified_sections: GENIE_REVEAL_FLOOR, sections_rev: 2, revealed_sections: three }))).toBe(2);
    expect(genieRevealSnapshot()?.sections).toEqual(three);

    expect(publishGenieReveal(status({ verified_sections: 3, sections_rev: 2, revealed_sections: null }))).toBe(2);
    expect(genieRevealSnapshot()?.sections).toEqual(three);

    // A second sweep in the same turn starts over below the floor.
    publishGenieReveal(status({ verified_sections: 0, sections_rev: 3 }));
    expect(genieRevealSnapshot()?.sections).toBeNull();
  });

  it('ignores a stale job and a second begin of the same job', () => {
    beginGenieReveal(JOB);
    publishGenieReveal(status({ verified_sections: 3, sections_rev: 1, revealed_sections: [0, 1, 2].map(section) }));
    beginGenieReveal(JOB);
    expect(genieRevealSnapshot()?.sections).toHaveLength(3);

    expect(publishGenieReveal(status({ job_id: 'another-job', verified_sections: 0 }))).toBeNull();
    expect(genieRevealSnapshot()?.sections).toHaveLength(3);
    endGenieReveal('another-job', 'ended');
    expect(genieRevealSnapshot()?.jobId).toBe(JOB);
  });

  it('clears a settled job on the next task and an ended one at once', () => {
    vi.useFakeTimers();
    beginGenieReveal(JOB);
    publishGenieReveal(status({ verified_sections: 3, sections_rev: 1, revealed_sections: [0, 1, 2].map(section) }));
    publishGenieReveal(status({ status: 'succeeded', stage: 'done', terminal: true }));
    expect(genieRevealSnapshot()?.sections).toHaveLength(3);

    endGenieReveal(JOB, 'settled');
    expect(genieRevealSnapshot()).not.toBeNull();
    vi.runAllTimers();
    expect(genieRevealSnapshot()).toBeNull();

    beginGenieReveal(JOB);
    endGenieReveal(JOB, 'ended');
    expect(genieRevealSnapshot()).toBeNull();
  });

  it('survives the document opening and clears on every other actor-scope reason', () => {
    const reasons: string[] = [];
    const off = subscribeActorScope(({ reason }) => reasons.push(`${reason}:${genieRevealSnapshot() ? 'kept' : 'cleared'}`));
    try {
      // A new document is pending, then its first observation opens it.
      _resetActorScopeForTests({ status: 'pending', owner: 'actor-a' });
      // The mid-test gate reset restores the browser's resetDocument
      // (window.location.replace): re-install a stub before the proven change.
      const resetDocument = vi.fn();
      _setResetDocumentForTests(resetDocument);
      beginGenieReveal(JOB);
      observeActor({ key: 'actor-a' });
      expect(reasons.slice(-1)).toEqual(['opened:kept']);

      observeActor({ key: 'actor-b' });
      expect(genieRevealSnapshot()).toBeNull();
      expect(resetDocument, 'a proven change after a real open resets the document').toHaveBeenCalledOnce();

      // That document is gone: B's new document is pending, then opens, and
      // a sign-out there closes it.
      _resetActorScopeForTests({ status: 'pending', owner: 'actor-b' });
      _setResetDocumentForTests(vi.fn());
      observeActor({ key: 'actor-b' });
      beginGenieReveal(JOB);
      observeActor({ key: null });
      expect(reasons.slice(-1)).toEqual(['closed:cleared']);
      expect(reasons.filter((r) => !r.startsWith('opened')).every((r) => r.endsWith(':cleared'))).toBe(true);
    } finally {
      off();
      _resetActorScopeForTests({ status: 'open', owner: NOBODY });
    }
  });
});
