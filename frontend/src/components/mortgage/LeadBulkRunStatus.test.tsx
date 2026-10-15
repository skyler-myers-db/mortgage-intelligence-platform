/**
 * @vitest-environment happy-dom
 *
 * A bulk run's report never prints the server's raw refusal text (audit
 * states-04 residual, W5d lq-requests-binding C14). The writers turn a
 * refusal into fixed copy (bulkRefusalCopy); this suite drives the REAL
 * approve and reject writers with a server that answers its own sentence,
 * then renders the run's report and canary line and reads the DOM.
 */
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { ApiError } from '../../lib/apiTransport';
import type { ApproveResult, RejectResult } from '../../lib/apiTypes';
import { BULK_UNRECORDED, bulkRefusalCopy } from './bulkRefusalCopy';
import { rejectWithReport } from './leadBulkRejectWrite';
import { approveWithReport, type DecisionWriteDeps } from './leadDecisionWrites';
import { LeadBulkRunResult } from './LeadBulkRunStatus';
import { bulkCanaryNotice } from './LeadBulkRunStatus.copy';
import type { BulkRowReport, BulkRunKind, BulkRunResult } from './useLeadBulkRun';

const SERVER_TEXT = 'Approval requires the audited generated draft proof';
const BORROWER = 'B-AAAAAAAAAAAA1';

function refusal(status: number): ApiError {
  return new ApiError(SERVER_TEXT, { path: '/api/outreach/approve', status });
}

function deps(error: ApiError): DecisionWriteDeps {
  return {
    leadsById: new Map(),
    campaignBinding: null,
    leadViewId: null,
    approvalRequestBatchId: null,
    approve: vi.fn((): Promise<ApproveResult> => Promise.reject(error)),
    reject: vi.fn((): Promise<RejectResult> => Promise.reject(error)),
    requestIds: { idFor: (fingerprint: string) => `req-${fingerprint.length}`, settle: () => undefined },
    isInFlight: () => false,
    passesGate: () => true,
    latch: () => undefined,
    onDecided: () => undefined,
  };
}

function rendered(kind: BulkRunKind, report: BulkRowReport): string {
  const result: BulkRunResult = {
    kind,
    total: 2,
    ok: 0,
    failed: [{ borrowerId: BORROWER, outcome: 'backend', message: report.message }],
    skipped: [],
    notStarted: ['B-AAAAAAAAAAAA2'],
    stopped: false,
    sessionEnded: false,
    canary: { borrowerId: BORROWER, message: report.message },
  };
  const html = renderToStaticMarkup(<LeadBulkRunResult result={result} onDismiss={() => undefined} />);
  return `${html}\n${bulkCanaryNotice(result.canary!)}`;
}

describe('bulk refusal copy reaches the rendered report, never the server text', () => {
  it.each([403, 409, 422, 429, 500])('a %s on a bulk approve row', async (status) => {
    const report = await approveWithReport(
      deps(refusal(status)),
      BORROWER,
      undefined,
      { bulk_id: 'bulk-test-1', bulk_rationale: 'Reviewed cohort.', suppressInvalidation: true },
      null,
    );
    expect(report.outcome).toBe('backend');
    const text = rendered('approve', report);
    expect(text).not.toContain(SERVER_TEXT);
    expect(text).toContain(bulkRefusalCopy(refusal(status)));
  });

  it.each([403, 422, 503])('a %s on a bulk reject row', async (status) => {
    const report = await rejectWithReport(deps(refusal(status)), BORROWER, new AbortController().signal, {
      bulkId: 'bulk-test-2',
      reasonCode: 'low_intent',
      note: 'Reviewed.',
    });
    expect(report.outcome).toBe('backend');
    const text = rendered('reject', report);
    expect(text).not.toContain(SERVER_TEXT);
    expect(text).toContain(bulkRefusalCopy(refusal(status)));
  });

  it('a bulk reject row the server answers without recording reads the fixed copy, never the wire flag', async () => {
    const unrecorded = deps(refusal(500));
    unrecorded.reject = vi.fn((): Promise<RejectResult> => Promise.resolve({ rejected: false }));
    const report = await rejectWithReport(unrecorded, BORROWER, new AbortController().signal, {
      bulkId: 'bulk-test-3',
      reasonCode: 'low_intent',
      note: 'Reviewed.',
    });
    expect(report).toEqual({ outcome: 'backend', message: BULK_UNRECORDED });
    const text = rendered('reject', report);
    expect(text).toContain(`refused: ${BULK_UNRECORDED}`);
    expect(text).not.toMatch(/endpoint|rejected=false/i);
  });
});
