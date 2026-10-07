/**
 * Spec-local handlers for palette-offer-sales-loop.fixture.spec.ts (W5d
 * w5-palette-offer-sales-loop): the palette's audit-free sources (the
 * actor's own my-events, one saved view, a saved lead for the Console), and a
 * campaign-bound Offer (the campaign read the binding is verified against,
 * a draft that echoes the binding). Nothing here registers by default.
 * Values are synthetic: masked ids, Summit Mortgage, `.example` mail.
 */
import type { ActorAuditEventPage, OutreachDraftResult } from '../../../../src/lib/apiTypes';
import type { CampaignSummary, WorkspaceState } from '../../../../src/types';
import type { SavedView, SavedViewListResponse } from '../../../../src/types/leadFilters';
import type { ContractSample } from '../contractSamples';
import { json, type FixtureRequest, type MockApi } from '../mockApi';
import { LEADS } from './borrowers';
import { outreachDraftFor } from './offers';
import { SNAPSHOT_AT } from './reference';

/** The borrowers this actor opened recently, newest first (masked ids). */
export const RECENT_IDS: readonly string[] = [LEADS[5].borrower_id, LEADS[6].borrower_id, LEADS[7].borrower_id];

export const RECENT_EVENTS: ActorAuditEventPage = {
  items: [
    ...RECENT_IDS.map((subject_id, index) => ({
      event_type: 'VIEW_BORROWER',
      entity_type: 'borrower',
      subject_id,
      created_at: `2026-07-14T14:${String(50 - index * 5).padStart(2, '0')}:00Z`,
    })),
    // Not a borrower open: never a Recent borrowers row.
    { event_type: 'VIEW_LEADS', entity_type: 'lead_queue', subject_id: null, created_at: '2026-07-14T14:30:00Z' },
  ],
  next_cursor: null,
};

export const PALETTE_VIEW: SavedView = {
  view_id: '5a1e0000-0000-4000-8000-0000000000b1',
  name: 'Texas refi review',
  params: 'segment=itm&state=TX',
  created_at: SNAPSHOT_AT,
  updated_at: SNAPSHOT_AT,
};

/** The palette's sources: the seeded my-events and one saved view. */
export function registerPaletteSources(mockApi: MockApi): void {
  mockApi.register<ActorAuditEventPage>('GET', '/api/audit/my-events', () => json<ActorAuditEventPage>(RECENT_EVENTS));
  mockApi.register<SavedViewListResponse>('GET', '/api/workspace/saved-views', () =>
    json<SavedViewListResponse>({ saved_views: [PALETTE_VIEW] }));
}

/** A workspace with one saved lead, for the Console's saved-workspace links. */
export const SAVED_LEAD_ID = LEADS[3].borrower_id;

export function registerSavedLead(mockApi: MockApi): void {
  const lead = LEADS[3];
  mockApi.register<WorkspaceState>('GET', '/api/workspace', () => json<WorkspaceState>({
    saved_leads: [{
      borrower_id: lead.borrower_id,
      city: lead.city,
      state: lead.state,
      zip: lead.zip,
      recommended_offer: 'Rate-and-term refinance',
      opportunity_score: lead.opportunity_score,
      confidence: lead.confidence,
      saved_at: SNAPSHOT_AT,
      updated_at: SNAPSHOT_AT,
    }],
    saved_drafts: [],
  }));
}

export const BOUND_CAMPAIGN_ID = 'cmp-fixture-0042';
export const BOUND_VARIANT = 'Variant A';

/** The campaign the Offer and the Lead Queue verify a binding against: one email variant. */
export const BOUND_CAMPAIGN = {
  campaign_id: BOUND_CAMPAIGN_ID,
  name: 'Texas refi wave',
  owner_email: 'growth.lead@summit-mortgage.example',
  status: 'approved',
  treatment_state: 'ready',
  actionable: true,
  actionability_issue: null,
  criteria: { states: ['IL'] },
  suppression_policy: {},
  message_variants: [{ variant_name: BOUND_VARIANT, channel: 'email' }],
  channel_cascade: [],
  send_window: {},
  holdout: null,
  roi_assumptions: null,
  household_dedup: { enabled: false, dedupe_unit: 'borrower', primary_contact_strategy: 'highest_opportunity_eligible' },
  household_summary: {
    enabled: false,
    candidate_borrower_count: 0,
    selected_primary_count: 0,
    suppressed_co_owner_count: 0,
    household_count: 0,
    owner_link_household_count: 0,
    mailing_address_household_count: 0,
    singleton_household_count: 0,
    primary_contact_strategy: 'highest_opportunity_eligible',
    source_assets: ['mip.gold.household_rollup', 'mip.gold.borrower_360'],
  },
  created_at: SNAPSHOT_AT,
  updated_at: SNAPSHOT_AT,
} satisfies CampaignSummary;

export function boundSearch(): string {
  return new URLSearchParams({ campaign_id: BOUND_CAMPAIGN_ID, variant_name: BOUND_VARIANT }).toString();
}

/** GET /api/campaigns/:id for the bound campaign, and a draft that echoes the binding. */
export function registerBoundCampaign(mockApi: MockApi): void {
  mockApi.register<CampaignSummary>('GET', '/api/campaigns/:campaignId', () => json<CampaignSummary>(BOUND_CAMPAIGN));
  mockApi.register<OutreachDraftResult>('POST', '/api/outreach/draft', (request: FixtureRequest) => {
    const body = (request.body ?? {}) as { campaign_id?: string | null; variant_name?: string | null };
    return json<OutreachDraftResult>({
      ...outreachDraftFor(request),
      campaign_id: body.campaign_id ?? null,
      variant_name: body.variant_name ?? null,
    });
  });
}

export function contractSamples(): ContractSample[] {
  return [
    {
      source: 'ActorAuditEventPage(palette recents)',
      method: 'GET',
      pattern: '/api/audit/my-events',
      query: 'limit=25',
      body: RECENT_EVENTS,
    },
    {
      source: 'SavedViewListResponse(palette)',
      method: 'GET',
      pattern: '/api/workspace/saved-views',
      body: { saved_views: [PALETTE_VIEW] } satisfies SavedViewListResponse,
    },
    {
      source: 'CampaignSummary(bound offer)',
      method: 'GET',
      pattern: '/api/campaigns/:campaignId',
      path: `/api/campaigns/${BOUND_CAMPAIGN_ID}`,
      body: BOUND_CAMPAIGN,
    },
  ];
}
