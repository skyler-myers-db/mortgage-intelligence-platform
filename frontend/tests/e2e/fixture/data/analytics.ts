/**
 * Analytics fixtures, one per dashboard tab: executive, geography, economics
 * (plus the scatter zoom points), segments and signals. The executive funnel
 * is built from `reference.TOTALS` so it agrees with the Home KPIs and map.
 */
import type {
  EconomicsAnalyticsResponse,
  EquitySpreadBin,
  EquitySpreadPointsResponse,
  ExecutiveAnalyticsResponse,
  GeographyAnalyticsResponse,
  SegmentAnalyticsResponse,
  SignalAnalyticsResponse,
} from '../../../../src/types';
import { fixture, json, type FixtureEntry } from '../mockApi';
import { BORROWERS } from './borrowers';
import { SNAPSHOT_AT, SNAPSHOT_DATE, STATES, TOTALS } from './reference';
import { SEGMENTS } from './segments';

const FUNNEL: ReadonlyArray<readonly [string, number, string]> = [
  ['addressable', TOTALS.addressable, 'mip.gold.borrower_360'],
  ['in_the_money', TOTALS.inTheMoney, 'mip.gold.borrower_360'],
  ['high_opportunity', TOTALS.highOpportunity, 'mip.gold.borrower_360'],
  ['offer_recommended', TOTALS.offersRecommended, 'mip.gold.borrower_360'],
  ['approved', TOTALS.approved, 'mip.gold.borrower_lifecycle_state'],
  ['actioned', TOTALS.actioned, 'mip.gold.borrower_lifecycle_state'],
];

/**
 * Score histogram. Buckets sum to TOTALS.addressable, and the 80+ buckets
 * (3,500) stay below the score-75+ headline (TOTALS.highOpportunity).
 */
export const SCORE_BUCKETS: ReadonlyArray<readonly [number, number]> = [
  [30, 3133], [40, 9200], [50, 18800], [60, 27620], [70, 27300], [80, 2480], [90, 1020],
];

/**
 * The canonical display band (backend scoring.score_band, mip.gold.fn_score_band,
 * src/lib/opportunityScore.ts scoreBand: high >= 85, med >= 65, else low).
 * EquitySpreadPoint rejects any other band for a score. Repeated here because
 * the fixture may only `import type` from src: keep 85 / 65 in step with
 * SCORE_BAND_HIGH_MIN / SCORE_BAND_MED_MIN there (the fixture contract test
 * catches a backend move, not a frontend-only one).
 */
function canonicalScoreBand(opportunityScore: number): 'high' | 'med' | 'low' {
  if (opportunityScore >= 85) return 'high';
  if (opportunityScore >= 65) return 'med';
  return 'low';
}

const EQUITY_BINS: EquitySpreadBin[] = [10, 20, 30, 40, 50, 60, 70].flatMap((equity, row) =>
  [0, 50, 100, 150, 200].map((spread, column) => ({
    equity_bin_pct: equity,
    spread_bin_bps: spread,
    borrower_count: 420 + ((row * 5 + column) * 137) % 2600,
    mean_opportunity_score: 58 + ((row + column) * 3) % 34,
    in_the_money_borrowers: spread >= 100 && equity >= 20 ? 180 + row * 40 : 0,
  })),
);

export const analyticsFixtures: FixtureEntry[] = [
  fixture('GET', '/api/analytics/executive', () =>
    json<ExecutiveAnalyticsResponse>({
      totals: {
        snapshot_date: SNAPSHOT_DATE,
        addressable_borrowers: TOTALS.addressable,
        in_the_money_borrowers: TOTALS.inTheMoney,
        high_opportunity_borrowers: TOTALS.highOpportunity,
        offer_recommended_borrowers: TOTALS.offersRecommended,
        approved_borrowers: TOTALS.approved,
        actioned_borrowers: TOTALS.actioned,
      },
      stages: FUNNEL.map(([stage, count, source], index) => ({
        stage,
        stage_order: index + 1,
        borrower_count: count,
        source,
      })),
      score_distribution: SCORE_BUCKETS.map(([bucket, count]) => ({ score_bucket: bucket, borrower_count: count })),
      provenance: {
        snapshot_date: SNAPSHOT_DATE,
        lifecycle_synced_at: SNAPSHOT_AT,
        population_source: 'mip.semantics.portfolio_headline_metric_view',
        workflow_source: 'mip.gold.borrower_lifecycle_state',
        note: 'Workflow stages read the gold lifecycle mirror and can trail the approval funnel tab.',
      },
    }),
  ),
  fixture('GET', '/api/analytics/geography', () =>
    json<GeographyAnalyticsResponse>({
      state_opportunities: STATES.map((state) => ({
        state: state.code,
        borrower_count: state.addressable,
        mean_opportunity_score: state.avgScore,
        in_the_money_borrowers: state.inTheMoney,
      })),
      state_avm_values: STATES.map((state) => ({
        state: state.code,
        total_avm_value_usd: state.addressable * 612000,
        total_lien_balance_usd: state.addressable * 318400,
        total_equity_usd: state.addressable * 293600,
      })),
      top_zips: STATES.map((state) => ({
        state: state.code,
        zip: state.zip,
        city: state.city,
        borrower_count: Math.round(state.addressable * 0.18),
        in_the_money_borrowers: Math.round(state.inTheMoney * 0.18),
        mean_opportunity_score: state.avgScore + 2,
        mean_rate_spread_bps: 104 + state.avgScore - 75,
      })),
    }),
  ),
  fixture('GET', '/api/analytics/economics', () =>
    json<EconomicsAnalyticsResponse>({
      rate_spread_histogram: [0, 25, 50, 75, 100, 125, 150, 175, 200].map((bucket, index) => ({
        spread_bucket_bps: bucket,
        borrower_count: [21400, 18900, 14200, 11800, 9100, 6300, 4100, 2400, 1353][index],
      })),
      equity_spread: {
        bins: EQUITY_BINS,
        total_borrowers: TOTALS.addressable,
        equity_bin_pct: 10,
        spread_bin_bps: 50,
        equity_domain_min: 0,
        equity_domain_max: 80,
        spread_domain_min: 0,
        spread_domain_max: 250,
        source_table: 'mip.gold.borrower_360',
        refreshed_at: SNAPSHOT_AT,
      },
      top_borrowers: BORROWERS.slice(0, 10).map((borrower, index) => ({
        borrower_id: borrower.borrower_id,
        display_name: borrower.display_name,
        state: borrower.state,
        city: borrower.city,
        opportunity_score: borrower.opportunity_score,
        rate_spread_bps: borrower.rate_spread_bps,
        equity_pct: borrower.why_panel.equity_pct,
        recommended_offer: borrower.recommended_offer,
        rank_overall: index + 1,
      })),
    }),
  ),
  fixture('GET', '/api/analytics/economics/points', ({ query }) => {
    const points = BORROWERS.map((borrower) => ({
      borrower_id: borrower.borrower_id,
      display_name: borrower.display_name,
      segment: borrower.segment_codes[0],
      state: borrower.state,
      equity_pct: borrower.why_panel.equity_pct,
      rate_spread_bps: borrower.rate_spread_bps,
      opportunity_score: borrower.opportunity_score,
      coordinate_total: 1,
      score_band: canonicalScoreBand(borrower.opportunity_score),
      in_the_money: borrower.why_panel.in_the_money,
    }));
    return json<EquitySpreadPointsResponse>({
      points,
      total_matching: points.length,
      showing: points.length,
      point_cap: 500,
      truncated: false,
      viewport: {
        equity_min: Number(query.get('equity_min') ?? 0),
        equity_max: Number(query.get('equity_max') ?? 80),
        spread_min: Number(query.get('spread_min') ?? 0),
        spread_max: Number(query.get('spread_max') ?? 250),
      },
      source_table: 'mip.gold.borrower_360',
      refreshed_at: SNAPSHOT_AT,
    });
  }),
  fixture('GET', '/api/analytics/segments', () =>
    json<SegmentAnalyticsResponse>({
      scope: { code: 'all', label: 'All segments', description: 'Every reviewed Module 0 segment.' },
      overview: SEGMENTS.map((segment) => ({
        segment_code: segment.code,
        name: segment.name,
        borrower_count: segment.count,
        mean_opportunity_score: segment.avg_score,
        delta_vs_prior_label: segment.delta,
        description: segment.description,
        // Percent units: the Segments tab renders `${approval_rate.toFixed(1)}%`.
        approval_rate: 3.4,
        outreach_rate: 2.5,
        mean_rate_spread_bps: 112,
        mean_equity_pct: 46,
        in_the_money_borrowers: Math.round(segment.count * 0.4),
      })),
      counts: SEGMENTS.map((segment) => ({ segment_code: segment.code, segment_name: segment.name, value: segment.count })),
      average_scores: SEGMENTS.map((segment) => ({ segment_code: segment.code, segment_name: segment.name, value: segment.avg_score })),
      by_state: STATES.slice(0, 4).flatMap((state) =>
        SEGMENTS.slice(0, 3).map((segment) => ({
          state: state.code,
          segment_code: segment.code,
          segment_name: segment.name,
          borrower_count: Math.round((segment.count * state.addressable) / TOTALS.addressable),
        })),
      ),
      top_segments_by_state: STATES.map((state) => {
        const top = SEGMENTS.find((segment) => segment.code === state.topSegment) ?? SEGMENTS[0];
        return {
          state: state.code,
          segment_code: top.code,
          segment_name: top.name,
          borrower_count: Math.round((top.count * state.addressable) / TOTALS.addressable),
          state_rank: 1,
        };
      }),
    }),
  ),
  fixture('GET', '/api/analytics/signals', () =>
    json<SignalAnalyticsResponse>({
      evidence_daily: ['2026-07-08', '2026-07-09', '2026-07-10', '2026-07-11', '2026-07-12', '2026-07-13', '2026-07-14'].flatMap((date, index) => [
        { event_date: date, signal_type: 'rate_spread', event_count: 1840 + index * 120 },
        { event_date: date, signal_type: 'equity', event_count: 1260 + index * 80 },
        { event_date: date, signal_type: 'market_trend', event_count: 640 + index * 30 },
      ]),
      evidence_by_signal: [
        { signal_type: 'rate_spread', source_product: 'Voluntary Lien', source_table: 'cotality.liens.voluntary_lien', source_label: 'Voluntary Lien', event_count: 15400, mean_confidence: 0.92, confidence_source: 'source_row', confidence_label: 'High' },
        { signal_type: 'equity', source_product: 'AVM', source_table: 'cotality.avm.current', source_label: 'AVM', event_count: 10500, mean_confidence: 0.88, confidence_source: 'source_row', confidence_label: 'High' },
        { signal_type: 'market_trend', source_product: 'Mortgage Market Analytics', source_table: 'cotality.mma.refi_activity', source_label: 'Mortgage Market Analytics', event_count: 5100, mean_confidence: 0.84, confidence_source: 'source_row', confidence_label: 'Medium' },
      ],
      evidence_examples: BORROWERS.slice(0, 6).map((borrower) => {
        const event = borrower.evidence_events[0];
        return {
          borrower_id: borrower.borrower_id,
          display_name: borrower.display_name,
          state: borrower.state,
          signal_type: event.signal_type,
          source_product: event.source_product,
          signal_value: event.signal_value,
          display_text: event.display_text,
          confidence: event.confidence,
          timestamp: event.timestamp,
        };
      }),
    }),
  ),
];
