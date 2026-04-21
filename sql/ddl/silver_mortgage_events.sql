-- =============================================================================
-- silver_mortgage_events.sql
-- -----------------------------------------------------------------------------
-- Purpose:   DDL for `mip.silver.mortgage_events`, the historical
--            mortgage-event grain table. One row per originated/refinanced/
--            released mortgage event per CLIP. Consumed by gold to
--            reconstruct per-borrower timelines (`gold.evidence_events`),
--            compute recent-refi and recent-payoff counts for `intent_
--            trigger` scoring, and detect competitor-refi cases for the
--            retention segment.
--
-- Data contract reference: docs/data-contract-module0.md §2.3.
-- Slice:     module0-real-data-slice2 (silver lift from the Cotality share).
-- Source:    cotality_mortgage_data.corelogic.entrada_eval_mortgage_domain_v1
--            (~29M rows, event grain; composite txn id is PK).
--
-- Geography filter (non-negotiable):
--            WHERE deed_situs_state_static IN ('IL','CA','FL','TX','WA','CO')
--            -- Note: this share's state column is `deed_situs_state_static`
--            (different from the voluntary_lien / property_v3 convention).
--            Transformation file handles the column rename to `situs_state`
--            for downstream join consistency.
--
-- PII posture (NON-NEGOTIABLE, governance-real-data-review §1):
--            - No `borrower_1_full_name` : never land a borrower name. The
--              share's `borrower_1_identifier` is a Cotality-opaque ID (not
--              a name), safe to persist for retention-segment joins.
--            - `lender_company_name`     : real lender names. Gold remaps
--              to `{Summit Mortgage, Competitor A, Competitor B}` vocab.
--
-- Indicator types (from slice-2 probe):
--            `refinance_loan_indicator`, `equity_loan_indicator`,
--            `reverse_mortgage_indicator` are BIGINT (1/0) in the share,
--            NOT STRING ('Y'/'N'). Transformation casts to BOOLEAN via
--            `CAST(ind AS BOOLEAN)` (1 -> true, 0 -> false), NOT
--            `indicator = 'Y'`.
--
-- Date-type note:
--            `mortgage_derived_date` is the event-date anchor. Transformation
--            resolves BIGINT-yyyyMMdd form (share probe) to DATE via
--            TO_DATE(CAST(x AS STRING), 'yyyyMMdd') with NULL-safe guards.
--
-- Columns (data-contract §2.3):
--
--            mortgage_txn_id          PK. `mortgage_composite_transaction_id`.
--            clip                     FK to lien_current / property_master.
--            situs_state              Renamed from deed_situs_state_static.
--            event_date               Event date (from mortgage_derived_date).
--            event_year               YEAR(event_date) convenience column.
--            mortgage_amount          Loan amount, USD.
--            rate_cascade             Fractional rate from share cascade.
--            purpose_code             mortgage_purpose_code.
--            loan_type_code           mortgage_loan_type_code.
--            is_refinance             BOOLEAN from refinance_loan_indicator.
--            is_equity_loan           BOOLEAN; detects HELOC/HEL.
--            is_reverse_mortgage      BOOLEAN; reverse-mortgage flag.
--            lender_name              Lender at event time (remapped at gold).
--            release_date             Lien release date if any.
--            status_indicator         mortgage_status_indicator.
--            borrower_identifier      Cotality borrower/entity id (NOT name).
--            ingest_ts                CURRENT_TIMESTAMP() for audit.
--            _meta_batch_id           Lakeflow run id (NULL-OK).
--
-- Clustering: Liquid cluster on (clip, event_date) -- gold per-CLIP timeline
--            queries filter on clip and take the most recent N events; event
--            counts in the last 90 days feed `intent_trigger`. 29M rows
--            filtered to 6 states is still ~10M+; clustering on (clip,
--            event_date) lets that scan skip aggressively. Partitioning by
--            event_year was considered and rejected: gold reads the last 90
--            days most often, so year-partition boundaries don't help.
--
-- Idempotency: CREATE TABLE IF NOT EXISTS. Transformation MERGE-keys on
--            `mortgage_txn_id` (composite txn id is a PK on the share).
-- =============================================================================

CREATE TABLE IF NOT EXISTS mip.silver.mortgage_events (
  mortgage_txn_id        STRING    NOT NULL COMMENT 'Composite transaction ID from share (mortgage_composite_transaction_id). PK.',
  clip                   STRING    NOT NULL COMMENT 'Cotality mastered property ID. FK to silver.lien_current + silver.property_master.',
  situs_state            STRING    NOT NULL COMMENT '2-char state code (renamed from deed_situs_state_static). Filter: IN (IL, CA, FL, TX, WA, CO).',
  event_date             DATE               COMMENT 'Event date. TO_DATE(CAST(yyyyMMdd AS STRING), ...) from share.',
  event_year             INT                COMMENT 'YEAR(event_date). Convenience column for cohort aggregates.',
  mortgage_amount        BIGINT             COMMENT 'Loan amount, USD.',
  rate_cascade           DOUBLE             COMMENT 'Fractional rate from share cascade.',
  purpose_code           STRING             COMMENT 'mortgage_purpose_code from share.',
  loan_type_code         STRING             COMMENT 'mortgage_loan_type_code from share.',
  is_refinance           BOOLEAN            COMMENT 'CAST(refinance_loan_indicator AS BOOLEAN). Share indicator is BIGINT 1/0.',
  is_equity_loan         BOOLEAN            COMMENT 'CAST(equity_loan_indicator AS BOOLEAN). HELOC/HEL flag.',
  is_reverse_mortgage    BOOLEAN            COMMENT 'CAST(reverse_mortgage_indicator AS BOOLEAN).',
  lender_name            STRING             COMMENT 'Lender company name at event time. Gold remaps to controlled vocab before UI.',
  release_date           DATE               COMMENT 'Lien release date if released.',
  status_indicator       STRING             COMMENT 'mortgage_status_indicator from share.',
  borrower_identifier    STRING             COMMENT 'Cotality opaque borrower/entity ID (NOT a name). Safe to retain for retention joins.',
  ingest_ts              TIMESTAMP NOT NULL COMMENT 'Silver ingest timestamp (CURRENT_TIMESTAMP at MERGE time).',
  _meta_batch_id         STRING             COMMENT 'Lakeflow pipeline run id for observability.'
)
USING DELTA
CLUSTER BY (clip, event_date)
COMMENT 'Event-grain mortgage history lifted from cotality_mortgage_data.corelogic.entrada_eval_mortgage_domain_v1 filtered to deed_situs_state_static IN (IL, CA, FL, TX, WA, CO). Feeds intent_trigger scoring and gold.evidence_events. See docs/data-contract-module0.md §2.3.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);
