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
--   The gold.evidence_events controlled vocabulary has 12 live signal types
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
-- =============================================================================

CREATE OR REPLACE TABLE mip.gold.borrower_dossier AS
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
  b.is_competitor_lien,
  b.second_pos_amount,
  b.first_pos_loan_type,
  b.owner_name_hash,
  b.min_spread_bps_applied,
  b.min_equity_pct_applied,
  b.in_the_money,
  b.trigger_timeline_json,
  -- New dossier-only columns: pre-joined evidence payload.
  COALESCE(ef.evidence_events, ARRAY()) AS evidence_events,
  COALESCE(et.trigger_timeline, ARRAY()) AS trigger_timeline,
  CURRENT_TIMESTAMP() AS refreshed_at
FROM mip.gold.borrower_360 AS b
LEFT JOIN evidence_full AS ef ON ef.clip = b.clip
LEFT JOIN evidence_top3 AS et ON et.clip = b.clip;
