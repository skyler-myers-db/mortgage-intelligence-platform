/**
 * A Lead Queue approve whose offer code the route would refuse
 * (WireCodeRefusedError, audit quality-04 P2, W5e w5-wire-types A1): the
 * single row reads the existing 'Reload the app to approve' copy in a toast,
 * a bulk row reports it in the run, and the code itself never appears in
 * either. Nothing is recorded, so the row is never decided.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { OutreachDraftResult } from '../../lib/apiTypes';
import { WireCodeRefusedError } from '../../lib/outreachWireCodes';
import { makeLeadSummary } from '../../test/factories/leads';
import { approveWithReport, type DecisionWriteDeps } from './leadDecisionWrites';

const toastMocks = vi.hoisted(() => ({ error: vi.fn(), info: vi.fn(), success: vi.fn() }));
vi.mock('../../lib/toast', () => ({ toast: toastMocks }));

const BORROWER = 'B-0000000000007';
const DRAFT = { generation_id: 'gen-1', subject: 'Review', body: 'draft', offer_code: 'refi' } as OutreachDraftResult;

function deps(approve: DecisionWriteDeps['approve']): DecisionWriteDeps & { decided: string[] } {
  const decided: string[] = [];
  return {
    decided,
    leadsById: new Map([[BORROWER, makeLeadSummary({ borrower_id: BORROWER, evidence_ids: ['ev-1'], recommended_offer_code: 'mystery_offer' })]]),
    campaignBinding: null,
    leadViewId: null,
    approve,
    reject: vi.fn(),
    requestIds: { idFor: () => 'req-1', settle: vi.fn() } as unknown as DecisionWriteDeps['requestIds'],
    isInFlight: () => false,
    passesGate: () => true,
    latch: vi.fn(),
    onDecided: (borrowerId) => {
      decided.push(borrowerId);
    },
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('approveWithReport and a refused wire code', () => {
  it('a single row shows the existing reload copy, never the code, and is not decided', async () => {
    const writeDeps = deps(vi.fn().mockRejectedValue(new WireCodeRefusedError('approve', 'offer_code')));

    const report = await approveWithReport(writeDeps, BORROWER, undefined, {}, DRAFT);

    expect(report).toEqual({ outcome: 'backend', message: 'Reload the app to approve' });
    expect(toastMocks.error).toHaveBeenCalledTimes(1);
    expect(toastMocks.error).toHaveBeenCalledWith(`Couldn't approve ${BORROWER}`, { detail: 'Reload the app to approve' });
    expect(JSON.stringify(toastMocks.error.mock.calls)).not.toContain('mystery_offer');
    expect(writeDeps.decided).toEqual([]);
  });

  it('a bulk row reports it in the run without a toast', async () => {
    const writeDeps = deps(vi.fn().mockRejectedValue(new WireCodeRefusedError('approve', 'offer_code')));

    const report = await approveWithReport(writeDeps, BORROWER, undefined, { bulk_id: 'bulk-1' }, null);

    expect(report).toEqual({ outcome: 'backend', message: 'Reload the app to approve' });
    expect(toastMocks.error).not.toHaveBeenCalled();
    expect(writeDeps.decided).toEqual([]);
  });
});
