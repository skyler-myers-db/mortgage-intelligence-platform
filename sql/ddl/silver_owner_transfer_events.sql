-- =============================================================================
-- silver_owner_transfer_events.sql
-- -----------------------------------------------------------------------------
-- Purpose:   DDL for `mip.silver.owner_transfer_events`, the historical
--            deed/sale event grain. One row per sale/transfer per CLIP.
--            Feeds the gold evidence timeline (recent sale, REO, short-sale)
--            and the investor-segment signals (cash purchase, investor
--            purchase, corporate-buyer, out-of-state-buyer mailing).
--
-- Data contract reference: docs/data-contract-module0.md §2.4.
-- Slice:     module0-real-data-slice2 (silver lift from the Cotality share).
-- Source:    cotality_mortgage_data.corelogic.entrada_eval_owner_transfer_
--            domain_v1 (~24M rows, event grain; composite txn id is PK).
--
-- Geography filter (non-negotiable):
--            WHERE deed_situs_state_static IN ('IL','CA','FL','TX','WA','CO')
--
-- PII posture (NON-NEGOTIABLE, governance-real-data-review §1):
--            - `buyer_1_full_name`, `seller_1_full_name` : NEVER landed.
--              Gold segment counts + investor-purchase flags do NOT require
--              buyer names. `buyer_1_identifier` (opaque id) is retained
--              for Owner-Link-style aggregation.
--            - `buyer_mailing_state`                    : retained because
--              out-of-state buyer mailing is a useful investor signal;
--              street is never persisted.
--
-- Indicator types (from slice-2 probe):
--            All of `cash_purchase_indicator`, `investor_purchase_indicator`,
--            `foreclosure_reo_indicator`, `short_sale_indicator`,
--            `new_construction_indicator`, `resale_indicator`,
--            `interfamily_related_indicator`, `buyer_1_corporate_indicator`
--            are BIGINT (1/0) in the share. Transformation uses
--            CAST(ind AS BOOLEAN), NOT `= 'Y'`.
--
-- Columns (data-contract §2.4 minus buyer-name PII):
--
--            transfer_txn_id          PK. Composite txn id from share.
--            clip                     FK to lien_current / property_master.
--            situs_state              Renamed from deed_situs_state_static.
--            sale_date                From sale_derived_date.
--            sale_amount              Sale amount, USD.
--            sale_type_code           sale_type_code from share.
--            is_cash_purchase         BOOLEAN from cash_purchase_indicator.
--            is_investor_purchase     BOOLEAN.
--            is_reo                   BOOLEAN from foreclosure_reo_indicator.
--            is_short_sale            BOOLEAN.
--            is_new_construction      BOOLEAN.
--            is_resale                BOOLEAN.
--            is_interfamily           BOOLEAN.
--            buyer_is_corporate       BOOLEAN from buyer_1_corporate_indicator.
--            buyer_identifier         Opaque buyer id (NOT a name).
--            buyer_mailing_state      Retained for out-of-state investor flag.
--            ingest_ts, _meta_batch_id
--
-- Clustering: Liquid cluster on (clip, sale_date) -- aligned with
--            gold.evidence_events per-CLIP scan pattern.
--
-- Idempotency: CREATE TABLE IF NOT EXISTS. Transformation MERGEs on
--            `transfer_txn_id`.
-- =============================================================================

CREATE TABLE IF NOT EXISTS mip.silver.owner_transfer_events (
  transfer_txn_id        STRING    NOT NULL COMMENT 'Composite transfer transaction ID from share. PK.',
  clip                   STRING    NOT NULL COMMENT 'Cotality mastered property ID. FK to silver.lien_current / property_master.',
  situs_state            STRING    NOT NULL COMMENT '2-char state code (renamed from deed_situs_state_static). Filter: IN (IL, CA, FL, TX, WA, CO).',
  sale_date              DATE               COMMENT 'Sale date (from sale_derived_date).',
  sale_amount            BIGINT             COMMENT 'Sale amount, USD.',
  sale_type_code         STRING             COMMENT 'sale_type_code from share.',
  is_cash_purchase       BOOLEAN            COMMENT 'CAST(cash_purchase_indicator AS BOOLEAN).',
  is_investor_purchase   BOOLEAN            COMMENT 'CAST(investor_purchase_indicator AS BOOLEAN).',
  is_reo                 BOOLEAN            COMMENT 'CAST(foreclosure_reo_indicator AS BOOLEAN).',
  is_short_sale          BOOLEAN            COMMENT 'CAST(short_sale_indicator AS BOOLEAN).',
  is_new_construction    BOOLEAN            COMMENT 'CAST(new_construction_indicator AS BOOLEAN).',
  is_resale              BOOLEAN            COMMENT 'CAST(resale_indicator AS BOOLEAN).',
  is_interfamily         BOOLEAN            COMMENT 'CAST(interfamily_related_indicator AS BOOLEAN).',
  buyer_is_corporate     BOOLEAN            COMMENT 'CAST(buyer_1_corporate_indicator AS BOOLEAN).',
  buyer_identifier       STRING             COMMENT 'Cotality opaque buyer/entity ID (NOT a name).',
  buyer_mailing_state    STRING             COMMENT 'Out-of-state buyer signal; only state retained, no street.',
  ingest_ts              TIMESTAMP NOT NULL COMMENT 'Silver ingest timestamp (CURRENT_TIMESTAMP at MERGE time).',
  _meta_batch_id         STRING             COMMENT 'Lakeflow pipeline run id for observability.'
)
USING DELTA
CLUSTER BY (clip, sale_date)
COMMENT 'Event-grain owner-transfer history lifted from cotality_mortgage_data.corelogic.entrada_eval_owner_transfer_domain_v1 filtered to deed_situs_state_static IN (IL, CA, FL, TX, WA, CO). Buyer names never land in silver. See docs/data-contract-module0.md §2.4.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);
