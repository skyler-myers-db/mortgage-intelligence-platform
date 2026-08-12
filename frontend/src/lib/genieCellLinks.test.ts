import { describe, expect, it } from 'vitest';
import { answerCohortFromActions, borrower360Path, genieCellHref, isMaskedBorrowerId, stateLeadQueuePath } from './genieCellLinks';

const BORROWER = 'B-7K2M9QX4TB3PZ';

describe('isMaskedBorrowerId', () => {
  it('accepts the canonical masked id shape', () => {
    expect(isMaskedBorrowerId(BORROWER)).toBe(true);
  });

  it.each([
    ['lowercase', 'b-7k2m9qx4tb3pz'],
    ['too short', 'B-7K2M9QX4TB3'],
    ['too long', 'B-7K2M9QX4TB3PZQ'],
    ['no prefix', '7K2M9QX4TB3PZ'],
    ['number', 12345],
    ['null', null],
  ])('rejects %s', (_label, value) => {
    expect(isMaskedBorrowerId(value)).toBe(false);
  });
});

describe('genieCellHref', () => {
  it('links a borrower_id cell to the borrower 360 route', () => {
    expect(genieCellHref('borrower_id', BORROWER)).toBe(`/borrower-360/${BORROWER}`);
  });

  it('links a state cell to the same state-filtered lead queue the map uses', () => {
    expect(genieCellHref('state', 'IL')).toBe('/lead-queue?state=IL');
    expect(genieCellHref('state', 'il')).toBe('/lead-queue?state=IL');
    expect(genieCellHref('state_code', 'TX')).toBe('/lead-queue?state=TX');
  });

  it('leaves a city cell plain when its own row carries no state', () => {
    // Fail closed. A bare name is ambiguous across states (CYPRESS is
    // CA 14,630 / TX 1), so there is no honest cohort to link to.
    expect(genieCellHref('city', 'Chicago')).toBeNull();
    expect(genieCellHref('city', 'Chicago', {}, { city: 'Chicago', n: 523010 })).toBeNull();
  });

  it('links a city cell to the (city, state) cohort when the row carries a state', () => {
    expect(genieCellHref('city', 'Chicago', {}, { city: 'Chicago', state: 'IL' })).toBe(
      '/lead-queue?cities=CHICAGO~IL',
    );
  });

  it('leaves a borrower_id that does not match the masked shape plain', () => {
    expect(genieCellHref('borrower_id', 'unknown')).toBeNull();
    expect(genieCellHref('borrower_id', null)).toBeNull();
  });

  it('leaves a state-looking value in an unrelated column plain', () => {
    expect(genieCellHref('segment_code', 'IL')).toBeNull();
  });

  it('leaves non-2-letter state values plain', () => {
    expect(genieCellHref('state', 'Illinois')).toBeNull();
  });

  it('is case-insensitive on the column name', () => {
    expect(genieCellHref('State', 'IL')).toBe('/lead-queue?state=IL');
    expect(genieCellHref('BORROWER_ID', BORROWER)).toBe(`/borrower-360/${BORROWER}`);
  });
});

describe('path builders', () => {
  it('encodes the borrower id', () => {
    expect(borrower360Path('B-ABC/DEF')).toBe('/borrower-360/B-ABC%2FDEF');
  });

  it('upper-cases the state before building the queue path', () => {
    expect(stateLeadQueuePath(' ca ')).toBe('/lead-queue?state=CA');
  });
});

// --- A row link must open the population the ROW reports ------------------
//
// Measured live on paychex 2026-08-11: the answer to "Which states have the
// highest average opportunity score among in-the-money borrowers?" rendered a
// row reading "WA — 5,847 in-the-money borrowers" whose link was
// `/lead-queue?state=WA` — all 31,114 contactable WA borrowers, ~5x the row,
// with no disclosure. The governed action next to it carried the right cohort
// the whole time; only the table cell dropped it.

describe('answer-scoped row links', () => {
  const itmAction = {
    action_type: 'open_cohort',
    criteria: {
      result_filters: {
        states: ['WA', 'TX', 'CA'],
        segment_codes: ['itm'],
        segment_mode: 'any',
      },
    },
  };

  it('carries the answer segment onto the row state link', () => {
    const cohort = answerCohortFromActions([itmAction]);
    const href = genieCellHref('state', 'WA', cohort);
    expect(href).toContain('state=WA');
    expect(href).toContain('segment=itm');
  });

  it('scopes the link to the ROW state, never the answer whole state list', () => {
    const cohort = answerCohortFromActions([itmAction]);
    const href = genieCellHref('state', 'WA', cohort) ?? '';
    expect(href).not.toContain('TX');
    expect(href).not.toContain('CA');
    expect(href).not.toContain('states=');
  });

  it('carries a numeric floor and a portfolio criterion', () => {
    const cohort = answerCohortFromActions([{
      action_type: 'open_cohort',
      criteria: {
        result_filters: {
          min_opportunity_score: 75,
          portfolio_criteria: { occupancy: 'Owner-occupied' },
        },
      },
    }]);
    const href = genieCellHref('state', 'IL', cohort) ?? '';
    expect(href).toContain('min_opportunity_score=75');
    expect(href).toContain('occupancy=Owner-occupied');
  });

  it('emits segment_codes + mode when the answer covered several segments', () => {
    const cohort = answerCohortFromActions([{
      action_type: 'open_cohort',
      criteria: { result_filters: { segment_codes: ['itm', 'equity'], segment_mode: 'all' } },
    }]);
    const href = genieCellHref('state', 'IL', cohort) ?? '';
    expect(href).toContain('segment_codes=itm%2Cequity');
    expect(href).toContain('segment_mode=all');
  });

  it('falls back to geography alone when the answer has no governed cohort', () => {
    expect(answerCohortFromActions([])).toEqual({});
    expect(answerCohortFromActions(null)).toEqual({});
    expect(genieCellHref('state', 'WA', {})).toBe('/lead-queue?state=WA');
  });

  it('ignores non-cohort actions when reading the answer filters', () => {
    const cohort = answerCohortFromActions([
      { action_type: 'save_borrowers', criteria: { result_filters: { segment_codes: ['listed'] } } },
    ]);
    expect(cohort).toEqual({});
  });

  it('does not link a borrower id through the cohort path', () => {
    const cohort = answerCohortFromActions([itmAction]);
    expect(genieCellHref('borrower_id', 'B-102FL7THC6Q3L', cohort))
      .toBe('/borrower-360/B-102FL7THC6Q3L');
  });
});

// --- A segment comparison must be actionable per row ----------------------
//
// Live sweep 2026-08-11: 14 of 23 questions answered with trusted SQL and
// offered NO governed action, because a `GROUP BY segment_code` answer has no
// single cohort — correctly, since the answer spans every segment. That left
// "which segment converts best" as a ranking the reader could not act on.
// The row IS the cohort: each row of a segment breakdown is one segment.

describe('segment row links', () => {
  it('links a segment_code cell to that segment queue', () => {
    expect(genieCellHref('segment_code', 'itm')).toBe('/lead-queue?segment=itm');
  });

  it('accepts the S1.3 overlay codes the queue can filter on', () => {
    expect(genieCellHref('segment_code', 'second_lien_itm'))
      .toBe('/lead-queue?segment=second_lien_itm');
  });

  it('does not link a segment the queue cannot filter on', () => {
    // The live metric view spells conversion segments differently from
    // borrower_360 (`heloc`, `cash_out`); a link that silently drops the
    // filter is the failure this whole module guards against.
    expect(genieCellHref('segment_code', 'heloc')).toBeNull();
    expect(genieCellHref('segment_code', 'cash_out')).toBeNull();
    expect(genieCellHref('segment_code', '')).toBeNull();
    expect(genieCellHref('segment_code', 42)).toBeNull();
  });

  it('carries the answer portfolio criteria but never its segment list', () => {
    const cohort = answerCohortFromActions([{
      action_type: 'open_cohort',
      criteria: {
        result_filters: {
          segment_codes: ['listed'],
          min_equity_pct: 35,
        },
      },
    }]);
    const href = genieCellHref('segment_code', 'itm', cohort) ?? '';
    // The ROW's segment wins — the answer's own segment list would contradict it.
    expect(href).toContain('segment=itm');
    expect(href).not.toContain('listed');
    expect(href).toContain('min_equity_pct=35');
  });
});

// A `state, segment_code, borrowers` row reports the INTERSECTION. Adversarial
// review 2026-08-11: the segment cell opened that segment NATIONALLY, turning
// "WA — itm — 5,847" into every in-the-money borrower in the country.
describe('segment links on a row that also carries geography', () => {
  const row = { state: 'WA', segment_code: 'itm', borrowers: 5847 };

  it('carries the ROW state onto the segment link', () => {
    const href = genieCellHref('segment_code', 'itm', {}, row) ?? '';
    expect(href).toContain('state=WA');
    expect(href).toContain('segment=itm');
  });

  it('still opens nationally when the row has no geography', () => {
    expect(genieCellHref('segment_code', 'itm', {}, { segment_code: 'itm', approval_rate: 0.1 }))
      .toBe('/lead-queue?segment=itm');
  });

  it('ignores a row whose state is not a usable code', () => {
    expect(genieCellHref('segment_code', 'itm', {}, { state: '_ALL', segment_code: 'itm' }))
      .toBe('/lead-queue?segment=itm');
  });

  it('takes the row state, never the answer-wide state list', () => {
    const cohort = answerCohortFromActions([{
      action_type: 'open_cohort',
      criteria: { result_filters: { states: ['WA', 'TX', 'CA'], segment_codes: ['itm'] } },
    }]);
    const href = genieCellHref('segment_code', 'itm', cohort, row) ?? '';
    expect(href).toContain('state=WA');
    expect(href).not.toContain('TX');
    expect(href).not.toContain('CA');
  });
});
