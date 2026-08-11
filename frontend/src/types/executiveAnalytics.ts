/**
 * Executive analytics contracts (`GET /api/v1/analytics/executive`).
 *
 * Mirrors backend/schemas/analytics.py. Every stage carries a `source` — the
 * same per-stage disclosure the approval funnel already publishes — and the
 * response carries a `provenance` block naming the as-of boundary of the gold
 * reads. The workflow stages (Approved, Actioned) come from the gold lifecycle
 * MIRROR of Lakebase, so they can trail the approval funnel tab, which reads
 * Lakebase directly. That lag is by design; the provenance block is how the
 * app says so instead of leaving two tabs quietly disagreeing.
 */

export interface FunnelTotals {
  snapshot_date?: string | null;
  addressable_borrowers: number;
  in_the_money_borrowers: number;
  high_opportunity_borrowers: number;
  offer_recommended_borrowers: number;
  approved_borrowers: number;
  actioned_borrowers: number;
}

export interface FunnelStage {
  stage: string;
  stage_order: number;
  borrower_count: number;
  source: string;
}

export interface ScoreBucket {
  score_bucket: number;
  borrower_count: number;
}

export interface ExecutiveProvenance {
  snapshot_date?: string | null;
  lifecycle_synced_at?: string | null;
  population_source: string;
  workflow_source: string;
  note: string;
}

export interface ExecutiveAnalyticsResponse {
  totals: FunnelTotals;
  stages: FunnelStage[];
  score_distribution: ScoreBucket[];
  provenance: ExecutiveProvenance;
}
