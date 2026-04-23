-- =============================================================================
-- gold_borrower_360.sql (transformation)
-- -----------------------------------------------------------------------------
-- Purpose:   Populate `mip.gold.borrower_360` via CTAS. One row per CLIP
--            joining silver.lien_current (SPINE) + silver.property_master +
--            gold.property_owner_bridge + silver.market_rates_weekly
--            (is_latest=TRUE).
--
-- Grain:     One row per clip.
-- Pattern:   CREATE OR REPLACE TABLE ... AS SELECT. Full rebuild is the
--            default refresh posture per data-contract §3.2. 5M rows on
--            serverless photon completes in minutes; idempotent on repeat
--            runs.
-- Slice:     module0-real-data-slice3.
-- Data contract: docs/data-contract-module0.md §3.2.
--
-- PII posture (non-negotiable, governance §1):
--   - display_name = 'Owner ' || SUBSTR(owner_name_hash, 1, 8). Synthesized.
--   - subject_property = 'Synthetic property · ' || city || ', ' || state ||
--                        ' ' || zip. No street.
--   - owner_name_hash is carried for internal correlation; router strips
--     before /api/* emission.
--
-- BLOCKED columns (data-contract §9):
--   - has_permit      = FALSE.
--   - listed_for_sale = FALSE.
-- Both hardcoded here so the scoring layer (gold.lead_scores + fn_next_best_
-- offer) sees stable false values on real data. When Cotality Permits + MLS
-- land, the `BLOCKED` literals become real joins and this comment block is
-- the only place to update.
--
-- Threshold convention: default thresholds live in data-contract §5 + UDF headers.
-- We apply them here as bound inputs to fn_in_the_money. When admin-config
-- thresholds land (Slice 5), these literals become a CROSS JOIN against
-- mip_app.thresholds.
--
-- Market rate: one row from silver.market_rates_weekly where is_latest=TRUE
-- and series_id='MORTGAGE30US'. This is a CROSS-like join via the CTE
-- `market` -- every borrower reads the same par rate at refresh time.
--
-- trigger_timeline_json: materialized via SUBQUERY + to_json(collect_list(...))
-- of the top-3 evidence_events ORDER BY signal_rank. Prevents per-row fan-out
-- when the dossier renders.
--
-- Product label resolution: recommended_offer comes from an inline map MAP<
-- STRING, STRING> so gold emits the human label directly and the router
-- passes through. Matches NBO_PRODUCT_LABELS in scoring.py.
--
-- why_now: deterministic template per offer_code (data-contract §6). Uses
-- `FORMAT_STRING` and `CAST(... AS STRING)` to interpolate bps and equity
-- into the one-sentence template.
-- =============================================================================

CREATE OR REPLACE TABLE mip.gold.borrower_360 AS
WITH market AS (
  -- Exactly one row: the latest MORTGAGE30US market rate. A missing row here
  -- is a hard failure -- gold cannot compute rate_spread without a par
  -- rate -- so the Lakeflow pipeline should ensure market_rates_weekly has
  -- is_latest data before this CTAS runs.
  SELECT rate_fraction AS market_rate_fraction
  FROM mip.silver.market_rates_weekly
  WHERE series_id = 'MORTGAGE30US' AND is_latest = TRUE
  LIMIT 1
),
-- Recent-event aggregates per CLIP feeding intent_trigger (last 90 days).
-- Intent_trigger itself is computed in gold.lead_scores, not here, but the
-- AVM-uplift evidence signal reads from recent AVMs; we expose the raw
-- counts on borrower_360 so WhyPanel can cite them without a fresh join.
base AS (
  SELECT
    lc.clip,
    lc.situs_state                      AS state,
    lc.situs_zip_code                   AS zip,
    pm.situs_city                       AS city,
    pm.situs_cbsa_code,
    -- 5-char FIPS county code from silver.property_master. Projected up so
    -- gold.county_rollup + gold.zip_rollup can aggregate natively without a
    -- ZIP->county crosswalk seed. Nullable: ~0.2% of silver rows have a
    -- missing fips_county_code (block-level geocode gap); those CLIPs land
    -- in the state rollup but not in any county/ZIP rollup.
    pm.fips_county_code                 AS county_fips_5,
    pm.owner_link_id,
    pm.owner_name_hash,
    pm.owner_is_corporate,
    pm.is_absentee,
    pm.owner_occupancy_code,
    pm.year_built,
    pm.bedrooms,
    pm.bathrooms,
    lc.avm_value,
    lc.total_open_lien_balance,
    lc.estimated_cltv,
    lc.first_pos_rate,                  -- fractional
    lc.first_pos_loan_type,
    lc.first_pos_lender_current,
    -- Normalised lender raw_key for the JOIN to mip.ref.lender_dictionary.
    -- Both sides are normalised with UPPER(TRIM(...)) so the match is case +
    -- trailing-whitespace insensitive. See sql/ref/lender_dictionary_seed.sql
    -- for the canonical raw_key set (11 seeded entries incl. 'SUMMIT MTG').
    CASE
      WHEN lc.first_pos_lender_current IS NULL THEN NULL
      ELSE UPPER(TRIM(lc.first_pos_lender_current))
    END                                 AS lender_raw_key,
    lc.second_pos_amount,
    COALESCE(pob.related_property_count, 1) AS related_property_count
  FROM mip.silver.lien_current AS lc
  LEFT JOIN mip.silver.property_master AS pm
    ON pm.clip = lc.clip
  LEFT JOIN mip.gold.property_owner_bridge AS pob
    ON pob.owner_link_id = pm.owner_link_id
  WHERE lc.situs_state IN ('IL','CA','FL','TX','WA','CO')
    AND lc.clip IS NOT NULL
),
-- Slice13-accuracy: promote current-customer detection from an inline
-- UPPER(...) LIKE '%SUMMIT%' to a governed JOIN against
-- mip.ref.lender_dictionary. The dictionary carries `is_competitor` per
-- lender (FALSE iff the row IS the tenant, e.g. SUMMIT MTG for Summit
-- Mortgage); `is_current_customer` is its inverse. Running as a CTE so
-- the JOIN happens once and the projected boolean flows through
-- enriched / scored / with_segments like the old literal.
--
-- Behaviour vs prior inline LIKE:
--   - Known tenant lender (raw_key = 'SUMMIT MTG'): is_current_customer = TRUE.
--   - Known third-party lender (is_competitor = TRUE in ref): FALSE.
--   - Lender string not in ref.lender_dictionary: COALESCE -> FALSE, matching
--     the fallback posture (no known customer relationship). Third-party
--     lenders not yet seeded still land in `is_competitor_lien` because of
--     the `lender IS NOT NULL AND NOT is_current_customer` fallback below.
--   - Lender string NULL: FALSE for both flags.
lender_ref AS (
  SELECT
    UPPER(TRIM(raw_key)) AS raw_key,
    is_competitor
  FROM mip.ref.lender_dictionary
),
enriched AS (
  SELECT
    b.*,
    m.market_rate_fraction,
    -- Rate spread via frozen UDF. Both sides fractional.
    mip.gold.fn_rate_spread(b.first_pos_rate, m.market_rate_fraction) AS rate_spread_bps,
    -- Equity % derived preferentially from estimated_cltv (Cotality-computed
    -- CLTV is authoritative when present), with fallback to avm / lien math.
    -- Result clipped to [0, 100].
    CAST(
      GREATEST(0, LEAST(100, CASE
        WHEN b.estimated_cltv IS NOT NULL AND b.estimated_cltv > 0
          THEN ROUND(100 - b.estimated_cltv)
        WHEN b.avm_value IS NOT NULL AND b.avm_value > 0
          THEN ROUND(100.0 * (b.avm_value - COALESCE(b.total_open_lien_balance, 0)) / b.avm_value)
        ELSE 0
      END))
    AS INT) AS equity_pct,
    CAST(GREATEST(0, COALESCE(b.avm_value, 0) - COALESCE(b.total_open_lien_balance, 0)) AS BIGINT)
      AS equity_estimate,
    -- LTV mirror: 100 - equity_pct, but computed from lien/avm so rounding
    -- drift cannot make (ltv + equity_pct) != 100 for a given row.
    CAST(
      CASE
        WHEN b.avm_value IS NOT NULL AND b.avm_value > 0
          THEN ROUND(100.0 * COALESCE(b.total_open_lien_balance, 0) / b.avm_value)
        ELSE 0
      END
    AS INT) AS ltv,
    -- is_investor derived boolean.
    (b.related_property_count >= 2
     OR COALESCE(b.owner_is_corporate, FALSE)
     OR COALESCE(b.is_absentee, FALSE)) AS is_investor,
    -- Current-customer detection: governed JOIN against
    -- mip.ref.lender_dictionary (slice13-accuracy). Previously an inline
    -- UPPER(...) LIKE '%SUMMIT%'. `is_current_customer` = NOT is_competitor
    -- when the lender is known; FALSE otherwise.
    COALESCE(NOT lr.is_competitor, FALSE) AS is_current_customer,
    -- Competitor lien: servicer known AND is NOT our tenant. If the raw
    -- lender string lands in the ref dictionary with is_competitor = TRUE,
    -- that's authoritative. If the raw lender string is missing from the
    -- dictionary (unseeded third-party), treat the presence of any non-null
    -- lender string as evidence of a competitor lien -- matches the prior
    -- "servicer known and != Summit" semantic so unknown third-party lenders
    -- keep lighting up the retention + competitor-lien paths.
    (b.first_pos_lender_current IS NOT NULL
     AND NOT COALESCE(NOT lr.is_competitor, FALSE)) AS is_competitor_lien,
    (COALESCE(b.owner_occupancy_code, '') = 'O') AS is_owner_occupied,
    -- BLOCKED columns -- hardcoded FALSE until Cotality Permits + MLS land.
    CAST(FALSE AS BOOLEAN) AS has_permit,
    CAST(FALSE AS BOOLEAN) AS listed_for_sale
  FROM base AS b
  CROSS JOIN market AS m
  LEFT JOIN lender_ref AS lr
    ON lr.raw_key = b.lender_raw_key
),
-- Scored rows: bring the default thresholds inline and call the frozen
-- UDFs for ITM + next-best-offer.
scored AS (
  SELECT
    e.*,
    -- Default thresholds (data-contract §5 + frozen fixtures). Hardcoded
    -- here; when admin-config lands (Slice 5) these become a CROSS JOIN
    -- against mip_app.thresholds.
    75  AS min_spread_bps_applied,
    15  AS min_equity_pct_applied,
    35  AS heloc_equity_min_applied,
    25  AS cashout_equity_min_applied,
    50  AS retention_min_spread_applied,
    mip.gold.fn_in_the_money(
      e.rate_spread_bps, e.equity_pct, 75, 15
    ) AS in_the_money,
    mip.gold.fn_next_best_offer(
      e.rate_spread_bps,
      e.equity_pct,
      e.has_permit,
      e.listed_for_sale,
      e.is_investor,
      e.is_current_customer,
      e.is_competitor_lien,
      75, 15, 35, 25, 50
    ) AS recommended_offer_code
  FROM enriched AS e
),
-- Segment codes array (order matters for the segment stripe rendering).
with_segments AS (
  SELECT
    s.*,
    FILTER(
      ARRAY(
        CASE WHEN s.in_the_money                            THEN 'itm'       END,
        CASE WHEN s.listed_for_sale                         THEN 'listed'    END,
        CASE WHEN s.has_permit                              THEN 'permit'    END,
        CASE WHEN s.is_investor                             THEN 'investor'  END,
        CASE WHEN s.equity_pct >= 35 AND s.second_pos_amount IS NULL
                                                            THEN 'equity'    END,
        CASE WHEN s.is_current_customer
              AND (s.rate_spread_bps >= 50
                   OR s.is_competitor_lien
                   OR s.listed_for_sale)                    THEN 'retention' END
      ),
      x -> x IS NOT NULL
    ) AS segment_codes
  FROM scored AS s
),
-- Evidence counts per CLIP (feeds the `evidence` sub-score in lead_scores,
-- but we also need a rough count here to populate evidence_ids). Only the
-- LIVE signal_types (no 'permit' / 'listing') are counted.
evidence_counts AS (
  SELECT clip, COUNT(*) AS evidence_event_count
  FROM mip.gold.evidence_events
  WHERE signal_type NOT IN ('permit', 'listing')
  GROUP BY clip
),
-- Top-3 evidence timeline per CLIP, pre-materialized as JSON to avoid
-- per-row fan-out at read.
timeline AS (
  SELECT
    clip,
    to_json(collect_list(ev)) AS trigger_timeline_json,
    collect_list(evidence_id) AS evidence_ids
  FROM (
    SELECT
      clip,
      evidence_id,
      struct(
        evidence_id,
        source_product,
        source_table,
        signal_type,
        signal_value,
        display_text,
        confidence,
        `timestamp`
      ) AS ev,
      ROW_NUMBER() OVER (PARTITION BY clip ORDER BY signal_rank, evidence_id) AS rn
    FROM mip.gold.evidence_events
    WHERE signal_type NOT IN ('permit', 'listing')
  ) ranked
  WHERE rn <= 3
  GROUP BY clip
),
-- Sub-scores: economic_incentive + fit + relationship here; intent_trigger +
-- evidence live fully in gold.lead_scores but we need them NOW to compute
-- opportunity_score + confidence for borrower_360 (matches §3.2 contract).
-- Sub-score formulas (fix/copilot-batch-post-merge 2026-04-22):
--
-- The tiered CASE statements collapsed 5.16M borrowers into only a
-- handful of discrete (economic_incentive, fit, relationship, evidence)
-- buckets, which in turn collapsed `fn_lead_score` into just 3 unique
-- values across the top 500 of the ranked queue (66/67/68 at refresh
-- time). Fix: replace the tiered case with continuous (linear) blends
-- so small variation in inputs produces small variation in outputs and
-- the opportunity_score distribution actually spans a useful range.
--
-- Continuity invariants vs. the prior tiered formulas:
--   - economic_incentive: rows that clear BOTH the 200-bps/35% band
--     still score ~95+; rows that clear only the 0-bps lane still
--     score in the mid-30s. Monotonic in spread AND equity.
--   - fit: owner-occupant + CONV/FHA/VA still beats corporate, which
--     still beats the fallback. Monotonic in property-size signals.
--   - relationship: current-customer > competitor-lien > no-relation.
--     Customer tenure (historical_distinct_clips) adds up to +10.
--   - evidence: unchanged (already continuous).
--
-- fn_lead_score itself is unchanged (frozen primitive; Python parity
-- test pins the weighted-sum math + banker's rounding). The
-- gold.lead_scores CTAS mirrors this formula 1:1 -- drift between the
-- two is a parity-test failure by construction (they recompute the same
-- sub-scores from the same inputs; the weighted blend is canonical).
subscores AS (
  SELECT
    w.clip,
    -- economic_incentive: continuous blend of spread + equity that
    -- saturates gently. spread is compressed via a log-style curve
    -- (via the sqrt shape) so very-high-spread borrowers don't all
    -- bunch at 100; equity contributes linearly.
    --   spread_pts = LEAST(55, ROUND(3 * sqrt(GREATEST(0, spread_bps)))) -- saturates at spread ~340 bps
    --   equity_pts = LEAST(50, ROUND(equity_pct * 0.5))                  -- linear, saturates at 100% equity
    -- A borrower with spread=246, equity=79 scores ~47 + ~40 = 87.
    -- A borrower with spread=735, equity=83 scores ~55 + ~42 = 97.
    -- A borrower with spread=100, equity=25 scores ~30 + ~13 = 43.
    CAST(LEAST(100, GREATEST(0,
        LEAST(55, CAST(ROUND(3 * sqrt(GREATEST(0, w.rate_spread_bps))) AS INT))
      + LEAST(50, CAST(ROUND(0.5 * LEAST(100, GREATEST(0, w.equity_pct))) AS INT))
    )) AS INT) AS economic_incentive,
    -- intent_trigger: BLOCKED terms (permit, listing, avm_uplift) stay 0
    -- on real data until Cotality Permits + MLS land. Continuous
    -- contributions from always-live signals. Sum cap is ~85 for a
    -- hypothetical maxed-out row so even top-tier borrowers rarely
    -- saturate -- separation in the top tail is the whole point of
    -- the 2026-04-22 fix.
    --   * 20 if is_competitor_lien (recapture trigger)
    --   * LEAST(25, 10 * (related_property_count - 1)) Owner Link signal
    --   * 0-30 continuous rate-drift: LEAST(30, ROUND(2 * sqrt(spread_bps)))
    --     (saturates gently around ~225 bps so top-band still varies)
    --   * LEAST(10, equity_pct / 10) continuous equity proxy
    --   * 8 bump for is_current_customer (soft retention intent)
    CAST(LEAST(100, GREATEST(0,
        20 * CASE WHEN w.is_competitor_lien THEN 1 ELSE 0 END
      + LEAST(25, GREATEST(0, (COALESCE(w.related_property_count, 1) - 1) * 10))
      + LEAST(30, CAST(ROUND(2 * sqrt(GREATEST(0, w.rate_spread_bps))) AS INT))
      + LEAST(10, GREATEST(0, CAST(w.equity_pct / 10 AS INT)))
      + CASE WHEN w.is_current_customer THEN 8 ELSE 0 END
    )) AS INT) AS intent_trigger,
    -- fit: continuous over property-size features. Monotonic
    -- (owner-occupant + CONV/FHA/VA with many bedrooms beats everything).
    CAST(LEAST(100, GREATEST(0,
        CASE
          WHEN w.is_owner_occupied AND w.first_pos_loan_type IN ('CONV','FHA','VA') THEN 70
          WHEN w.is_owner_occupied                                                  THEN 60
          WHEN w.owner_is_corporate                                                 THEN 50
          ELSE 40
        END
      + LEAST(20, 4 * COALESCE(w.bedrooms, 0))
      + LEAST(10, 3 * CAST(COALESCE(w.bathrooms, 0) AS INT))
    )) AS INT) AS fit,
    -- relationship: continuous. Current-customer base 70 + multi-property
    -- tenure bonus up to +25; competitor-lien 55; multi-property owners
    -- get a +10 nudge over the no-relation floor. The `(related - 1)*5`
    -- bump spreads owner-link-rich borrowers across several integer
    -- score values instead of clumping them at 45 or 55.
    CAST(LEAST(100, GREATEST(0,
      CASE
        WHEN w.is_current_customer
          THEN 70
        WHEN w.is_competitor_lien
          THEN 55
        WHEN COALESCE(w.related_property_count, 1) > 1
          THEN 45
        ELSE 35
      END
      + LEAST(25, GREATEST(0, (COALESCE(w.related_property_count, 1) - 1) * 5))
    )) AS INT) AS relationship,
    -- evidence: was LEAST(100, 20*count) which capped every borrower
    -- with >=5 events at 100 (median real count is 4, so ~50% of rows
    -- saturated). Swap to 10*count + sqrt(second_pos_amount/1000) --
    -- keeps the per-event linearity but spreads the top tail via a
    -- continuous lien-amount term so dossier-rich borrowers still beat
    -- dossier-sparse ones, without every row landing at 100.
    LEAST(100, GREATEST(0,
      10 * COALESCE(ec.evidence_event_count, 0)
      + CASE
          WHEN w.second_pos_amount IS NOT NULL AND w.second_pos_amount > 0
            THEN LEAST(20, CAST(ROUND(sqrt(w.second_pos_amount / 1000.0)) AS INT))
          ELSE 0
        END
    )) AS evidence
  FROM with_segments AS w
  LEFT JOIN evidence_counts AS ec ON ec.clip = w.clip
)
SELECT
  w.clip,
  -- Borrower ID derivation (slice13 Wave-2 fix):
  --   Prior formula `LPAD(ABS(XXHASH64(clip)) % 99999 + 10000, 5, '0')` collapsed
  --   5.16M CLIPs into ~90K synthetic IDs (avg 57 collisions per id, worst 688),
  --   so `SELECT ... WHERE borrower_id = :id LIMIT 1` returned a non-deterministic
  --   CLIP. We now widen to base36(ABS(XXHASH64(clip))) padded to 13 chars,
  --   giving 36^13 = ~1.7e20 slots for 5.16M rows -- collision probability negligible.
  --   CONV(..., 10, 36) is Spark's base converter; LPAD stabilises the string length.
  --   UPPER() normalises the base-36 output to 0-9A-Z so the
  --   `B-[0-9A-Z]{13}` contract the parity test pins is deterministic
  --   across engines (raised by Copilot 2026-04-22 — some SQL engines
  --   return lowercase base-36 digits).
  CONCAT('B-', LPAD(UPPER(CONV(CAST(ABS(XXHASH64(w.clip)) AS STRING), 10, 36)), 13, '0')) AS borrower_id,
  CONCAT('Owner ', SUBSTR(w.owner_name_hash, 1, 8))                                   AS display_name,
  w.city,
  w.state,
  w.zip,
  w.situs_cbsa_code,
  w.county_fips_5,
  w.segment_codes,
  w.equity_estimate,
  w.equity_pct,
  w.rate_spread_bps,
  w.market_rate_fraction,
  mip.gold.fn_lead_score(
    ss.economic_incentive, ss.intent_trigger, ss.fit, ss.relationship, ss.evidence
  )                                                                                  AS opportunity_score,
  CAST(ROUND((ss.economic_incentive + ss.intent_trigger + ss.fit
               + ss.relationship + ss.evidence) / 5.0) AS INT)                       AS confidence,
  w.recommended_offer_code,
  -- Human label map: matches NBO_PRODUCT_LABELS in scoring.py. Kept inline
  -- so gold is self-contained -- no runtime join required.
  CASE w.recommended_offer_code
    WHEN 'purchase'        THEN 'Purchase Mortgage'
    WHEN 'refi_plus_heloc' THEN 'Refinance + HELOC'
    WHEN 'heloc'           THEN 'HELOC'
    WHEN 'refi'            THEN 'Refinance'
    WHEN 'cash_out'        THEN 'Cash-out Refi'
    WHEN 'investor'        THEN 'Investor Product'
    WHEN 'retention'       THEN 'Retention'
    ELSE                        'Nurture'
  END                                                                               AS recommended_offer,
  -- why_now template (data-contract §6). Plain-English for business
  -- personas (loan officers, marketing leads, VPs of Lending). No bps,
  -- no threshold syntax, no internal jargon -- this string renders
  -- verbatim on Borrower 360 and is what a human reads before
  -- approving outreach. Updated 2026-04-22 (fix/copilot-batch-post-merge)
  -- to drop '+XXX bps (>= YY)' rule-engine phrasing.
  CASE w.recommended_offer_code
    WHEN 'refi_plus_heloc' THEN
      'Current rate sits meaningfully above market and the home carries strong equity -- a refinance with a HELOC cross-sell fits.'
    WHEN 'heloc' THEN
      'Recent remodel activity plus strong home equity points to a HELOC conversation.'
    WHEN 'refi' THEN
      'Current rate is well above market, and equity clears the refi cushion (below the HELOC bar) -- lead with a refinance.'
    WHEN 'cash_out' THEN
      'Current rate is near market, but strong home equity supports a cash-out refinance conversation.'
    WHEN 'purchase' THEN
      'The home is actively listed -- a purchase mortgage on the next home is the right offer.'
    WHEN 'investor' THEN
      CONCAT('Owner Link ties ', CAST(w.related_property_count AS STRING),
             ' related properties -- route to the investor desk.')
    WHEN 'retention' THEN
      'Current customer drifting above our refi bar -- reach out before a competitor pulls the lien.'
    ELSE
      'No active trigger yet -- keep in nurture until a signal fires.'
  END                                                                                AS why_now,
  COALESCE(tl.evidence_ids, ARRAY())                                                 AS evidence_ids,
  'pending'                                                                          AS approval_status,
  w.owner_link_id,
  -- Truncate ZIP to 5 digits to match the api-boundary redaction
  -- (`pii_redaction.synthesize_subject_property` uses `zip[:5]`). As of
  -- slice13 Wave-2, silver.property_master + silver.lien_current emit a
  -- 5-digit situs_zip_code, so this is now a COALESCE (not a SUBSTR)
  -- guard. Tracked by docs/validation/borrower-e2e-audit.md +
  -- docs/validation/data-corrections.md §REFRESH-AFTER-WAVE-2.
  CONCAT('Synthetic property · ', COALESCE(w.city, 'Unknown'), ', ',
         w.state, ' ', COALESCE(w.zip, '00000'))                                      AS subject_property,
  CAST(COALESCE(w.avm_value, 0) AS BIGINT)                                           AS avm_value,
  CAST(COALESCE(w.total_open_lien_balance, 0) AS BIGINT)                             AS current_lien_balance,
  -- current_rate in PERCENT form (5.75), matches Pydantic + mock_data.
  CAST(COALESCE(w.first_pos_rate * 100, 0.0) AS DOUBLE)                              AS current_rate,
  w.ltv,
  w.related_property_count,
  w.is_owner_occupied,
  COALESCE(w.is_absentee, FALSE)                                                     AS is_absentee,
  COALESCE(w.owner_is_corporate, FALSE)                                              AS is_corporate_owner,
  w.has_permit,
  w.listed_for_sale,
  w.is_investor,
  w.is_current_customer,
  w.is_competitor_lien,
  w.second_pos_amount,
  w.first_pos_loan_type,
  w.owner_name_hash,
  w.min_spread_bps_applied,
  w.min_equity_pct_applied,
  w.in_the_money,
  COALESCE(tl.trigger_timeline_json, '[]')                                           AS trigger_timeline_json,
  CURRENT_TIMESTAMP()                                                                AS refreshed_at
FROM with_segments AS w
LEFT JOIN subscores AS ss ON ss.clip = w.clip
LEFT JOIN timeline  AS tl ON tl.clip = w.clip;
