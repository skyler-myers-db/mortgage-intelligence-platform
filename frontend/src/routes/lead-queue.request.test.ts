import { describe, expect, it } from 'vitest';
import {
  leadQueueFilterInputFromSearchParams,
  leadQueueLenderRefs,
  leadsRequestFromSearchParams,
  leadsRequestUnresolved,
} from './lead-queue.request';

const REFS = ['All', 'Competitor B'];

describe('Lead Queue request builders (W5a)', () => {
  it('derives the lender options the way the route does', () => {
    expect(leadQueueLenderRefs(undefined)).toEqual(['All']);
    expect(leadQueueLenderRefs(['', 'Competitor B'])).toEqual(['Competitor B']);
  });

  it('builds the filter input with the RAW assignee and the bounds on the criteria', () => {
    const input = leadQueueFilterInputFromSearchParams(
      new URLSearchParams(
        'segment_codes=itm,equity&segment_mode=all&state=il&zips=60617,xx&cities=CHICAGO~IL'
        + '&target_lender_ref=Competitor+B&approval_status=Pending&assigned_to=me&aged_days=7'
        + '&min_opportunity_score=70&occupancy=Owner-occupied',
      ),
      REFS,
    );
    expect(input).toMatchObject({
      segmentCodes: ['itm', 'equity'],
      segmentMode: 'all',
      stateFilter: 'IL',
      zipFilters: ['60617'],
      cityFilters: ['CHICAGO~IL'],
      targetLenderRef: 'Competitor B',
      approvalStatus: 'pending',
      outreachStatus: undefined,
      assignedTo: 'me',
      agedDays: 7,
      portfolioCriteria: { occupancy: 'Owner-occupied', min_opportunity_score: '70' },
    });
  });

  it('resolves assigned_to=me to the actor and reports an unresolved me', () => {
    const sp = new URLSearchParams('assigned_to=me&state=TX');
    expect(leadsRequestFromSearchParams(sp, REFS, 'lo@summit.example').opts?.assignedTo).toBe('lo@summit.example');
    expect(leadsRequestFromSearchParams(sp, REFS, null).opts?.assignedTo).toBeUndefined();
    expect(leadsRequestUnresolved(sp, null)).toBe(true);
    expect(leadsRequestUnresolved(sp, 'lo@summit.example')).toBe(false);
    expect(leadsRequestUnresolved(new URLSearchParams('state=TX'), null)).toBe(false);
  });

  it('shapes the request exactly as api.leadsPage takes it', () => {
    expect(leadsRequestFromSearchParams(new URLSearchParams('state=TX&max_rate_spread_bps=50'), REFS, null)).toEqual({
      segment: undefined,
      geo: {
        state: 'TX',
        zip: undefined,
        county: undefined,
        counties: [],
        states: [],
        zips: [],
        cities: [],
        borrowerIds: [],
      },
      opts: {
        segmentCodes: [],
        segmentMode: 'any',
        targetLenderRef: undefined,
        cohortId: undefined,
        funnelStage: undefined,
        portfolioCriteria: { max_rate_spread_bps: '50' },
        approvalStatus: 'any',
        outreachStatus: 'any',
        assignedTo: undefined,
        agedDays: null,
      },
    });
  });
});
