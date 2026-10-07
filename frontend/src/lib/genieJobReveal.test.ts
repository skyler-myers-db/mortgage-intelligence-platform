/**
 * The verified-sections reveal, keyed to its job (audit 2026-09-21 genie-01
 * phase 1b; W5c folds it into the in-flight store): the floor, keeping the
 * sections on a null re-send, a terminal status changing nothing, and a
 * status of another job starting from nothing. Moved from the deleted
 * lib/genieVerifiedReveal side channel, whose clears now come from the
 * in-flight turn's own removal (genieInFlightTurn.cancel.test.ts).
 */
import { describe, expect, it } from 'vitest';
import type { GenieCompletionJobStatus } from '../types/genieJobs';
import { GENIE_REVEAL_FLOOR, genieRevealCountLine, nextJobReveal } from './genieJobReveal';

const JOB = '0a1b2c3d-0000-4000-8000-0000000000aa';
const OTHER = '0a1b2c3d-0000-4000-8000-0000000000bb';
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

describe('nextJobReveal', () => {
  it('counts below the floor and shows sections from it, keeping them on a null re-send', () => {
    const counted = nextJobReveal(null, status({ verified_sections: 2, sections_rev: 1 }));
    expect(counted).toEqual({ jobId: JOB, verified: 2, partsPlanned: 7, rev: 1, sections: null });

    const three = [0, 1, 2].map(section);
    const shown = nextJobReveal(
      counted,
      status({ verified_sections: GENIE_REVEAL_FLOOR, sections_rev: 2, revealed_sections: three }),
    );
    expect(shown.sections).toEqual(three);
    expect(shown.rev).toBe(2);

    const kept = nextJobReveal(shown, status({ verified_sections: 3, sections_rev: 2, revealed_sections: null }));
    expect(kept.sections).toEqual(three);

    // A second sweep in the same turn starts over below the floor.
    expect(nextJobReveal(kept, status({ verified_sections: 0, sections_rev: 3 })).sections).toBeNull();
  });

  it('a missing count is zero and a missing revision keeps the held one', () => {
    const held = nextJobReveal(null, status({ verified_sections: 1, sections_rev: 4 }));

    expect(nextJobReveal(held, status())).toMatchObject({ verified: 0, rev: 4, sections: null });
  });

  it('a terminal status changes nothing', () => {
    const shown = nextJobReveal(
      null,
      status({ verified_sections: 3, sections_rev: 2, revealed_sections: [0, 1, 2].map(section) }),
    );

    expect(nextJobReveal(shown, status({ status: 'succeeded', stage: 'done', terminal: true }))).toBe(shown);
  });

  it('a status of another job starts from nothing, never with this job\'s sections', () => {
    const shown = nextJobReveal(
      null,
      status({ verified_sections: 3, sections_rev: 2, revealed_sections: [0, 1, 2].map(section) }),
    );

    const other = nextJobReveal(shown, status({ job_id: OTHER, verified_sections: 3, sections_rev: 2 }));

    expect(other).toEqual({ jobId: OTHER, verified: 3, partsPlanned: 7, rev: 2, sections: null });
  });
});

describe('genieRevealCountLine', () => {
  it('names the verified count, with the planned total when known', () => {
    const reveal = nextJobReveal(null, status({ verified_sections: 1, sections_rev: 1 }));

    expect(genieRevealCountLine(reveal)).toBe('Partial research · 1 of 7 sub-analyses verified so far');
    expect(genieRevealCountLine({ ...reveal, partsPlanned: null })).toBe(
      'Partial research · 1 sub-analyses verified so far',
    );
    expect(genieRevealCountLine({ ...reveal, verified: 0 })).toBeNull();
  });
});
