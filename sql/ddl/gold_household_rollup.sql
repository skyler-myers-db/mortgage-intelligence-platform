-- =============================================================================
-- gold_household_rollup.sql
-- -----------------------------------------------------------------------------
-- Purpose:   DDL for `mip.gold.household_rollup`, the opt-in campaign-time
--            deduplication surface. The default unit in Module 0 remains
--            BORROWER; this table only supplies a governed household grouping
--            when the campaign builder explicitly enables household dedup.
--
-- Grain:     One row per borrower / CLIP from `mip.gold.borrower_360`.
-- PK:        borrower_id.
-- Clustering: Liquid cluster on (household_id, borrower_id). Campaign creation
--            filters borrower_360, joins this table by borrower_id, then ranks
--            one contact-eligible primary per household.
--
-- Deterministic derivation:
--            1. owner_link: group CLIPs through shared Owner Links from
--               `mip.silver.property_owners` (S1.1), including co-owner links
--               on CLIPs reached through one shared owner-link hop. The
--               canonical key is the lexicographically smallest reachable
--               Owner Link, hashed before landing.
--            2. mailing_address: if no Owner Link exists, group by salted
--               owner_name_hash plus normalized mailing_city / mailing_state
--               from `mip.silver.property_master`. Street-level mailing address
--               never lands in silver or gold, so the heuristic is intentionally
--               conservative and hash-backed.
--            3. singleton: if neither signal is present, the borrower is its
--               own household.
--
-- PII posture:
--            No raw names, street addresses, CLIPs, or Owner Links are exposed.
--            household_id is "HH-" + a sha2 suffix over the derivation key;
--            household_derivation_key_hash stores the full sha2 for audit
--            reconciliation only. Borrower ids remain synthetic B-[0-9A-Z]{13}.
--
-- Evidence traceability:
--            derivation_source_tables lists the UC rows used for each method
--            (`mip.silver.property_owners`, `mip.silver.property_master`,
--            `mip.gold.borrower_360`). The frontend EvidenceDrawer cites this
--            gold table and its lineage when campaign summaries surface
--            household suppression counts.
--
-- Idempotency: CREATE TABLE IF NOT EXISTS. Transformation uses
--            CREATE OR REPLACE TABLE ... AS SELECT for a full gold rebuild.
-- =============================================================================

CREATE TABLE IF NOT EXISTS mip.gold.household_rollup (
  clip                            STRING    NOT NULL COMMENT 'Cotality CLIP below the API redaction boundary; joins to borrower_360.clip.',
  borrower_id                     STRING    NOT NULL COMMENT 'Synthetic stable borrower id, B-[0-9A-Z]{13}. No PII.',
  household_id                    STRING    NOT NULL COMMENT 'Deterministic public household id: HH- + first 16 hex chars of sha2(derivation key).',
  household_derivation_method     STRING    NOT NULL COMMENT 'owner_link | mailing_address | singleton.',
  household_derivation_key_hash   STRING    NOT NULL COMMENT 'Full sha2 over the non-PII derivation key. Raw Owner Links, CLIPs, mailing city/state, and owner hashes are not emitted.',
  derivation_source_tables        ARRAY<STRING> NOT NULL COMMENT 'UC source rows supporting the derivation: mip.silver.property_owners, mip.silver.property_master, and/or mip.gold.borrower_360.',
  household_member_count          INT       NOT NULL COMMENT 'Count of borrower rows assigned to this household_id.',
  eligible_member_count           INT       NOT NULL COMMENT 'Count of household members that are campaign-contact eligible: marketing_eligible=true and has_unresolved_owner=false.',
  household_rank                  INT       NOT NULL COMMENT 'Deterministic rank inside household: eligible borrowers first, then opportunity_score DESC, borrower_id ASC.',
  is_household_primary            BOOLEAN   NOT NULL COMMENT 'TRUE only for rank 1 when that borrower is contact-eligible.',
  primary_borrower_id             STRING             COMMENT 'Synthetic borrower id of the selected primary contact, or NULL if no member is contact-eligible.',
  suppressed_by_household_dedup   BOOLEAN   NOT NULL COMMENT 'TRUE when this eligible borrower would be suppressed by opt-in campaign household dedup.',
  owner_link_reachable_count      INT       NOT NULL COMMENT 'Number of reachable Owner Links used by the owner_link derivation; 0 for non-owner-link methods.',
  refreshed_at                    TIMESTAMP NOT NULL COMMENT 'Shared gold refresh timestamp from mip.ref.refresh_run_state.'
)
USING DELTA
CLUSTER BY (household_id, borrower_id)
COMMENT 'Campaign-time household dedup rollup. Borrower remains the default unit; household grouping is opt-in at campaign creation and evidence-cited through UC lineage.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);
