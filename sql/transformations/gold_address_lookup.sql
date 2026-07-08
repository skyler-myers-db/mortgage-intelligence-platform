-- =============================================================================
-- gold_address_lookup.sql (transformation)
-- -----------------------------------------------------------------------------
-- Purpose:   Populate `mip.gold.address_lookup` via CTAS. One row per
--            `address_hash` -- the governed "property loan lookup" spine that
--            the app UI, the Growth Agent dossier specialist, and future org
--            agents all consume to resolve a caller-supplied street address +
--            ZIP to a masked CLIP and its loan facts.
--
--            This is NOT a CLIP resolution / mastering service (that is
--            Cotality's mastering API). This is a SHARE-SCOPED, EXACT-after-
--            canonicalization lookup: it only matches an address that already
--            exists in the current Cotality share, and only when the caller's
--            canonicalized string is byte-identical to the stored one. No
--            fuzzy matching, no USPS abbreviation dictionaries in v1.
--
-- Reads:     The SAME raw-share sources silver_property_master + silver_lien_
--            current read:
--              cotality_mortgage_data.corelogic.entrada_eval_property_domain_v3
--              cotality_mortgage_data.corelogic.entrada_eval_voluntary_lien_status_marketing_v2
--            ETL identity runs this CTAS (the same identity that runs
--            mip_refresh_silver / mip_refresh_scores), so reading the raw share
--            here is allowed and consistent with docs/security/GRANTS.md §5/§7.
--            The running app SP never reads the share -- it reads this gold
--            table (schema-level grant, GRANTS.md §gold).
--
-- Grain:     One row per address_hash. Deterministic dedupe: latest lien
--            recency wins (avm_as_of_date DESC, then first_pos_date DESC, then
--            total_open_lien_balance DESC, then clip for a stable final tiebreak).
-- Slice:     module0-governed-property-loan-lookup.
--
-- =========================== PII posture (non-negotiable) ====================
-- `situs_street_address` EXISTS in the raw share (property + lien domains) but
-- is DELIBERATELY NEVER PROJECTED into silver, gold, or any /api surface -- see
-- silver_property_master.sql / silver_lien_current.sql headers. This CTAS
-- preserves that invariant absolutely:
--   - `situs_street_address` is read ONLY inside the `hashed` CTE, solely to
--     compute `address_hash`, and the raw column is DROPPED at the outer
--     SELECT. It never appears in the CREATE TABLE column list.
--   - The stored key is a salt-free SHA-256; the address itself is not
--     recoverable from it and is never written anywhere in this table.
--   - `clip` / `owner_link_id` are raw in gold and masked at egress by the
--     Python boundary (backend/services/pii_redaction.py::mask_cotality_id),
--     exactly like every other gold surface.
--
-- ========================= Address canonicalization spec =====================
-- Implemented IDENTICALLY in backend/services/address_normalization.py
-- (normalize_address). Any change here MUST be mirrored there or every lookup
-- misses. v1 spec:
--   1. UPPER-case.
--   2. TRIM leading/trailing whitespace.
--   3. Collapse internal whitespace runs to a single space.
--   4. Strip the characters `.` `,` `#` entirely (removed, not spaced).
--   5. Expand nothing else (NO suffix/abbreviation dictionary).
-- Spark expression order below:
--   translate(UPPER(x), '.,#', '')      -- upper + strip the 3 chars
--   regexp_replace(..., '\\s+', ' ')    -- collapse whitespace runs
--   TRIM(...)                            -- trim the ends
-- Hash: sha2(CONCAT(<norm>, '|', <zip5>), 256) -- lowercase hex, matches
-- Python hashlib.sha256(...).hexdigest(). The literal `|` separator prevents
-- ('12 A','34567') colliding with ('12','A34567').
--
-- ZIP5 contract: LEFT(situs_zip_code, 5) after the same digit-normalization
-- silver applies, so a stored '10001' hash-matches a caller's '10001-1234'.
--
-- Loan facts: reuse the EXACT source column names silver_lien_current uses
-- (first_position_currently_assigned_lender_company_name, estimated_combined_
-- ltv..., total_amount_of_open_mortgage_liens, first_position_mortgage_interest
-- _rate) so this table stays consistent with what gold_borrower_360 exposes.
-- Rate is converted to fractional form and bounded exactly as silver does.
--
-- COMMENT ON TABLE/COLUMN below are tool-grade docstrings -- this table is an
-- org-consumable lookup spine and its columns must be self-describing for the
-- Growth Agent + Genie.
-- =============================================================================

CREATE OR REPLACE TABLE mip.gold.address_lookup
CLUSTER BY (address_hash)
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
)
AS
WITH refresh_anchor AS (
  SELECT refresh_at
  FROM mip.ref.refresh_run_state
  ORDER BY captured_at DESC
  LIMIT 1
),
prop AS (
  -- Property domain: situs_street_address is read HERE (only) to build the
  -- hash, plus the geography + owner-link columns already projected into
  -- silver today. The raw street column is dropped at the outer SELECT.
  SELECT
    clip,
    -- ZIP5 normalization mirrors silver_property_master.sql.
    CASE
      WHEN LENGTH(REGEXP_REPLACE(CAST(situs_zip_code AS STRING), '[^0-9]', '')) >= 5
        THEN SUBSTR(REGEXP_REPLACE(CAST(situs_zip_code AS STRING), '[^0-9]', ''), 1, 5)
      WHEN LENGTH(REGEXP_REPLACE(CAST(situs_zip_code AS STRING), '[^0-9]', '')) = 4
       AND UPPER(TRIM(situs_state)) IN ('CT','MA','ME','NH','NJ','RI','VT','PR','VI')
        THEN LPAD(REGEXP_REPLACE(CAST(situs_zip_code AS STRING), '[^0-9]', ''), 5, '0')
      ELSE NULL
    END                                                             AS zip5,
    situs_city,
    situs_state,
    owner_1_identifier                                              AS owner_link_id,
    -- Canonicalize per the v1 spec (see header). Mirrors
    -- address_normalization.normalize_address.
    TRIM(
      REGEXP_REPLACE(
        TRANSLATE(UPPER(CAST(situs_street_address AS STRING)), '.,#', ''),
        '\\s+',
        ' '
      )
    )                                                               AS norm_address
  FROM cotality_mortgage_data.corelogic.entrada_eval_property_domain_v3
  WHERE situs_state IS NOT NULL
    AND clip IS NOT NULL
    AND situs_street_address IS NOT NULL
    AND TRIM(CAST(situs_street_address AS STRING)) <> ''
),
lien AS (
  -- Lien spine: loan facts consistent with gold_borrower_360, reusing the
  -- exact source column names silver_lien_current reads. Recency columns feed
  -- the deterministic dedupe.
  SELECT
    clip,
    (CAST(total_number_of_open_mortgage_liens AS INT) > 0
      OR CAST(total_amount_of_open_mortgage_liens AS BIGINT) > 0)   AS has_open_lien,
    CAST(total_amount_of_open_mortgage_liens AS BIGINT)             AS current_lien_balance,
    CAST(estimated_combined_ltv_loan_to_value AS DOUBLE)            AS estimated_cltv,
    first_position_currently_assigned_lender_company_name          AS first_pos_lender_current,
    -- Rate: percent -> fractional, bounded to a defensible APR range, exactly
    -- as silver_lien_current.sql / gold_borrower_360.sql do.
    CASE
      WHEN first_position_mortgage_interest_rate IS NULL THEN NULL
      WHEN CAST(first_position_mortgage_interest_rate AS DOUBLE) < 1 THEN NULL
      WHEN CAST(first_position_mortgage_interest_rate AS DOUBLE) > 15 THEN 0.15
      ELSE CAST(first_position_mortgage_interest_rate AS DOUBLE) / 100.0
    END                                                             AS current_rate_fraction,
    -- Recency tiebreak columns (dedupe only; not projected).
    TRY_TO_DATE(NULLIF(CAST(value_as_of_date_mktg AS STRING), '0'), 'yyyyMMdd')
      AS avm_as_of_date,
    TRY_TO_DATE(CAST(NULLIF(first_position_mortgage_date, 0) AS STRING), 'yyyyMMdd')
      AS first_pos_date
  FROM cotality_mortgage_data.corelogic.entrada_eval_voluntary_lien_status_marketing_v2
  WHERE clip IS NOT NULL
),
hashed AS (
  SELECT
    -- The join key. sha2(..., 256) emits lowercase hex, matching
    -- address_normalization.address_lookup_hash. Rows with an empty canonical
    -- address are excluded below so a blank string can never become a key.
    SHA2(CONCAT(p.norm_address, '|', COALESCE(p.zip5, '')), 256)     AS address_hash,
    p.zip5,
    p.situs_city,
    p.situs_state,
    p.clip,
    p.owner_link_id,
    COALESCE(l.has_open_lien, FALSE)                                 AS has_open_lien,
    l.current_lien_balance,
    l.estimated_cltv,
    l.first_pos_lender_current,
    l.current_rate_fraction,
    l.avm_as_of_date,
    l.first_pos_date
  FROM prop AS p
  LEFT JOIN lien AS l
    ON l.clip = p.clip
  WHERE p.norm_address <> ''
    AND p.zip5 IS NOT NULL
)
SELECT
  address_hash,
  zip5,
  situs_city,
  situs_state,
  clip,
  owner_link_id,
  has_open_lien,
  CAST(COALESCE(current_lien_balance, 0) AS BIGINT)                 AS current_lien_balance,
  -- Display LTV int, same source preference + non-negative floor as
  -- gold_borrower_360 (estimated_cltv when present and positive; else 0). Not
  -- upper-capped -- underwater properties may exceed 100.
  CAST(GREATEST(0, CASE
    WHEN estimated_cltv IS NOT NULL AND estimated_cltv > 0 THEN ROUND(estimated_cltv)
    ELSE 0
  END) AS INT)                                                      AS ltv,
  first_pos_lender_current,
  -- current_rate in PERCENT form (5.75) to match Borrower360 / gold_borrower_360.
  CAST(COALESCE(current_rate_fraction * 100, 0.0) AS DOUBLE)        AS current_rate,
  (SELECT refresh_at FROM refresh_anchor)                           AS refreshed_at
FROM hashed
-- Deterministic dedupe: one row per address_hash. Latest lien recency wins.
-- clip is the final stable tiebreak so re-runs are byte-identical even when
-- two CLIPs share an address_hash (distinct properties, same street+zip after
-- canonicalization -- rare but possible in the share).
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY address_hash
  ORDER BY avm_as_of_date DESC, first_pos_date DESC,
           current_lien_balance DESC, clip ASC
) = 1;

-- Column comments re-applied post-CTAS (CREATE OR REPLACE drops DDL comments;
-- the typeless CTAS column list is a PARSE_SYNTAX_ERROR on DBSQL). These MUST
-- be byte-identical to the COMMENT strings in sql/ddl/003_gold_tables.sql (the
-- test_gold_column_comment_guard drift guard pins both directions).
COMMENT ON TABLE mip.gold.address_lookup IS 'Governed property loan lookup spine. One row per address_hash. Share-scoped EXACT-after-canonicalization lookup (NOT Cotality CLIP mastering; no fuzzy match). Raw street address is NEVER stored — only its hash. Consumed by the property-loan-lookup API, the Growth Agent dossier specialist, and future org agents.';
COMMENT ON COLUMN mip.gold.address_lookup.address_hash IS 'PK. sha2(CONCAT(normalized_address, "|", zip5), 256), lowercase hex. Normalization: UPPER, TRIM, collapse whitespace runs, strip . , # (no abbreviation expansion). Mirrors backend/services/address_normalization.py. The raw street address is not stored; note this is a salt-free join key, so a privileged UC reader holding candidate addresses can test membership by hashing them — the audit ledger uses a tenant-secret HMAC token instead, and a keyed gold join key is the documented customer-deploy hardening.';
COMMENT ON COLUMN mip.gold.address_lookup.zip5 IS '5-digit situs ZIP used in the hash.';
COMMENT ON COLUMN mip.gold.address_lookup.situs_city IS 'Situs city (already in silver/gold; safe to display).';
COMMENT ON COLUMN mip.gold.address_lookup.situs_state IS 'Situs state (already in silver/gold; safe to display).';
COMMENT ON COLUMN mip.gold.address_lookup.clip IS 'Cotality CLIP for the matched property. Raw in gold; masked to clip_ref_* at the app/audit boundary.';
COMMENT ON COLUMN mip.gold.address_lookup.owner_link_id IS 'Cotality Owner Link id when present. Raw in gold; masked to owner_link_ref_* at egress.';
COMMENT ON COLUMN mip.gold.address_lookup.has_open_lien IS 'TRUE when the lien spine shows any open mortgage lien for this CLIP.';
COMMENT ON COLUMN mip.gold.address_lookup.current_lien_balance IS 'USD total_amount_of_open_mortgage_liens (COALESCE 0). Same source as borrower_360.current_lien_balance.';
COMMENT ON COLUMN mip.gold.address_lookup.ltv IS 'Display LTV int from estimated_combined_ltv_loan_to_value (>=0, not upper-capped). 0 when no CLTV signal.';
COMMENT ON COLUMN mip.gold.address_lookup.first_pos_lender_current IS 'Raw first-position currently-assigned servicer string. Generalized to a public-safe alias at the app boundary; never displayed raw.';
COMMENT ON COLUMN mip.gold.address_lookup.current_rate IS 'First-position mortgage rate in PERCENT form (5.75), bounded 1-15% as in silver. 0.0 when no rate signal.';
COMMENT ON COLUMN mip.gold.address_lookup.refreshed_at IS 'Deterministic refresh anchor from mip.ref.refresh_run_state.';
