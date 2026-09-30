/**
 * Per-spec handlers for filters-saved-views.fixture.spec.ts (W5a, audit
 * tables-06 / tables-09 phase 2): a bounds-aware GET /api/leads and an
 * in-memory saved-views store with scripted failures. The defaults
 * (data/leads.ts) serve an empty list and never write; a spec that saves or
 * deletes registers these so it owns the timing and the result.
 */
import type { LeadSummary } from '../../../../src/types';
import type {
  LeadCountResponse,
  LeadFacetsResponse,
  SavedView,
  SavedViewCreateRequest,
  SavedViewListResponse,
  SavedViewMutationResponse,
} from '../../../../src/types/leadFilters';
import type { ContractSample } from '../contractSamples';
import { json, type FixtureReply, type FixtureRequest, type MockApi } from '../mockApi';
import { LEADS } from './borrowers';
import { SNAPSHOT_AT } from './reference';

function bound(query: URLSearchParams, key: string): number | null {
  const raw = query.get(key);
  return raw === null || raw.trim() === '' ? null : Number(raw);
}

/** The fixture population under the state, segment and score / spread bounds. */
export function boundedLeads(query: URLSearchParams): LeadSummary[] {
  const states = (query.get('state') ?? '').split(',').filter(Boolean).map((code) => code.toUpperCase());
  const segment = query.get('segment');
  const minScore = bound(query, 'min_opportunity_score');
  const maxScore = bound(query, 'max_opportunity_score');
  const minSpread = bound(query, 'min_rate_spread_bps');
  const maxSpread = bound(query, 'max_rate_spread_bps');
  return LEADS.filter((lead) => {
    if (states.length > 0 && !states.includes(lead.state)) return false;
    if (segment && !lead.segment_codes.includes(segment as LeadSummary['segment_codes'][number])) return false;
    if (minScore !== null && lead.opportunity_score < minScore) return false;
    if (maxScore !== null && lead.opportunity_score > maxScore) return false;
    // Like the SQL: a missing spread never matches a spread bound.
    if ((minSpread !== null || maxSpread !== null) && lead.rate_spread_bps === null) return false;
    if (minSpread !== null && (lead.rate_spread_bps ?? 0) < minSpread) return false;
    if (maxSpread !== null && (lead.rate_spread_bps ?? 0) > maxSpread) return false;
    return true;
  });
}

export function boundedLeadsPage({ query }: FixtureRequest): FixtureReply<LeadSummary[]> {
  const rows = boundedLeads(query);
  return json<LeadSummary[]>(rows, {
    headers: { 'X-Total-Matching': String(rows.length), 'X-Returned-Rows': String(rows.length) },
  });
}

export type SavedViewFailure = 'none' | 'duplicate' | 'name' | 'unavailable';

interface ErrorBody {
  detail: string | Array<{ loc: string[]; msg: string; type: string }>;
}

/** The ids the store hands out, in order: server-issued UUIDs. */
const VIEW_IDS = [
  '5a1e0000-0000-4000-8000-000000000001',
  '5a1e0000-0000-4000-8000-000000000002',
  '5a1e0000-0000-4000-8000-000000000003',
] as const;

export const SEEDED_VIEW: SavedView = {
  view_id: '5a1e0000-0000-4000-8000-0000000000aa',
  name: 'IL pending',
  params: 'approval_status=pending&state=IL',
  created_at: SNAPSHOT_AT,
  updated_at: SNAPSHOT_AT,
};

/**
 * An owner-scoped saved-views store for one spec. `failure` scripts the next
 * responses: a duplicate name (409), a refused name (422, the no-echo body
 * the API's validation handler sends) or a Lakebase outage (503 on the list).
 */
export class SavedViewsStore {
  views: SavedView[];
  failure: SavedViewFailure = 'none';
  readonly posted: SavedViewCreateRequest[] = [];
  readonly deleted: string[] = [];
  private nextId = 0;

  constructor(seed: readonly SavedView[] = [SEEDED_VIEW]) {
    this.views = [...seed];
  }

  register(mockApi: MockApi): void {
    mockApi.register<SavedViewListResponse | ErrorBody>('GET', '/api/workspace/saved-views', () =>
      this.failure === 'unavailable'
        ? json<ErrorBody>({ detail: 'Lakebase temporarily unavailable' }, { status: 503 })
        : json<SavedViewListResponse>({ saved_views: this.views }));
    mockApi.register<SavedViewMutationResponse | ErrorBody>('POST', '/api/workspace/saved-views', ({ body }) => {
      const request = body as SavedViewCreateRequest;
      this.posted.push(request);
      if (this.failure === 'duplicate') return json<ErrorBody>({ detail: 'A saved view with this name already exists' }, { status: 409 });
      if (this.failure === 'name') {
        return json<ErrorBody>({
          detail: [{ loc: ['body', 'name'], msg: 'Value error, saved view name must be a short public-safe label', type: 'value_error' }],
        }, { status: 422 });
      }
      const view: SavedView = {
        view_id: VIEW_IDS[this.nextId % VIEW_IDS.length],
        name: request.name,
        params: request.params,
        created_at: SNAPSHOT_AT,
        updated_at: SNAPSHOT_AT,
      };
      this.nextId += 1;
      this.views = [view, ...this.views];
      return json<SavedViewMutationResponse>({ ok: true, view_id: view.view_id, audit_event_id: 'a0000000-0000-4000-8000-000000000001' });
    });
    mockApi.register<SavedViewMutationResponse | ErrorBody>('DELETE', '/api/workspace/saved-views/:viewId', ({ params }) => {
      const found = this.views.some((view) => view.view_id === params.viewId);
      if (!found) return json<ErrorBody>({ detail: 'Saved view not found' }, { status: 404 });
      this.deleted.push(params.viewId);
      this.views = this.views.filter((view) => view.view_id !== params.viewId);
      return json<SavedViewMutationResponse>({ ok: true, view_id: params.viewId, audit_event_id: 'a0000000-0000-4000-8000-000000000002' });
    });
  }
}

/** Bodies of the W5a endpoints, for the fixture contract (each validates against its API model). */
export function contractSamples(): ContractSample[] {
  const facets: LeadFacetsResponse[] = [
    { dimension: 'state', total_matching: 3, buckets: [{ value: 'IL', count: 2 }, { value: 'TX', count: 1 }] },
    { dimension: 'segment', total_matching: 3, buckets: [{ value: 'itm', count: 2 }, { value: 'equity', count: 1 }] },
    { dimension: 'product', total_matching: 3, buckets: [{ value: 'Refi', count: 2 }, { value: 'HELOC', count: 1 }] },
    { dimension: 'approval', total_matching: 3, buckets: [{ value: 'pending', count: 3 }] },
  ];
  return [
    {
      source: 'LeadCountResponse',
      method: 'GET',
      pattern: '/api/leads/count',
      query: 'state=IL',
      body: { total_matching: 42 } satisfies LeadCountResponse,
    },
    ...facets.map((body): ContractSample => ({
      source: `LeadFacetsResponse(${body.dimension})`,
      method: 'GET',
      pattern: '/api/leads/facets',
      query: `dimension=${body.dimension}`,
      body,
    })),
    {
      source: 'SavedViewListResponse(seeded)',
      method: 'GET',
      pattern: '/api/workspace/saved-views',
      body: { saved_views: [SEEDED_VIEW] } satisfies SavedViewListResponse,
    },
    {
      source: 'SavedViewMutationResponse(saved)',
      method: 'POST',
      pattern: '/api/workspace/saved-views',
      body: { ok: true, view_id: VIEW_IDS[0], audit_event_id: 'a0000000-0000-4000-8000-000000000001' } satisfies SavedViewMutationResponse,
    },
    {
      source: 'SavedViewMutationResponse(deleted)',
      method: 'DELETE',
      pattern: '/api/workspace/saved-views/:viewId',
      path: `/api/workspace/saved-views/${SEEDED_VIEW.view_id}`,
      body: { ok: true, view_id: SEEDED_VIEW.view_id, audit_event_id: 'a0000000-0000-4000-8000-000000000002' } satisfies SavedViewMutationResponse,
    },
  ];
}
