/**
 * Inputs and curated bodies for the fixture contract test (audit 2026-09-21
 * quality-09, step 3). tools/export_e2e_fixtures.mjs imports this module,
 * invokes every defaultFixtures() handler with the samples below, and adds
 * contractSamples(); tests/unit/test_e2e_fixture_contract.py then validates
 * each 2xx body against the real FastAPI response model of the route the app
 * would call. See docs/testing.md ("Fixture contract").
 *
 * The curated bodies are the scenario payloads specs register per test
 * (decision receipts, Genie turns and refusals, degraded health, layout
 * populations), which defaultFixtures() never serves. A data/*.ts module may
 * export its own `contractSamples()` instead of growing this file; the
 * exporter collects both.
 *
 * Imports are type-only or relative so the exporter runs on bare Node with no
 * node_modules (the fixture ESLint block enforces it).
 */
import type { LeadExportReceiptRequest } from '../../../src/lib/apiClients/leadExport';
import type { FixtureEntry, FixtureReply, HttpMethod } from './mockApi';
import { BORROWERS, PRIMARY_BORROWER } from './data/borrowers';
import { PRIMARY_ASSET_KEY } from './data/dataEstate';
import {
  APPROVE_AUDIT_ID,
  REJECT_AUDIT_ID,
  approveResult,
  decidedLifecycle,
  ledgerReceipt,
  rejectResult,
} from './data/decisionReceipt';
import { leadExportReceiptFor } from './data/exportAudit';
import { portfolioCreated, routedApproveResult } from './data/feedbackGuard';
import { GENIE_CONVERSATION_ID } from './data/genie';
import { REFUSAL_FAMILIES, REFUSAL_REPORT_ACCEPTED, REFUSED_QUESTIONS, refusedSubmit, refusedTurn } from './data/genieRefusal';
import {
  GENIE_MESSAGE_ID,
  GENIE_PROGRESS_TOKEN,
  genieAnswerFixture,
  genieDeepAnswerFixture,
  genieProgressFixture,
} from './data/genieTurn';
import { LIVE_SHAPED_HOME_PREVIEW, MAX_HOME_PREVIEW, MAX_HOME_SUMMARY } from './data/homeAnswer';
import { CONTACTABLE_PORTFOLIO_PREVIEW } from './data/portfolio';
import { emptyZipRollupsFixture, mapAllClassesFixture } from './data/mapEncoding';
import { RATE_WINDOW_SCREEN_NEAR_TOP } from './data/rateWindow';
import { QUEUE_LAYOUT_LEADS } from './data/queueLayout';
import { SNAPSHOT_AT } from './data/reference';
import { OFFICERS } from './data/salesOps';
import { ALL_CODE_ELIGIBLE_SEGMENTS, GATED_SEGMENTS, VARIED_SEGMENTS } from './data/segmentsCards';
import { SIGNED_IN_APPROVER } from './data/shellWayfinding';
import { HEALTH_WAREHOUSE_DOWN, HEALTH_WAREHOUSE_RESUMING } from './data/warehouseResume';

/**
 * One body the harness can serve, as tools/export_e2e_fixtures.mjs exports it.
 * Any fixture module (this one or a data/*.ts module) may export
 * `contractSamples(): ContractSample[] | Promise<ContractSample[]>`; the
 * exporter collects all of them.
 */
export interface ContractSample {
  /** A stable name for the body, e.g. `genieAnswerFixture()`; the exporter prefixes the module. */
  source: string;
  method: HttpMethod;
  /** Registry-style pattern the app calls, e.g. `/api/borrowers/:id/proof`. */
  pattern: string;
  /** Concrete path; default: the pattern with PARAM_SAMPLES substituted. */
  path?: string;
  /** Query string without the leading '?'; default ''. */
  query?: string;
  /** HTTP status the fixture answers with; default 200. */
  status?: number;
  body: unknown;
}

/** A value for every `:param` a registered pattern uses. An unknown one fails the export. */
export const PARAM_SAMPLES: Readonly<Record<string, string>> = {
  id: PRIMARY_BORROWER.borrower_id,
  assetKey: PRIMARY_ASSET_KEY,
  loanOfficerId: OFFICERS[0].loan_officer_id,
};

/** Request bodies for the default POST handlers, keyed `METHOD pattern`. */
export const BODY_SAMPLES: Readonly<Record<string, unknown>> = {
  'POST /api/portfolio/preview': { criteria: {} },
  'POST /api/portfolio/campaign-recommendation': { criteria: {} },
  'POST /api/offers/recommend': { borrower_id: PRIMARY_BORROWER.borrower_id },
  'POST /api/outreach/draft': { borrower_id: PRIMARY_BORROWER.borrower_id, channel: 'email' },
  'POST /api/genie/start': {},
};

/**
 * Extra query strings for default handlers whose body branches on the query,
 * keyed `METHOD pattern`; each is exported as one more sample.
 */
export const QUERY_SAMPLES: Readonly<Record<string, readonly string[]>> = {
  'GET /api/geo/zip-rollups': ['state=CA', 'state=zz'],
  'GET /api/geo/county-rollups': ['state=TX'],
  'GET /api/geo/assignment-overlay': ['level=county&state=TX', 'level=zip&state=TX&county_fips=48201'],
  'GET /api/sales/conversion': ['groupBy=cohort'],
};

function sample(source: string, method: HttpMethod, pattern: string, body: unknown, extra: Partial<ContractSample> = {}): ContractSample {
  return { source, method, pattern, body, ...extra };
}

function reply(source: string, method: HttpMethod, pattern: string, served: FixtureReply): ContractSample {
  return sample(source, method, pattern, served.body, { status: served.status ?? 200 });
}

/** Invoke a scenario FixtureEntry the way the mock API would. */
async function fromEntry(source: string, entry: FixtureEntry, query = ''): Promise<ContractSample> {
  const url = new URL(`http://fixture.invalid${entry.pattern}${query ? `?${query}` : ''}`);
  const served = await entry.handler({ method: entry.method, path: entry.pattern, url, query: url.searchParams, params: {}, body: null });
  return sample(source, entry.method, entry.pattern, served.body, { status: served.status ?? 200, query });
}

function genieSamples(): ContractSample[] {
  const statuses = ['SUBMITTED', 'FETCHING_METADATA', 'ASKING_AI', 'EXECUTING_QUERY', 'COMPLETED'] as const;
  return [
    sample('genieAnswerFixture()', 'POST', '/api/genie/message/complete', genieAnswerFixture()),
    sample('genieDeepAnswerFixture()', 'POST', '/api/genie/message/complete', genieDeepAnswerFixture()),
    ...statuses.map((status) =>
      sample(`genieProgressFixture(${status})`, 'POST', '/api/genie/message/progress', genieProgressFixture(status)),
    ),
    sample('registerGenieTurn submit', 'POST', '/api/genie/message/submit', {
      completed: false,
      conversation_id: GENIE_CONVERSATION_ID,
      message_id: GENIE_MESSAGE_ID,
      progress_token: GENIE_PROGRESS_TOKEN,
      question_hash: genieAnswerFixture().question_hash ?? null,
      deep: false,
      response: null,
    }),
    ...REFUSAL_FAMILIES.flatMap((reason) => [
      sample(`refusedTurn(${reason})`, 'POST', '/api/genie/message/complete', refusedTurn(reason, REFUSED_QUESTIONS[reason])),
      sample(`refusedSubmit(${reason})`, 'POST', '/api/genie/message/submit', refusedSubmit(reason, REFUSED_QUESTIONS[reason])),
    ]),
    sample('REFUSAL_REPORT_ACCEPTED', 'POST', '/api/genie/refusal-report', REFUSAL_REPORT_ACCEPTED),
  ];
}

function decisionSamples(): ContractSample[] {
  const declaration: LeadExportReceiptRequest = {
    scope: 'selected',
    row_count: 2,
    csv_sha256: 'a'.repeat(64),
    borrower_ids: [BORROWERS[0].borrower_id, BORROWERS[1].borrower_id],
    borrower_ids_sha256: 'b'.repeat(64),
    filters: {},
  };
  return [
    reply('approveResult()', 'POST', '/api/outreach/approve', approveResult(APPROVE_AUDIT_ID)),
    reply('routedApproveResult()', 'POST', '/api/outreach/approve', routedApproveResult()),
    reply('rejectResult()', 'POST', '/api/outreach/reject', rejectResult(REJECT_AUDIT_ID)),
    sample('ledgerReceipt(approved)', 'GET', '/api/audit/receipt/:id', ledgerReceipt(APPROVE_AUDIT_ID, PRIMARY_BORROWER, 'approved'), {
      path: `/api/audit/receipt/${APPROVE_AUDIT_ID}`,
    }),
    sample('ledgerReceipt(rejected)', 'GET', '/api/audit/receipt/:id', ledgerReceipt(REJECT_AUDIT_ID, PRIMARY_BORROWER, 'rejected'), {
      path: `/api/audit/receipt/${REJECT_AUDIT_ID}`,
    }),
    sample('decidedLifecycle(approved)', 'GET', '/api/borrowers/:id/lifecycle', decidedLifecycle(PRIMARY_BORROWER.borrower_id, 'approved', APPROVE_AUDIT_ID, SNAPSHOT_AT)),
    sample('decidedLifecycle(rejected)', 'GET', '/api/borrowers/:id/lifecycle', decidedLifecycle(PRIMARY_BORROWER.borrower_id, 'rejected', REJECT_AUDIT_ID, SNAPSHOT_AT)),
    reply('portfolioCreated()', 'POST', '/api/portfolio/create', portfolioCreated('Summit IL refi cohort')),
    sample('leadExportReceiptFor(selected)', 'POST', '/api/leads/export-receipt', leadExportReceiptFor(declaration)),
  ];
}

function surfaceSamples(): ContractSample[] {
  return [
    sample('CONTACTABLE_PORTFOLIO_PREVIEW', 'POST', '/api/portfolio/preview', CONTACTABLE_PORTFOLIO_PREVIEW),
    sample('LIVE_SHAPED_HOME_PREVIEW', 'POST', '/api/portfolio/preview', LIVE_SHAPED_HOME_PREVIEW),
    sample('MAX_HOME_PREVIEW', 'POST', '/api/portfolio/preview', MAX_HOME_PREVIEW),
    sample('MAX_HOME_SUMMARY', 'GET', '/api/home/summary', MAX_HOME_SUMMARY),
    sample('QUEUE_LAYOUT_LEADS', 'GET', '/api/leads', [...QUEUE_LAYOUT_LEADS]),
    sample('VARIED_SEGMENTS', 'GET', '/api/segments', [...VARIED_SEGMENTS]),
    sample('GATED_SEGMENTS', 'GET', '/api/segments', [...GATED_SEGMENTS]),
    sample('ALL_CODE_ELIGIBLE_SEGMENTS', 'GET', '/api/segments', [...ALL_CODE_ELIGIBLE_SEGMENTS]),
    sample('HEALTH_WAREHOUSE_RESUMING', 'GET', '/api/health', HEALTH_WAREHOUSE_RESUMING),
    sample('HEALTH_WAREHOUSE_DOWN', 'GET', '/api/health', HEALTH_WAREHOUSE_DOWN),
    sample('SIGNED_IN_APPROVER', 'GET', '/api/session', SIGNED_IN_APPROVER),
    sample('RATE_WINDOW_SCREEN_NEAR_TOP', 'GET', '/api/analytics/rate-window', RATE_WINDOW_SCREEN_NEAR_TOP),
  ];
}

export async function contractSamples(): Promise<ContractSample[]> {
  return [
    ...genieSamples(),
    ...decisionSamples(),
    ...surfaceSamples(),
    await fromEntry('mapAllClassesFixture', mapAllClassesFixture),
    await fromEntry('emptyZipRollupsFixture', emptyZipRollupsFixture, 'state=CA'),
  ];
}
