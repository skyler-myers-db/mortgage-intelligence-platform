/**
 * Fixtures for the w5-genie-context-deeplink lane spec
 * (genie-context-deeplink.fixture.spec.ts): the server-rendered Genie scope
 * for an empty Lead Queue (genie-04 phase 2), the panel/route conversation
 * switch and link cache (shell-03 remainder), the section outline (genie-08
 * item 1) and the durable feedback failure.
 *
 * Nothing here is a default read, so nothing is added to registry.ts: each
 * test registers what it needs. The scope reply is the server's real
 * rendering of the closed context the test sends (pinned by
 * tests/unit/test_genie_context_model.py), never improvised here.
 *
 * Synthetic only: conversation ids are shape-valid hex, answers carry no
 * borrower id, name or contact field.
 */
import type { GenieContextScopeResponse } from '../../../../src/lib/apiClients/genieContextScope';
import type { GenieAnswer, GenieAnswerSection, GenieSessionDetail, GenieSessionSummary, LeadSummary } from '../../../../src/types';
import type { ContractSample } from '../contractSamples';
import { json, type FixtureRequest, type MockApi } from '../mockApi';
import { genieAnswerFixture } from './genieTurn';

/** Two shape-valid conversations (32 hex) the link moves between. */
export const LINK_A_ID = '0a1b2c3d4e5f60718293a4b5c6d7e8f9';
export const LINK_B_ID = 'f9e8d7c6b5a4938271605f4e3d2c1b0a';
export const LINK_A_QUESTION = 'Which states lead on prime refi candidates?';
export const LINK_A_ANSWER = 'Conversation A: three states lead the prime refi screen.';
export const LINK_B_QUESTION = 'How is home equity spread across the footprint?';
export const LINK_B_ANSWER = 'Conversation B: equity concentrates in the coastal states.';

/** What POST /api/genie/context-scope renders for an all-mode itm x equity intersection. */
export const INTERSECTION_PROMPT =
  'How many borrowers across the current Cotality data coverage are in the prime refi candidates segment ' +
  'and the home equity candidate segment, and what is the average rate spread?';
export const INTERSECTION_SCOPE: GenieContextScopeResponse = { prompt: INTERSECTION_PROMPT, withheld: null, omitted: [] };
/** What it renders for Texas with the assignee preset: the assignee is named, never rendered. */
export const ASSIGNEE_PROMPT = 'How many borrowers are in Texas, and what is the average rate spread?';
export const ASSIGNEE_SCOPE: GenieContextScopeResponse = { prompt: ASSIGNEE_PROMPT, withheld: null, omitted: ['assignee'] };
export const WITHHELD_SCOPE: GenieContextScopeResponse = { prompt: null, withheld: 'refused', omitted: [] };

/** Every key the real server sends on a governed answer (the fixture contract checks presence). */
function served(body: GenieAnswer): GenieAnswer {
  return {
    summary: null,
    native_visualization: null,
    reasoning_trace: [],
    refusal_reason: null,
    refusal_report_hash: null,
    ...body,
    sections: (body.sections ?? []).map((section) => ({ narrative_withheld: false, ...section })),
  };
}

function answer(conversationId: string, messageId: string, text: string, question: string): GenieAnswer {
  return served(genieAnswerFixture({
    question,
    answer: text,
    conversation_id: conversationId,
    message_id: messageId,
    table_rows: [],
    visualization: null,
    sql_query: null,
    follow_up_questions: [],
  }));
}

/** A completed (inline) submit, with every key the real server sends. */
function inlineSubmit(conversationId: string, messageId: string, response: GenieAnswer) {
  return {
    completed: true,
    conversation_id: conversationId,
    message_id: messageId,
    progress_token: null,
    question_hash: 'c'.repeat(16),
    deep: false,
    completion_jobs: false,
    response,
  };
}

export function sessionDetail(conversationId: string): GenieSessionDetail {
  const [question, text] = conversationId === LINK_B_ID ? [LINK_B_QUESTION, LINK_B_ANSWER] : [LINK_A_QUESTION, LINK_A_ANSWER];
  return { conversation_id: conversationId, turns: [{ question, response: answer(conversationId, `${conversationId.slice(0, 8)}-m1`, text, question) }] };
}

export const LINK_HISTORY: GenieSessionSummary[] = [
  { conversation_id: LINK_B_ID, title: 'Equity spread', last_activity_at: '2026-10-01T15:04:00Z', turn_count: 1 },
];

/** Per-test handle on the session reads. */
export interface SessionReads {
  /** GET /api/genie/sessions/<id> calls the browser sent for `id`. */
  readsOf(id: string): number;
}

/** GET /api/genie/sessions (the History list) and GET /api/genie/sessions/:id. */
export function registerSessions(mockApi: MockApi): SessionReads {
  const reads = new Map<string, number>();
  mockApi.register<{ sessions: GenieSessionSummary[] }>('GET', '/api/genie/sessions', () => json({ sessions: LINK_HISTORY }));
  mockApi.register<GenieSessionDetail>('GET', '/api/genie/sessions/:id', ({ params }: FixtureRequest) => {
    reads.set(params.id, (reads.get(params.id) ?? 0) + 1);
    return json(sessionDetail(params.id));
  });
  return { readsOf: (id) => reads.get(id) ?? 0 };
}

/** Per-test handle on inline (completed) submits. */
export interface InlineSubmits {
  /** The `conversation_id` each submit body carried. */
  readonly conversations: ReadonlyArray<string | null>;
  readonly questions: readonly string[];
}

/**
 * POST /api/genie/message/submit, answered inline (`completed: true`). The
 * answer lands in `persist(bodyConversation)`: by default the conversation
 * the submit named.
 */
export function registerInlineSubmit(
  mockApi: MockApi,
  options: { persistTo?: string; answerFor?: (question: string, conversationId: string) => GenieAnswer } = {},
): InlineSubmits {
  const conversations: Array<string | null> = [];
  const questions: string[] = [];
  mockApi.register('POST', '/api/genie/message/submit', ({ body }: FixtureRequest) => {
    const request = (body ?? {}) as { question?: string; conversation_id?: string | null };
    const question = request.question ?? '';
    conversations.push(request.conversation_id ?? null);
    questions.push(question);
    const conversationId = options.persistTo ?? request.conversation_id ?? LINK_A_ID;
    const messageId = `${conversationId.slice(0, 8)}-m${questions.length + 1}`;
    const response = options.answerFor?.(question, conversationId)
      ?? answer(conversationId, messageId, `Answer to: ${question}`, question);
    return json(inlineSubmit(conversationId, messageId, response));
  });
  return { conversations, questions };
}

/** Per-test handle on the scope render. */
export interface ScopeRenders {
  readonly bodies: readonly unknown[];
}

/** POST /api/genie/context-scope with one fixed, typed reply. */
export function registerScope(mockApi: MockApi, reply: GenieContextScopeResponse): ScopeRenders {
  const bodies: unknown[] = [];
  mockApi.register<GenieContextScopeResponse>('POST', '/api/genie/context-scope', ({ body }: FixtureRequest) => {
    bodies.push(body);
    return json(reply);
  });
  return { bodies };
}

/** GET /api/leads answers a measured zero for any filter. */
export function registerEmptyQueue(mockApi: MockApi): void {
  mockApi.register<LeadSummary[]>('GET', '/api/leads', () =>
    json<LeadSummary[]>([], { headers: { 'X-Total-Matching': '0', 'X-Returned-Rows': '0' } }),
  );
}

const FIVE_TITLES = ['Market size', 'Rate spread', 'Equity cushion', 'Contactable share', 'What this adds up to'] as const;

function fiveSection(title: string, index: number): GenieAnswerSection {
  return {
    title,
    question: `Planned sub-question ${index + 1}: ${title.toLowerCase()}?`,
    // Long enough that every section can scroll to the top of either surface.
    answer: `${title}: finding ${index + 1} for the sweep, verified against its rows.\n\n` + 'Supporting detail. '.repeat(90),
    trusted_assets: ['mip.gold.borrower_360'],
    sql_query: null,
    row_count: null,
    table_rows: [],
    visualization: null,
  };
}

export const FIVE_SECTION_TITLES: readonly string[] = FIVE_TITLES;

/** A deep sweep with five sections: the outline's threshold is four. */
export function fiveSectionAnswer(conversationId: string, messageId: string, question: string): GenieAnswer {
  return served(genieAnswerFixture({
    question,
    conversation_id: conversationId,
    message_id: messageId,
    summary: 'Five planned analyses, each verified against its own rows.',
    sections: FIVE_TITLES.map(fiveSection),
    table_rows: [],
    visualization: null,
    follow_up_questions: [],
  }));
}

export function contractSamples(): ContractSample[] {
  return [
    { source: 'INTERSECTION_SCOPE', method: 'POST', pattern: '/api/genie/context-scope', body: INTERSECTION_SCOPE },
    { source: 'ASSIGNEE_SCOPE', method: 'POST', pattern: '/api/genie/context-scope', body: ASSIGNEE_SCOPE },
    { source: 'WITHHELD_SCOPE', method: 'POST', pattern: '/api/genie/context-scope', body: WITHHELD_SCOPE },
    { source: 'LINK_HISTORY', method: 'GET', pattern: '/api/genie/sessions', body: { sessions: LINK_HISTORY } },
    {
      source: 'sessionDetail(LINK_B_ID)',
      method: 'GET',
      pattern: '/api/genie/sessions/:id',
      path: `/api/genie/sessions/${LINK_B_ID}`,
      body: sessionDetail(LINK_B_ID),
    },
    {
      source: 'fiveSectionAnswer',
      method: 'POST',
      pattern: '/api/genie/message/submit',
      body: inlineSubmit(LINK_A_ID, 'sweep-m1', fiveSectionAnswer(LINK_A_ID, 'sweep-m1', 'Run the five-part sweep.')),
    },
  ];
}
