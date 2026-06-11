-- =============================================================================
-- gold_borrower_dossier.sql (transformation)
-- -----------------------------------------------------------------------------
-- Purpose:   Materialise `mip.gold.borrower_dossier` via CTAS. One row per
--            borrower_id carrying EVERYTHING the `/api/borrowers/{id}` dossier
--            payload needs, pre-joined so the read path is a single indexed
--            row lookup on the cluster key.
--
-- Grain:     One row per borrower_id (1:1 with mip.gold.borrower_360).
-- Pattern:   CREATE OR REPLACE TABLE ... AS SELECT. Full rebuild every
--            `mip_refresh_scores` run; mirrors the idempotency posture of
--            borrower_360, lead_population, and lockin_cohort.
-- Slice:     slice13-accuracy (perf — close the /api/borrowers/{id} p95 gap).
--
-- Why a dedicated dossier table (vs. the runtime fan-out):
--   - Slice 13 Wave 1 parallelised the borrower_360 + evidence_events fetch
--     into a 2-worker ThreadPoolExecutor and drove p95 from 4600 ms to
--     3300 ms. The remaining 3300 ms is warehouse-bound: two statements
--     per dossier request (SELECT borrower_360 row + SELECT evidence rows
--     for that CLIP). Even on a warm serverless warehouse the round-trip
--     floor sits around 1.2 s per statement.
--   - Folding evidence into borrower_360 as an ARRAY<STRUCT> pre-computed
--     collapses the read to one statement / one indexed row — a single
--     round-trip where the warehouse already has the bytes on Photon's
--     cluster cache. Target: p95 < 2000 ms (ideally < 1000 ms warm).
--   - Portable: every client that runs `databricks bundle deploy -t dev` +
--     `databricks bundle run mip_refresh_scores -t dev` gets the table.
--     No per-client grants beyond the ones already baked in for gold.
--
-- Evidence cap (20 rows per CLIP):
--   The gold.evidence_events controlled vocabulary has 13 live signal types
--   (permit / listing are BLOCKED); a single CLIP in the live warehouse
--   carries up to ~7–10 rows today. 20 is a comfortable ceiling that
--   covers all conceivable live signals per borrower with headroom for
--   Permits + MLS unblocking (+4 more) without forcing another schema
--   change. At the same time, 20 caps avg row size so a 5.16 M-row
--   rebuild lands in the same minutes-on-Photon regime as lead_population.
--
-- PII posture: identical to gold.borrower_360 — synthesized display_name,
-- no street / owner name / raw lender. Every column originates from
-- gold.borrower_360 or gold.evidence_events; the evidence STRUCT carries
-- only the EvidenceEvent-visible fields (no raw internal PII). The
-- repository layer still runs `redact_borrower_row` + `redact_evidence_row`
-- at the `/api/*` boundary; this CTAS changes shape, not redaction.
--
-- Self-contained refresh: operator or job runs
--   `databricks bundle run mip_refresh_scores -t dev`
-- after silver is current; no out-of-bundle steps.
--
-- 2026-06-11 audit P2-8: CTAS re-declares clustering/comments/properties
-- because COR TABLE drops DDL metadata on every refresh. Clustering, column
-- COMMENTs, and TBLPROPERTIES mirror the mip.gold.borrower_dossier block in
-- sql/ddl/003_gold_tables.sql (§10); the column list order matches the final
-- SELECT projection 1:1 (every borrower_360 column + the two evidence arrays).
-- =============================================================================

CREATE OR REPLACE TABLE mip.gold.borrower_dossier
CLUSTER BY (borrower_id)
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
)
AS
WITH evidence_full AS (
  -- Full evidence array per CLIP, ordered by signal_rank then evidence_id.
  -- Capped at 20 rows — see header for rationale. Collect in priority
  -- order so when the repository slices [:3] for trigger_timeline the
  -- head of the array IS the top-3.
  --
  -- Ordering contract: array_sort() after collect_list() is the
  -- Spark-guaranteed deterministic path. collect_list() itself does NOT
  -- preserve row order, even when the input subquery uses ROW_NUMBER()
  -- with an explicit ORDER BY (raised by Copilot 2026-04-22).
  -- Sorting by (signal_rank, evidence_id) locally via array_sort gives
  -- the same ordering the subquery window intended and matches the
  -- contract the Python repo + UI depend on.
  SELECT
    clip,
    array_sort(
      collect_list(ev),
      (a, b) -> CASE
        WHEN a.signal_rank  < b.signal_rank  THEN -1
        WHEN a.signal_rank  > b.signal_rank  THEN  1
        WHEN a.evidence_id  < b.evidence_id  THEN -1
        WHEN a.evidence_id  > b.evidence_id  THEN  1
        ELSE 0
      END
    ) AS evidence_events
  FROM (
    SELECT
      clip,
      struct(
        evidence_id,
        source_product,
        source_table,
        signal_type,
        signal_value,
        display_text,
        confidence,
        `timestamp`,
        signal_rank
      ) AS ev,
      ROW_NUMBER() OVER (PARTITION BY clip ORDER BY signal_rank, evidence_id) AS rn
    FROM mip.gold.evidence_events
    WHERE signal_type NOT IN ('permit', 'listing')
  ) ranked
  WHERE rn <= 20
  GROUP BY clip
),
evidence_top3 AS (
  -- Top-3 slice materialised separately so /api/borrowers/{id} can render
  -- the trigger timeline without touching the full array. Same
  -- array_sort-after-collect_list guarantee as evidence_full.
  SELECT
    clip,
    array_sort(
      collect_list(ev),
      (a, b) -> CASE
        WHEN a.signal_rank  < b.signal_rank  THEN -1
        WHEN a.signal_rank  > b.signal_rank  THEN  1
        WHEN a.evidence_id  < b.evidence_id  THEN -1
        WHEN a.evidence_id  > b.evidence_id  THEN  1
        ELSE 0
      END
    ) AS trigger_timeline
  FROM (
    SELECT
      clip,
      struct(
        evidence_id,
        source_product,
        source_table,
        signal_type,
        signal_value,
        display_text,
        confidence,
        `timestamp`,
        signal_rank
      ) AS ev,
      ROW_NUMBER() OVER (PARTITION BY clip ORDER BY signal_rank, evidence_id) AS rn
    FROM mip.gold.evidence_events
    WHERE signal_type NOT IN ('permit', 'listing')
  ) ranked
  WHERE rn <= 3
  GROUP BY clip
)
SELECT
  -- Every column from gold.borrower_360, 1:1 — the dossier is a
  -- superset of borrower_360, not a projection. The repository
  -- `_BORROWER_360_COLUMNS` + `trigger_timeline_json` list is pinned in
  -- Python; keep this SELECT list in sync.
  b.clip,
  b.borrower_id,
  b.display_name,
  b.city,
  b.state,
  b.zip,
  b.situs_cbsa_code,
  b.segment_codes,
  b.equity_estimate,
  b.equity_pct,
  b.rate_spread_bps,
  b.market_rate_fraction,
  b.opportunity_score,
  b.confidence,
  b.recommended_offer_code,
  b.recommended_offer,
  b.why_now,
  b.evidence_ids,
  b.approval_status,
  b.owner_link_id,
  b.subject_property,
  b.avm_value,
  b.current_lien_balance,
  b.current_rate,
  b.ltv,
  b.related_property_count,
  b.is_owner_occupied,
  b.is_absentee,
  b.is_corporate_owner,
  b.has_permit,
  b.listed_for_sale,
  b.is_investor,
  b.is_current_customer,
  b.is_former_customer,
  b.is_competitor_lien,
  b.has_first_party_relationship,
  b.first_party_relationship_depth,
  b.first_party_recent_interactions,
  b.first_party_recent_application,
  b.first_party_synthetic_demo,
  b.marketing_eligible,
  b.consent_status,
  b.suppression_reason,
  b.last_touch_at,
  b.eligible_recontact_at,
  b.current_lender_ref,
  b.second_pos_amount,
  b.first_pos_loan_type,
  b.owner_name_hash,
  b.min_spread_bps_applied,
  b.min_equity_pct_applied,
  b.heloc_equity_min_applied,
  b.cashout_equity_min_applied,
  b.retention_min_spread_applied,
  b.in_the_money,
  b.trigger_timeline_json,
  -- New dossier-only columns: pre-joined evidence payload.
  COALESCE(ef.evidence_events, ARRAY()) AS evidence_events,
  COALESCE(et.trigger_timeline, ARRAY()) AS trigger_timeline,
  -- Shared refresh_at captured once per run. See audit-holes-round-3 #7.
  (SELECT refresh_at FROM mip.ref.refresh_run_state ORDER BY captured_at DESC LIMIT 1) AS refreshed_at
FROM mip.gold.borrower_360 AS b
LEFT JOIN evidence_full AS ef ON ef.clip = b.clip
LEFT JOIN evidence_top3 AS et ON et.clip = b.clip;

-- Column comments re-applied post-CTAS (2026-06-11 audit P2-8 follow-up):
-- CREATE OR REPLACE drops DDL column comments on every refresh, and the
-- typeless CTAS column list is a PARSE_SYNTAX_ERROR on DBSQL (observed
-- live, run 2026-06-11). COMMENT ON COLUMN keeps the Genie grounding /
-- asset-page comments refresh-stable; the SQL file task executes the
-- statements in order.
COMMENT ON COLUMN mip.gold.borrower_dossier.clip IS 'CLIP. 1:1 with borrower_360.clip.';
COMMENT ON COLUMN mip.gold.borrower_dossier.borrower_id IS 'Synthetic id; cluster key. Matches borrower_360.borrower_id.';
COMMENT ON COLUMN mip.gold.borrower_dossier.display_name IS 'Synthesized label; never a real name.';
COMMENT ON COLUMN mip.gold.borrower_dossier.city IS 'Situs city.';
COMMENT ON COLUMN mip.gold.borrower_dossier.state IS 'Situs state from refreshed source coverage.';
COMMENT ON COLUMN mip.gold.borrower_dossier.zip IS '5-digit situs ZIP.';
COMMENT ON COLUMN mip.gold.borrower_dossier.situs_cbsa_code IS 'CBSA metro code.';
COMMENT ON COLUMN mip.gold.borrower_dossier.segment_codes IS 'Ordered SegmentCode list.';
COMMENT ON COLUMN mip.gold.borrower_dossier.equity_estimate IS 'USD.';
COMMENT ON COLUMN mip.gold.borrower_dossier.equity_pct IS '0..100.';
COMMENT ON COLUMN mip.gold.borrower_dossier.rate_spread_bps IS 'fn_rate_spread output.';
COMMENT ON COLUMN mip.gold.borrower_dossier.market_rate_fraction IS 'Fractional market rate.';
COMMENT ON COLUMN mip.gold.borrower_dossier.opportunity_score IS 'fn_lead_score output 0..100.';
COMMENT ON COLUMN mip.gold.borrower_dossier.confidence IS 'Mean of 5 sub-scores.';
COMMENT ON COLUMN mip.gold.borrower_dossier.recommended_offer_code IS 'fn_next_best_offer code.';
COMMENT ON COLUMN mip.gold.borrower_dossier.recommended_offer IS 'Human label.';
COMMENT ON COLUMN mip.gold.borrower_dossier.why_now IS 'Deterministic template per offer code.';
COMMENT ON COLUMN mip.gold.borrower_dossier.evidence_ids IS 'Ordered evidence ids.';
COMMENT ON COLUMN mip.gold.borrower_dossier.approval_status IS 'Default "pending"; Lakebase authoritative.';
COMMENT ON COLUMN mip.gold.borrower_dossier.owner_link_id IS 'Cotality Owner Link id.';
COMMENT ON COLUMN mip.gold.borrower_dossier.subject_property IS 'Synthetic city/state/ZIP5 string.';
COMMENT ON COLUMN mip.gold.borrower_dossier.avm_value IS 'AVM value; 0 when missing.';
COMMENT ON COLUMN mip.gold.borrower_dossier.current_lien_balance IS 'Total open lien balance.';
COMMENT ON COLUMN mip.gold.borrower_dossier.current_rate IS 'Percent form (5.75).';
COMMENT ON COLUMN mip.gold.borrower_dossier.ltv IS '0..100.';
COMMENT ON COLUMN mip.gold.borrower_dossier.related_property_count IS 'From gold.property_owner_bridge.';
COMMENT ON COLUMN mip.gold.borrower_dossier.is_owner_occupied IS 'owner_occupancy_code = "O".';
COMMENT ON COLUMN mip.gold.borrower_dossier.is_absentee IS 'From silver.property_master.';
COMMENT ON COLUMN mip.gold.borrower_dossier.is_corporate_owner IS 'From silver.property_master.';
COMMENT ON COLUMN mip.gold.borrower_dossier.has_permit IS 'BLOCKED: FALSE until Cotality Building Permits lands.';
COMMENT ON COLUMN mip.gold.borrower_dossier.listed_for_sale IS 'BLOCKED: FALSE until Cotality MLS Listings lands.';
COMMENT ON COLUMN mip.gold.borrower_dossier.is_investor IS 'Derived: multi-property OR corporate OR absentee.';
COMMENT ON COLUMN mip.gold.borrower_dossier.is_current_customer IS 'Current servicer is a tenant-lender alias in ref.lender_dictionary.';
COMMENT ON COLUMN mip.gold.borrower_dossier.is_former_customer IS 'Historical tenant-lender relationship with no current tenant lien.';
COMMENT ON COLUMN mip.gold.borrower_dossier.is_competitor_lien IS 'Current servicer is known and not a tenant-lender alias.';
COMMENT ON COLUMN mip.gold.borrower_dossier.has_first_party_relationship IS 'TRUE when optional first-party feeds resolve to this borrower.';
COMMENT ON COLUMN mip.gold.borrower_dossier.first_party_relationship_depth IS 'Bounded count of resolved first-party feed categories.';
COMMENT ON COLUMN mip.gold.borrower_dossier.first_party_recent_interactions IS 'Recent interaction count from the first-party engagement feed.';
COMMENT ON COLUMN mip.gold.borrower_dossier.first_party_recent_application IS 'TRUE when a recent first-party LOS/application event exists.';
COMMENT ON COLUMN mip.gold.borrower_dossier.first_party_synthetic_demo IS 'TRUE only for rows touched by the Summit demo_synthetic first-party seed.';
COMMENT ON COLUMN mip.gold.borrower_dossier.marketing_eligible IS 'From borrower_360; TRUE only when consent, suppression, and frequency-cap gates are clear.';
COMMENT ON COLUMN mip.gold.borrower_dossier.consent_status IS 'From borrower_360; opt_in / opt_out / unknown.';
COMMENT ON COLUMN mip.gold.borrower_dossier.suppression_reason IS 'From borrower_360; controlled suppression reason.';
COMMENT ON COLUMN mip.gold.borrower_dossier.last_touch_at IS 'From borrower_360; most recent first-party marketing/contact touch.';
COMMENT ON COLUMN mip.gold.borrower_dossier.eligible_recontact_at IS 'From borrower_360; earliest permitted re-contact time when capped.';
COMMENT ON COLUMN mip.gold.borrower_dossier.current_lender_ref IS 'Public-demo-safe current-servicer reference.';
COMMENT ON COLUMN mip.gold.borrower_dossier.second_pos_amount IS 'For "equity" segment predicate.';
COMMENT ON COLUMN mip.gold.borrower_dossier.first_pos_loan_type IS 'For fit sub-score.';
COMMENT ON COLUMN mip.gold.borrower_dossier.owner_name_hash IS 'sha2 hash from silver; internal only, router strips.';
COMMENT ON COLUMN mip.gold.borrower_dossier.min_spread_bps_applied IS 'Threshold this refresh.';
COMMENT ON COLUMN mip.gold.borrower_dossier.min_equity_pct_applied IS 'Threshold this refresh.';
COMMENT ON COLUMN mip.gold.borrower_dossier.heloc_equity_min_applied IS 'HELOC equity threshold this refresh.';
COMMENT ON COLUMN mip.gold.borrower_dossier.cashout_equity_min_applied IS 'Cash-out equity threshold this refresh.';
COMMENT ON COLUMN mip.gold.borrower_dossier.retention_min_spread_applied IS 'Retention spread threshold this refresh.';
COMMENT ON COLUMN mip.gold.borrower_dossier.in_the_money IS 'fn_in_the_money output.';
COMMENT ON COLUMN mip.gold.borrower_dossier.trigger_timeline_json IS 'JSON-encoded top-3 evidence rows (carried from borrower_360 for parity).';
COMMENT ON COLUMN mip.gold.borrower_dossier.evidence_events IS 'Full evidence array (capped at 20 per CLIP) sorted by signal_rank.';
COMMENT ON COLUMN mip.gold.borrower_dossier.trigger_timeline IS 'Top-3 slice of evidence_events for the trigger timeline.';
COMMENT ON COLUMN mip.gold.borrower_dossier.refreshed_at IS 'Refresh timestamp.';
