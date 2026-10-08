/**
 * Genie answers whose proof carries the genie-10 phase 2 provenance
 * (support, column, occurrences) for the lane spec
 * genie-provenance-tiles.fixture.spec.ts. Not in the default registry: a
 * spec serves them through registerGenieTurn / registerGenieJob.
 *
 * Each body is what the backend would ship: the claims verifier checks the
 * answer's OWN prose (owner ruling O2), so a cross-check note appended after
 * the check is never in the claims, and `occurrences` counts the token over
 * the checked prose only. Synthetic, state-level, guard-safe: no borrower
 * ids, names or contact fields; test data only, never a runtime overlay.
 * contractSamples() hands every body to test_e2e_fixture_contract as a 200
 * of POST /api/genie/message/complete, so the claims validate against the
 * real GenieVerifiedClaim model.
 */
import type { GenieAnswer, GenieAnswerSection, GenieVerifiedClaim, GenieVisualization } from '../../../../src/types';
import type { ContractSample } from '../contractSamples';
import { genieAnswerFixture, genieDeepAnswerFixture } from './genieTurn';
import { STATES, TOTALS } from './reference';

const fmt = (value: number) => value.toLocaleString('en-US');
const [IL, TX, CA] = STATES;
const TOP_THREE = IL.inTheMoney + TX.inTheMoney + CA.inTheMoney;

/** The keys the real server always sends that the shared fixture leaves out. */
function served(answer: GenieAnswer): GenieAnswer {
  const viz = (v: GenieVisualization | null | undefined) => (v ? { series: null, reason: null, ...v } : null);
  return {
    summary: null,
    native_visualization: null,
    reasoning_trace: [],
    refusal_reason: null,
    refusal_report_hash: null,
    ...answer,
    visualization: viz(answer.visualization),
    sections: (answer.sections ?? []).map((section) => ({
      narrative_withheld: false,
      ...section,
      visualization: viz(section.visualization),
    })),
  };
}

function cell(token: string, column: string, section: string | null = null): GenieVerifiedClaim {
  return { token, kind: 'number', derivation: 'returned_value', section, support: 'cell', column, occurrences: 1 };
}

/** (a) One cell figure, one derived total, one stated threshold. */
export const SINGLE_CELL = fmt(IL.inTheMoney);
export const SINGLE_DERIVED = fmt(TOP_THREE);
export const SINGLE_BOUND = '500';

export function provenanceSingleAnswer(): GenieAnswer {
  const base = genieAnswerFixture();
  return served(genieAnswerFixture({
    answer:
      `${IL.name} leads the footprint with ${SINGLE_CELL} prime refi candidates. ` +
      `The top three states hold ${SINGLE_DERIVED} of them, and every listed state holds more than ${SINGLE_BOUND}.`,
    proof: {
      ...base.proof,
      claims: {
        verified: 3,
        total: 3,
        items: [
          cell(SINGLE_CELL, 'in_the_money'),
          { token: SINGLE_DERIVED, kind: 'number', derivation: 'derived_from_rows', section: null, support: 'derived', column: null, occurrences: null },
          { token: SINGLE_BOUND, kind: 'number', derivation: 'bound', section: null, support: 'bound', column: null, occurrences: null },
        ],
      },
    },
  }));
}

/** (b) Two cell figures in Genie's prose, then the deterministic ranking
 *  cross-check note the adapter appends AFTER the claims check. The prose's
 *  "10" coincides with the note's "10", so the client must not mark it. */
export const DIVERGENCE_NOTE =
  "Governed cross-check: this answer's framing overlaps 3 of 10 borrowers with the canonical opportunity ranking; " +
  'both are governed views — the Lead Queue holds the operational list.';
export const DIVERGENCE_SCORE = '97';
export const DIVERGENCE_COINCIDENT = '10';

export function provenanceDivergenceAnswer(): GenieAnswer {
  const base = genieAnswerFixture();
  return served(genieAnswerFixture({
    answer:
      `The ranking's leading lead score is ${DIVERGENCE_SCORE}, and ${DIVERGENCE_COINCIDENT} of the ranked borrowers sit in ${IL.name}.` +
      `\n\n${DIVERGENCE_NOTE}`,
    table_rows: [
      { state: IL.code, borrowers: 10, top_lead_score: 97 },
      { state: TX.code, borrowers: 6, top_lead_score: 93 },
    ],
    visualization: null,
    proof: {
      ...base.proof,
      known_data_gaps: [DIVERGENCE_NOTE],
      claims: {
        verified: 2,
        total: 2,
        items: [cell(DIVERGENCE_SCORE, 'top_lead_score'), cell(DIVERGENCE_COINCIDENT, 'borrowers')],
      },
    },
  }));
}

/** (c) A sweep: summary and section figures, plus a withheld section. */
export const SWEEP_SUMMARY_CELL = fmt(IL.inTheMoney);
export const SWEEP_SUMMARY_SUM = fmt(TOTALS.inTheMoney);
export const SWEEP_SECTION_CELL = fmt(IL.inTheMoney);
export const SWEEP_CONTACTABLE_CELL = fmt(IL.contactable);
export const WITHHELD_TITLE = 'Rate spread by state';

export function provenanceSweepAnswer(): GenieAnswer {
  const deep = genieDeepAnswerFixture();
  const sections = (deep.sections ?? []) as GenieAnswerSection[];
  const [byState, contactable] = sections;
  const withheld: GenieAnswerSection = {
    title: WITHHELD_TITLE,
    question: 'How does the rate spread vary by state?',
    answer: `2 results, shown in the chart and table below: state IL ${SINGLE_CELL}; state TX ${fmt(TX.inTheMoney)}.`,
    trusted_assets: byState.trusted_assets,
    sql_query: 'SELECT state, AVG(rate_spread_bps) AS avg_rate_spread_bps FROM mip.gold.borrower_360 GROUP BY state',
    row_count: 2,
    table_rows: [
      { state: IL.code, avg_rate_spread_bps: 182 },
      { state: TX.code, avg_rate_spread_bps: 141 },
    ],
    visualization: null,
    narrative_withheld: true,
  };
  const summary = `${IL.name} alone holds ${SWEEP_SUMMARY_CELL} of the footprint's ${SWEEP_SUMMARY_SUM} prime refi candidates.`;
  return served({
    ...deep,
    summary,
    sections: [
      { ...byState, answer: `${IL.name} leads with ${SWEEP_SECTION_CELL} prime refi candidates.` },
      { ...contactable, answer: `${IL.name} has ${SWEEP_CONTACTABLE_CELL} contact-eligible borrowers.` },
      withheld,
    ],
    proof: {
      ...deep.proof,
      claims: {
        verified: 4,
        total: 4,
        items: [
          cell(SWEEP_SUMMARY_CELL, 'in_the_money'),
          { ...cell(SWEEP_SUMMARY_SUM, 'in_the_money'), support: 'sum', derivation: 'derived_from_rows', occurrences: null },
          cell(SWEEP_SECTION_CELL, 'in_the_money', byState.title),
          cell(SWEEP_CONTACTABLE_CELL, 'contactable', contactable.title),
        ],
      },
    },
  });
}

/** (d) An answer recorded before provenance existed, as History replays it:
 *  the stored claims carry no provenance, so the model serves nulls. */
export function provenanceLegacyAnswer(): GenieAnswer {
  const answer = provenanceSingleAnswer();
  const claims = answer.proof?.claims;
  return {
    ...answer,
    proof: {
      ...answer.proof,
      claims: claims
        ? { ...claims, items: claims.items.map((item) => ({ ...item, support: null, column: null, occurrences: null })) }
        : null,
    },
  };
}

/** Response bodies this module serves, for the fixture contract exporter. */
export function contractSamples(): ContractSample[] {
  const bodies: Array<[string, GenieAnswer]> = [
    ['provenanceSingleAnswer()', provenanceSingleAnswer()],
    ['provenanceDivergenceAnswer()', provenanceDivergenceAnswer()],
    ['provenanceSweepAnswer()', provenanceSweepAnswer()],
    ['provenanceLegacyAnswer()', provenanceLegacyAnswer()],
  ];
  return bodies.map(([source, body]) => ({
    source,
    method: 'POST',
    pattern: '/api/genie/message/complete',
    path: '/api/v1/genie/message/complete',
    query: '',
    status: 200,
    body,
  }));
}
