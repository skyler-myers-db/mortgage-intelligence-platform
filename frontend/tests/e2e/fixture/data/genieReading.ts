/**
 * Genie reading lane fixtures (audit 2026-09-21 wave 3: stack-02, genie-07,
 * genie-08, motion-v2, genie-06 slice 2): an answer whose narrative uses the
 * wider markdown grammar and carries 120 rows x 7 columns, a five-section
 * sweep, a History replay that kept 50 of 120 rows, stored turns to seed
 * the transcript with, and the GENIE_ANSWER_EXPORT receipt a CSV download
 * waits for.
 *
 * Nothing here is in the default registry: a turn and a receipt are
 * registered per test (the receipt is a WRITE and each test owns its timing,
 * as in ./exportAudit.ts). Synthetic and guard-safe: state-level counts from
 * the shared footprint, no borrower ids, names or contact fields, no
 * protected-class or health vocabulary. The link in the narrative points at
 * example.com and must never become an anchor.
 */
import type { GenieAnswer, GenieAnswerSection } from '../../../../src/types';
import type {
  GenieAnswerExportReceipt,
  GenieAnswerExportReceiptRequest,
} from '../../../../src/lib/apiClients/genieExport';
import type { MockApi } from '../mockApi';
import { GENIE_CONVERSATION_ID } from './genie';
import { GENIE_MESSAGE_ID, genieAnswerFixture } from './genieTurn';
import { STATES } from './reference';

export const READING_QUESTION = 'Where does prime refinance demand sit by state?';
export const RECEIPT_ID = '5f0c2a8e-4b1d-4c3e-9a70-0000000000b7';
export const RECEIPT_ACTOR = 'analyst@summit-mortgage.example';
export const RECEIPT_RECORDED_AT = '2026-07-14T15:05:00Z';
/** The only anchor the narrative may produce is a reviewed Source link. */
export const INERT_LINK_HOST = 'example.com';

/** Every construct of the answer grammar, once. */
export const READING_NARRATIVE = [
  '### Where refinance demand sits ###',
  `Three states hold most of the *prime* refi candidates in the footprint:`,
  `1. ${STATES[0].name} leads with ${STATES[0].inTheMoney.toLocaleString('en-US')}.`,
  `2. ${STATES[1].name} follows with ${STATES[1].inTheMoney.toLocaleString('en-US')}.`,
  `3. ${STATES[2].name} is third with ${STATES[2].inTheMoney.toLocaleString('en-US')}.`,
  `See [the rate table](https://${INERT_LINK_HOST}/rates) for the spread.`,
  '| State | Candidates |',
  '|---|---:|',
  `| ${STATES[0].code} | ${STATES[0].inTheMoney.toLocaleString('en-US')} |`,
].join('\n');

/** `count` synthetic rows, seven columns wide. */
export function wideRows(count: number): Array<Record<string, unknown>> {
  return Array.from({ length: count }, (_, i) => {
    const state = STATES[i % STATES.length];
    return {
      state: state.code,
      segment_code: state.topSegment,
      borrowers: state.addressable - i,
      avg_score: state.avgScore,
      avg_rate_spread_bps: 80 + (i % 40),
      avg_equity_pct: 30 + (i % 25),
      contactable: state.contactable + i,
    };
  });
}

/** A single-turn answer: the wider grammar and 120 rows x 7 columns. */
export function readingAnswer(overrides: Partial<GenieAnswer> = {}): GenieAnswer {
  const rows = wideRows(120);
  return genieAnswerFixture({
    answer: READING_NARRATIVE,
    table_rows: rows,
    row_count: rows.length,
    visualization: null,
    follow_up_questions: [],
    ...overrides,
  });
}

function section(index: number): GenieAnswerSection {
  const state = STATES[index];
  return {
    title: `${state.name} opportunity`,
    question: `How large is the ${state.name} opportunity?`,
    answer: `${state.name} holds ${state.inTheMoney.toLocaleString('en-US')} prime refi candidates.`,
    trusted_assets: ['mip.semantics.borrower_opportunity_metric_view'],
    sql_query: 'SELECT state, SUM(in_the_money) AS in_the_money FROM mip.semantics.borrower_opportunity_metric_view GROUP BY state',
    row_count: 1,
    table_rows: [{ state: state.code, in_the_money: state.inTheMoney }],
    visualization: null,
  };
}

/** A five-section sweep: outline plus accordion headings. */
export function fiveSectionAnswer(): GenieAnswer {
  return genieAnswerFixture({
    summary: `${STATES[0].name} and ${STATES[1].name} lead the prime refi opportunity in the current footprint.`,
    sections: [0, 1, 2, 3, 4].map(section),
  });
}

/** A History replay that kept 50 of 120 rows (GENIE_HISTORY_TRIMMED_ROWS). */
export function trimmedReplayAnswer(messageId: string): GenieAnswer {
  return genieAnswerFixture({
    message_id: messageId,
    answer: `${STATES[0].name} leads the footprint.`,
    table_rows: wideRows(50).map(({ state, borrowers }) => ({ state, borrowers })),
    row_count: 120,
    visualization: null,
  });
}

/** A settled earlier turn, as the transcript store persists it. */
export interface StoredTurn {
  question: string;
  response: GenieAnswer;
}

export function earlierTurns(count: number): StoredTurn[] {
  return Array.from({ length: count }, (_, i) => ({
    question: `Earlier question ${i + 1}: how many prime refi candidates are in ${STATES[i % STATES.length].name}?`,
    response: genieAnswerFixture({
      message_id: `fixture-message-earlier-${i + 1}`,
      answer: `${STATES[i % STATES.length].name} holds ${STATES[i % STATES.length].inTheMoney.toLocaleString('en-US')} prime refi candidates in earlier turn ${i + 1}.`,
      visualization: null,
      follow_up_questions: [],
    }),
  }));
}

export function genieExportReceiptFor(declaration: GenieAnswerExportReceiptRequest): GenieAnswerExportReceipt {
  return {
    audit_event_id: RECEIPT_ID,
    event_type: 'GENIE_ANSWER_EXPORT',
    actor: RECEIPT_ACTOR,
    scope: declaration.scope,
    row_count: declaration.row_count,
    csv_sha256: declaration.csv_sha256,
    columns_sha256: declaration.columns_sha256,
    recorded_at: RECEIPT_RECORDED_AT,
  };
}

export interface ReceiptRecorder {
  readonly bodies: unknown[];
}

/**
 * Answer POST /api/genie/export-receipt with `status`: 200 returns the ledger
 * row for the declaration; anything else returns a constant detail.
 */
export function registerExportReceipt(mockApi: MockApi, status = 200): ReceiptRecorder {
  const bodies: unknown[] = [];
  mockApi.register<GenieAnswerExportReceipt | { detail: string }>('POST', '/api/genie/export-receipt', ({ body }) => {
    bodies.push(body);
    if (status !== 200) {
      return { status, body: { detail: status === 404 ? 'Genie answer not found' : 'Service temporarily unavailable' } };
    }
    return { body: genieExportReceiptFor(body as GenieAnswerExportReceiptRequest) };
  });
  return { bodies };
}

const SAMPLE_DECLARATION: GenieAnswerExportReceiptRequest = {
  conversation_id: GENIE_CONVERSATION_ID,
  message_id: GENIE_MESSAGE_ID,
  scope: 'answer',
  row_count: 120,
  answer_row_count: 120,
  csv_sha256: 'a'.repeat(64),
  columns_sha256: 'b'.repeat(64),
};

export interface ContractSample {
  source: string;
  method: 'GET' | 'POST';
  pattern: string;
  path: string;
  query: string;
  status: number;
  body: unknown;
}

/** Response bodies this module registers, for the fixture contract exporter. */
export function contractSamples(): ContractSample[] {
  const source = 'data/genieReading.ts';
  return [
    {
      source,
      method: 'POST',
      pattern: '/api/genie/export-receipt',
      path: '/api/genie/export-receipt',
      query: '',
      status: 200,
      body: genieExportReceiptFor(SAMPLE_DECLARATION),
    },
    ...[readingAnswer(), fiveSectionAnswer(), trimmedReplayAnswer('fixture-message-trimmed')].map((body) => ({
      source,
      method: 'POST' as const,
      pattern: '/api/genie/message/complete',
      path: '/api/genie/message/complete',
      query: '',
      status: 200,
      body,
    })),
  ];
}
