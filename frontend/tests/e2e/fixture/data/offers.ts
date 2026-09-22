/**
 * Offer Orchestrator fixtures: the primary-offer recommendation and the
 * governed outreach draft.
 *
 * Both echo the borrower's `source_refreshed_at` (reference.SNAPSHOT_AT). The
 * route compares the borrower, offer and draft snapshots and renders a
 * mismatch error when they differ, so these three must move together.
 *
 * `POST /api/outreach/draft` writes a DRAFT_OUTREACH audit row in the real
 * backend. The fixture has no side effect, but tests must still only trigger
 * it the way a user would (opening the orchestrator), never from hover,
 * prefetch or polling.
 */
import type { OfferRecommendation } from '../../../../src/types';
import type { OutreachDraftResult } from '../../../../src/lib/apiTypes';
import { fixture, json, type FixtureEntry, type FixtureRequest } from '../mockApi';
import { PRIMARY_BORROWER, borrowerById } from './borrowers';
import { LENDER_NAME, SNAPSHOT_AT } from './reference';

function requestedBorrowerId(request: FixtureRequest): string {
  const body = request.body as { borrower_id?: unknown } | null;
  return typeof body?.borrower_id === 'string' ? body.borrower_id : PRIMARY_BORROWER.borrower_id;
}

function requestedChannel(request: FixtureRequest): OutreachDraftResult['channel'] {
  const channel = (request.body as { channel?: unknown } | null)?.channel;
  return channel === 'sms' || channel === 'direct_mail' ? channel : 'email';
}

export const offerFixtures: FixtureEntry[] = [
  fixture('POST', '/api/offers/recommend', (request) => {
    const borrower = borrowerById(requestedBorrowerId(request)) ?? PRIMARY_BORROWER;
    return json<OfferRecommendation>({
      borrower_id: borrower.borrower_id,
      source_refreshed_at: SNAPSHOT_AT,
      offer_code: borrower.recommended_offer_code ?? 'refi',
      offer_type: 'refi',
      product_label: borrower.recommended_offer,
      confidence: 0.88,
      rationale: `Lien rate is ${borrower.rate_spread_bps} bps above par with ${borrower.why_panel.equity_pct}% equity.`,
      evidence_ids: borrower.evidence_ids,
      sources: ['mip.gold.fn_next_best_offer', 'mip.gold.fn_in_the_money'],
      source_labels: [
        { name: 'mip.gold.fn_next_best_offer', display_label: 'Primary offer rules' },
        { name: 'mip.gold.fn_in_the_money', display_label: 'In-the-money rule' },
      ],
      alternatives: [
        { offer_code: 'heloc', product_label: 'HELOC only', reason_not_chosen: 'Rate spread clears the refinance threshold, so a combined offer ranks higher.' },
        { offer_code: 'cash_out', product_label: 'Cash-out refinance', reason_not_chosen: 'LTV after cash-out would exceed the lender overlay.' },
      ],
      thresholds_applied: { min_spread_bps: 75, min_equity_pct: 15 },
    });
  }),
  fixture('POST', '/api/outreach/draft', (request) => {
    const borrower = borrowerById(requestedBorrowerId(request)) ?? PRIMARY_BORROWER;
    const channel = requestedChannel(request);
    return json<OutreachDraftResult>({
      generation_id: 'gen-fixture-0001',
      response_hash: 'c'.repeat(64),
      source_refreshed_at: SNAPSHOT_AT,
      borrower_id: borrower.borrower_id,
      campaign_id: null,
      variant_name: null,
      offer_code: borrower.recommended_offer_code ?? 'refi',
      channel,
      subject: channel === 'email' ? 'A quick review of your mortgage options' : null,
      body: [
        'Hello,',
        'Based on current market rates and your estimated home equity, a mortgage review may lower your monthly cost. A loan officer can walk you through the numbers, with no obligation.',
        `${LENDER_NAME} · NMLS #000000 · Equal Housing Lender`,
      ].join('\n\n'),
      status: 'draft',
      disclosure_version: 'fixture-2026-07',
      disclosure_state: borrower.state,
      marketing_eligible: true,
      generation_mode: 'governed_fallback',
      generator_label: 'Reviewed outreach template',
      strategy_summary: 'Benefit-led framing; human approval is required before any send.',
      evidence_summary: borrower.evidence_events.map((event) => event.display_text),
      evidence_assets: ['mip.gold.borrower_360', 'mip.gold.evidence_events'],
    });
  }),
];
