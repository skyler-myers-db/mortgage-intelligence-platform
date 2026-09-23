/**
 * A scripted live Genie turn for the fixture harness: the async
 * submit → progress → complete contract of backend/api/genie.py, driven from
 * the test so it can hold the turn open, count polls and release each step.
 *
 * Nothing here is registered by default (see ./genie.ts): a spec that asks a
 * question calls `registerGenieTurn(mockApi, ...)` and owns the timing.
 *
 * The answer is synthetic and guard-safe: state-level counts from the shared
 * footprint, no borrower ids, no names, no contact fields, no protected-class
 * or health vocabulary. It is test data only and never overlays a live turn
 * in the running app (CLAUDE.md live-first rule).
 */
import type { GenieAnswer } from '../../../../src/types';
import type { GenieLiveProgress, GenieSubmitResult } from '../../../../src/lib/apiTypes';
import type { MockApi } from '../mockApi';
import { GENIE_CONVERSATION_ID } from './genie';
import { SNAPSHOT_AT, STATES, TOTALS } from './reference';

export const GENIE_MESSAGE_ID = 'fixture-message-0001';
export const GENIE_PROGRESS_TOKEN = 'fixture-progress-token-0001';
export const GENIE_QUESTION = 'Which states have the most prime refi candidates?';

const SOURCE_ASSETS = ['mip.semantics.borrower_opportunity_metric_view', 'mip.gold.borrower_360'];
const GOVERNED_SQL =
  'SELECT state, SUM(in_the_money) AS in_the_money FROM mip.semantics.borrower_opportunity_metric_view GROUP BY state ORDER BY in_the_money DESC';

/** The backend's stage vocabulary (backend/services/genie_progress.py). */
const STAGE_BY_STATUS: Record<string, [stage: string, label: string]> = {
  SUBMITTED: ['understanding', 'Genie accepted the question'],
  FETCHING_METADATA: ['understanding', 'Reading governed table metadata'],
  ASKING_AI: ['drafting', 'Drafting a governed SQL plan'],
  EXECUTING_QUERY: ['executing', 'Running the governed query'],
  COMPLETED: ['complete', 'Verifying the answer against its rows'],
};

export function genieProgressFixture(status: keyof typeof STAGE_BY_STATUS): GenieLiveProgress {
  const [stage, stage_label] = STAGE_BY_STATUS[status];
  return {
    status,
    stage,
    stage_label,
    terminal: status === 'COMPLETED',
    failed: false,
    reasoning_trace: [
      { kind: 'process', content: 'Selected the governed borrower opportunity metric view.' },
      ...(stage === 'executing' || stage === 'complete'
        ? [{ kind: 'process', content: 'Ran one governed aggregate over the current footprint.' }]
        : []),
    ],
    sql_preview: stage === 'executing' || stage === 'complete' ? GOVERNED_SQL : null,
    error_hint: null,
  };
}

const STATE_ROWS = STATES.map((state) => ({ state: state.code, in_the_money: state.inTheMoney }));
const LEADER = STATES[0];

function narrative(): string {
  return (
    `${LEADER.name} leads the footprint with ${LEADER.inTheMoney.toLocaleString('en-US')} prime refi candidates, ` +
    `followed by ${STATES[1].name} (${STATES[1].inTheMoney.toLocaleString('en-US')}) and ` +
    `${STATES[2].name} (${STATES[2].inTheMoney.toLocaleString('en-US')}). ` +
    `Across all ${STATES.length} states, ${TOTALS.inTheMoney.toLocaleString('en-US')} borrowers clear the refinance-economics screen.`
  );
}

/** A single-turn answer: narrative, verified rows, chart plan, proof. */
export function genieAnswerFixture(overrides: Partial<GenieAnswer> = {}): GenieAnswer {
  return {
    answer: narrative(),
    source: 'genie',
    trusted_assets: SOURCE_ASSETS,
    conversation_id: GENIE_CONVERSATION_ID,
    message_id: GENIE_MESSAGE_ID,
    elapsed_ms: 18400,
    question_hash: 'd'.repeat(16),
    sql_query: GOVERNED_SQL,
    row_count: STATE_ROWS.length,
    genie_status: 'COMPLETED',
    metric_value: null,
    table_rows: STATE_ROWS,
    visualization: { kind: 'bar', title: 'Prime refi candidates by state', x: 'state', y: 'in_the_money', reason: 'One measure across a small set of states.' },
    follow_up_questions: [
      'How many of them are current customers?',
      'Which counties in Illinois carry the most?',
    ],
    proof: {
      sql_query: GOVERNED_SQL,
      source_assets: SOURCE_ASSETS,
      data_freshness: SOURCE_ASSETS.map((asset) => ({ asset, refreshed_at: SNAPSHOT_AT, status: 'fresh', note: null })),
      row_count: STATE_ROWS.length,
      filters: ['footprint: current coverage'],
      trusted: true,
      reasoning_trace: [{ kind: 'process', content: 'Verified every figure against the returned rows.' }],
      known_data_gaps: [],
      conversation_id: GENIE_CONVERSATION_ID,
      message_id: GENIE_MESSAGE_ID,
      elapsed_ms: 18400,
      generated_at: SNAPSHOT_AT,
    },
    actions: [],
    ...overrides,
  };
}

/** A deep-research answer: an executive summary first, then titled sections. */
export function genieDeepAnswerFixture(): GenieAnswer {
  const top = STATES.slice(0, 3);
  return genieAnswerFixture({
    summary:
      `${LEADER.name}, ${STATES[1].name} and ${STATES[2].name} hold ` +
      `${top.reduce((sum, state) => sum + state.inTheMoney, 0).toLocaleString('en-US')} of the ` +
      `${TOTALS.inTheMoney.toLocaleString('en-US')} prime refi candidates in the current footprint.`,
    sections: [
      {
        title: 'Candidates by state',
        question: 'How are prime refi candidates distributed by state?',
        answer: narrative(),
        trusted_assets: SOURCE_ASSETS,
        sql_query: GOVERNED_SQL,
        row_count: STATE_ROWS.length,
        table_rows: STATE_ROWS,
        visualization: { kind: 'bar', title: 'Prime refi candidates by state', x: 'state', y: 'in_the_money' },
      },
      {
        title: 'Contactable share',
        question: 'How many of them are contactable today?',
        answer:
          `${TOTALS.contactable.toLocaleString('en-US')} borrowers across the footprint are contact-eligible; ` +
          'the Lead Queue ranks that subset.',
        trusted_assets: SOURCE_ASSETS,
        sql_query: 'SELECT state, SUM(contactable) AS contactable FROM mip.semantics.borrower_opportunity_metric_view GROUP BY state',
        row_count: STATES.length,
        table_rows: STATES.map((state) => ({ state: state.code, contactable: state.contactable })),
        visualization: { kind: 'bar', title: 'Contactable borrowers by state', x: 'state', y: 'contactable' },
      },
    ],
  });
}

export interface GenieTurnScript {
  /** Submit flags the turn as deep research. Default false. */
  deep?: boolean;
  /** The governed answer the completion call returns. */
  answer?: GenieAnswer;
  /** Hold the completion call until `releaseComplete()`. Default false. */
  holdComplete?: boolean;
  /** Keep progress non-terminal until `finishGenieTurn()`. Default true. */
  holdProgress?: boolean;
}

/**
 * Test-side handle on one scripted turn. Counters read the mock's own call
 * log, so they count what the browser really sent.
 */
export interface GenieTurnController {
  readonly submits: number;
  readonly progressPolls: number;
  readonly completes: number;
  /** Make the next progress poll report Genie's own turn as terminal. */
  finishGenieTurn(): void;
  /** Let a held completion call return the answer. */
  releaseComplete(): void;
}

export function registerGenieTurn(mockApi: MockApi, script: GenieTurnScript = {}): GenieTurnController {
  const answer = script.answer ?? genieAnswerFixture();
  let genieTerminal = script.holdProgress === false;
  let completeGate: Promise<void> = Promise.resolve();
  let openGate: () => void = () => undefined;
  if (script.holdComplete) {
    completeGate = new Promise<void>((resolve) => {
      openGate = resolve;
    });
  }
  const counts = { submits: 0, progressPolls: 0, completes: 0 };

  mockApi.register<GenieSubmitResult>('POST', '/api/genie/message/submit', () => {
    counts.submits += 1;
    return {
      body: {
        completed: false,
        conversation_id: GENIE_CONVERSATION_ID,
        message_id: GENIE_MESSAGE_ID,
        progress_token: GENIE_PROGRESS_TOKEN,
        question_hash: answer.question_hash ?? null,
        deep: script.deep === true,
        response: null,
      },
    };
  });
  mockApi.register<GenieLiveProgress>('POST', '/api/genie/message/progress', () => {
    counts.progressPolls += 1;
    return { body: genieProgressFixture(genieTerminal ? 'COMPLETED' : 'EXECUTING_QUERY') };
  });
  mockApi.register<GenieAnswer>('POST', '/api/genie/message/complete', async () => {
    counts.completes += 1;
    await completeGate;
    return { body: answer };
  });

  return {
    get submits() {
      return counts.submits;
    },
    get progressPolls() {
      return counts.progressPolls;
    },
    get completes() {
      return counts.completes;
    },
    finishGenieTurn() {
      genieTerminal = true;
    },
    releaseComplete() {
      openGate();
    },
  };
}
