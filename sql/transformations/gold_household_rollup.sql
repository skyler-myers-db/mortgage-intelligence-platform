-- =============================================================================
-- gold_household_rollup.sql (transformation)
-- -----------------------------------------------------------------------------
-- Purpose:   Populate `mip.gold.household_rollup` for opt-in campaign-time
--            household dedup. Module 0 defaults to BORROWER everywhere; this
--            table is read only when a campaign explicitly enables the
--            household dedup toggle.
--
-- Grain:     One row per borrower / CLIP from `mip.gold.borrower_360`.
-- Pattern:   CREATE OR REPLACE TABLE ... AS SELECT. Full rebuild follows the
--            gold refresh posture and is deterministic for the same inputs.
-- Data contract: docs/data-contract-module0.md §3.7.
--
-- Derivation order:
--   1. owner_link: use S1.1 `mip.silver.property_owners` one-row-per
--      (clip, owner_link) rows. For each CLIP, look through one shared
--      Owner-Link hop and its co-owner slots, then choose the lexicographically
--      smallest reachable owner_link_id as the canonical key. The raw Owner
--      Link never lands; only sha2-derived household ids do.
--   2. mailing_address: when no Owner Link exists, use owner_name_hash plus
--      normalized mailing_city / mailing_state from property_master. This is
--      intentionally conservative and avoids street-level mailing attributes.
--   3. singleton: remaining borrowers keep their own household.
--
-- Primary-contact ranking:
--   Eligible members rank before ineligible members, then by opportunity_score
--   DESC and borrower_id ASC. is_household_primary is TRUE only if the rank-1
--   borrower is marketing_eligible and has_unresolved_owner=false, so campaign
--   dedup can never promote an ineligible co-owner.
--
-- 2026-07-09 S1.5: CTAS re-declares clustering/comments/properties because
-- CREATE OR REPLACE TABLE drops DDL metadata on every refresh.
-- =============================================================================

CREATE OR REPLACE TABLE mip.gold.household_rollup
CLUSTER BY (household_id, borrower_id)
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
clip_owner_links AS (
  SELECT DISTINCT
    po.clip,
    NULLIF(TRIM(po.owner_link_id), '') AS owner_link_id
  FROM mip.silver.property_owners AS po
  JOIN mip.gold.borrower_360 AS b
    ON b.clip = po.clip
  WHERE po.owner_link_id IS NOT NULL
),
owner_link_reach AS (
  -- One shared-owner hop plus co-owner slots on the reached CLIPs. This keeps
  -- the derivation deterministic in SQL without requiring a recursive graph
  -- library, and it handles the common household shape: A+B co-own one CLIP,
  -- A or B appears on another CLIP.
  SELECT
    base.clip,
    member.owner_link_id AS reachable_owner_link_id
  FROM clip_owner_links AS base
  JOIN clip_owner_links AS shared_clip
    ON shared_clip.owner_link_id = base.owner_link_id
  JOIN clip_owner_links AS member
    ON member.clip = shared_clip.clip
),
owner_link_households AS (
  SELECT
    clip,
    MIN(reachable_owner_link_id) AS owner_link_household_key,
    CAST(COUNT(DISTINCT reachable_owner_link_id) AS INT) AS owner_link_reachable_count
  FROM owner_link_reach
  GROUP BY clip
),
mailing_households AS (
  SELECT
    po.clip,
    MIN(CONCAT(
      'mail:',
      po.owner_name_hash,
      ':',
      UPPER(TRIM(pm.mailing_city)),
      ':',
      UPPER(TRIM(pm.mailing_state))
    )) AS mailing_household_key
  FROM mip.silver.property_owners AS po
  JOIN mip.silver.property_master AS pm
    ON pm.clip = po.clip
  LEFT JOIN owner_link_households AS olh
    ON olh.clip = po.clip
  WHERE olh.clip IS NULL
    AND po.owner_name_hash IS NOT NULL
    AND pm.mailing_city IS NOT NULL
    AND pm.mailing_state IS NOT NULL
  GROUP BY po.clip
),
assigned AS (
  SELECT
    b.clip,
    b.borrower_id,
    b.opportunity_score,
    b.marketing_eligible,
    b.has_unresolved_owner,
    CASE
      WHEN olh.owner_link_household_key IS NOT NULL THEN CONCAT('owner_link:', olh.owner_link_household_key)
      WHEN mh.mailing_household_key IS NOT NULL THEN mh.mailing_household_key
      ELSE CONCAT('singleton:', b.borrower_id)
    END AS household_key,
    CASE
      WHEN olh.owner_link_household_key IS NOT NULL THEN 'owner_link'
      WHEN mh.mailing_household_key IS NOT NULL THEN 'mailing_address'
      ELSE 'singleton'
    END AS household_derivation_method,
    CASE
      WHEN olh.owner_link_household_key IS NOT NULL THEN ARRAY('mip.silver.property_owners', 'mip.gold.borrower_360')
      WHEN mh.mailing_household_key IS NOT NULL THEN ARRAY('mip.silver.property_owners', 'mip.silver.property_master', 'mip.gold.borrower_360')
      ELSE ARRAY('mip.gold.borrower_360')
    END AS derivation_source_tables,
    COALESCE(olh.owner_link_reachable_count, 0) AS owner_link_reachable_count
  FROM mip.gold.borrower_360 AS b
  LEFT JOIN owner_link_households AS olh
    ON olh.clip = b.clip
  LEFT JOIN mailing_households AS mh
    ON mh.clip = b.clip
),
households AS (
  SELECT
    *,
    CONCAT('HH-', SUBSTR(sha2(household_key, 256), 1, 16)) AS household_id,
    sha2(household_key, 256) AS household_derivation_key_hash,
    (marketing_eligible = TRUE AND COALESCE(has_unresolved_owner, FALSE) = FALSE) AS is_contact_eligible_for_household
  FROM assigned
),
ranked AS (
  SELECT
    *,
    CAST(COUNT(*) OVER (PARTITION BY household_id) AS INT) AS household_member_count,
    CAST(COUNT_IF(is_contact_eligible_for_household) OVER (PARTITION BY household_id) AS INT) AS eligible_member_count,
    ROW_NUMBER() OVER (
      PARTITION BY household_id
      ORDER BY
        CASE WHEN is_contact_eligible_for_household THEN 0 ELSE 1 END,
        opportunity_score DESC,
        borrower_id ASC
    ) AS household_rank,
    FIRST_VALUE(borrower_id) OVER (
      PARTITION BY household_id
      ORDER BY
        CASE WHEN is_contact_eligible_for_household THEN 0 ELSE 1 END,
        opportunity_score DESC,
        borrower_id ASC
    ) AS ranked_primary_borrower_id
  FROM households
)
SELECT
  clip,
  borrower_id,
  household_id,
  household_derivation_method,
  household_derivation_key_hash,
  derivation_source_tables,
  household_member_count,
  eligible_member_count,
  CAST(household_rank AS INT) AS household_rank,
  (household_rank = 1 AND is_contact_eligible_for_household) AS is_household_primary,
  CASE
    WHEN eligible_member_count > 0 THEN ranked_primary_borrower_id
    ELSE NULL
  END AS primary_borrower_id,
  (is_contact_eligible_for_household AND household_rank > 1) AS suppressed_by_household_dedup,
  owner_link_reachable_count,
  (SELECT refresh_at FROM refresh_anchor) AS refreshed_at
FROM ranked;

COMMENT ON COLUMN mip.gold.household_rollup.clip IS 'Cotality CLIP below the API redaction boundary; joins to borrower_360.clip.';
COMMENT ON COLUMN mip.gold.household_rollup.borrower_id IS 'Synthetic stable borrower id, B-[0-9A-Z]{13}. No PII.';
COMMENT ON COLUMN mip.gold.household_rollup.household_id IS 'Deterministic public household id: HH- + first 16 hex chars of sha2(derivation key).';
COMMENT ON COLUMN mip.gold.household_rollup.household_derivation_method IS 'owner_link | mailing_address | singleton.';
COMMENT ON COLUMN mip.gold.household_rollup.household_derivation_key_hash IS 'Full sha2 over the non-PII derivation key. Raw Owner Links, CLIPs, mailing city/state, and owner hashes are not emitted.';
COMMENT ON COLUMN mip.gold.household_rollup.derivation_source_tables IS 'UC source rows supporting the derivation: mip.silver.property_owners, mip.silver.property_master, and/or mip.gold.borrower_360.';
COMMENT ON COLUMN mip.gold.household_rollup.household_member_count IS 'Count of borrower rows assigned to this household_id.';
COMMENT ON COLUMN mip.gold.household_rollup.eligible_member_count IS 'Count of household members that are campaign-contact eligible: marketing_eligible=true and has_unresolved_owner=false.';
COMMENT ON COLUMN mip.gold.household_rollup.household_rank IS 'Deterministic rank inside household: eligible borrowers first, then opportunity_score DESC, borrower_id ASC.';
COMMENT ON COLUMN mip.gold.household_rollup.is_household_primary IS 'TRUE only for rank 1 when that borrower is contact-eligible.';
COMMENT ON COLUMN mip.gold.household_rollup.primary_borrower_id IS 'Synthetic borrower id of the selected primary contact, or NULL if no member is contact-eligible.';
COMMENT ON COLUMN mip.gold.household_rollup.suppressed_by_household_dedup IS 'TRUE when this eligible borrower would be suppressed by opt-in campaign household dedup.';
COMMENT ON COLUMN mip.gold.household_rollup.owner_link_reachable_count IS 'Number of reachable Owner Links used by the owner_link derivation; 0 for non-owner-link methods.';
COMMENT ON COLUMN mip.gold.household_rollup.refreshed_at IS 'Shared gold refresh timestamp from mip.ref.refresh_run_state.';
