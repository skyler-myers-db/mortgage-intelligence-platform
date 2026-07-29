-- Lakebase Postgres DDL for Module 0 app state.
-- Target: Lakebase instance `mip_app_state`, database `mip_app_state`,
-- schema `mip_app`. This file is idempotent; Slice 5 runs it at bundle
-- deploy time and treats re-runs as a no-op.
--
-- Governance rationale: `docs/governance-real-data-review.md` §4 names
-- the required tables and their minimum-viable columns. The review also
-- pins `subject_clip` (which is the redacted `clip_ref` -- a 12-char
-- hash, not the raw Cotality CLIP) and bars PII from the audit metadata
-- JSONB. We keep the review's naming where the Slice 5 task prompt
-- disagrees, because §4 is the governance contract -- the ask is
-- reconciled here:
--   * `action_audit` has both `action` (from governance §4: canonical
--     verb like "view_borrower_360") and an `event_type` alias populated
--     by the Slice 5 writer path for forward compat with the task
--     prompt. The column is the same; we standardize on `action`.
--   * `subject_clip` (CLIP hash) and `subject_segment` (lowercased segment
--     code) are added as top-level columns so operators can SELECT on
--     them without JSONB indexing.
--
-- All UUID defaults use `gen_random_uuid()`; Lakebase Postgres enables
-- `pgcrypto` by default on a new instance, but we include the CREATE
-- EXTENSION line for portability.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS mip_app;
SET search_path TO mip_app, public;

-- Migration ledger ------------------------------------------------------
-- Operators must be able to answer "which Lakebase schema did this
-- customer instance reach?" without diffing catalog metadata by hand.
CREATE TABLE IF NOT EXISTS mip_app.schema_migrations (
    version     TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Campaigns -----------------------------------------------------------
-- One row per marketing campaign the user has built with the portfolio
-- builder. `criteria` JSONB stores the segment-filter payload; it MUST
-- NOT contain borrower names, addresses, or raw CLIPs -- only segment
-- codes, thresholds, and geography at ZIP/metro level.
CREATE TABLE IF NOT EXISTS mip_app.campaigns (
    campaign_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         TEXT NOT NULL,
    owner_email  TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'draft',
    criteria     JSONB NOT NULL DEFAULT '{}'::jsonb,
    json_contract_version SMALLINT NOT NULL DEFAULT 1,
    suppression_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
    message_variants JSONB NOT NULL DEFAULT '[]'::jsonb,
    channel_cascade JSONB NOT NULL DEFAULT '[]'::jsonb,
    send_window JSONB NOT NULL DEFAULT '{}'::jsonb,
    holdout JSONB,
    roi_assumptions JSONB,
    household_dedup JSONB NOT NULL DEFAULT '{"enabled": false, "dedupe_unit": "borrower", "primary_contact_strategy": "highest_opportunity_eligible"}'::jsonb,
    household_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key TEXT,
    request_payload_hash TEXT,
    creation_response JSONB,
    treatment_state TEXT NOT NULL DEFAULT 'legacy_unbound',
    treatment_materialization_id UUID,
    treatment_algorithm_version TEXT,
    treatment_contract_fingerprint TEXT,
    treatment_fingerprint TEXT,
    treatment_source_snapshot_id TEXT,
    treatment_delta_version BIGINT,
    treatment_assignment_digest TEXT,
    treatment_candidate_count BIGINT,
    treatment_selected_primary_count BIGINT,
    treatment_count BIGINT,
    treatment_holdout_count BIGINT,
    treatment_materialized_at TIMESTAMPTZ,
    treatment_build_lease_until TIMESTAMPTZ,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE mip_app.campaigns
    ADD COLUMN IF NOT EXISTS suppression_policy JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE mip_app.campaigns
    ADD COLUMN IF NOT EXISTS message_variants JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE mip_app.campaigns
    ADD COLUMN IF NOT EXISTS channel_cascade JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE mip_app.campaigns
    ADD COLUMN IF NOT EXISTS send_window JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE mip_app.campaigns
    ADD COLUMN IF NOT EXISTS holdout JSONB;
ALTER TABLE mip_app.campaigns
    ADD COLUMN IF NOT EXISTS roi_assumptions JSONB;
ALTER TABLE mip_app.campaigns
    ADD COLUMN IF NOT EXISTS household_dedup JSONB NOT NULL DEFAULT '{"enabled": false, "dedupe_unit": "borrower", "primary_contact_strategy": "highest_opportunity_eligible"}'::jsonb;
ALTER TABLE mip_app.campaigns
    ADD COLUMN IF NOT EXISTS household_summary JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE mip_app.campaigns
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT;
ALTER TABLE mip_app.campaigns
    ADD COLUMN IF NOT EXISTS request_payload_hash TEXT;
ALTER TABLE mip_app.campaigns
    ADD COLUMN IF NOT EXISTS creation_response JSONB;
ALTER TABLE mip_app.campaigns
    ADD COLUMN IF NOT EXISTS treatment_state TEXT NOT NULL DEFAULT 'legacy_unbound';
ALTER TABLE mip_app.campaigns
    ADD COLUMN IF NOT EXISTS treatment_materialization_id UUID;
ALTER TABLE mip_app.campaigns
    ADD COLUMN IF NOT EXISTS treatment_algorithm_version TEXT;
ALTER TABLE mip_app.campaigns
    ADD COLUMN IF NOT EXISTS treatment_contract_fingerprint TEXT;
ALTER TABLE mip_app.campaigns
    ADD COLUMN IF NOT EXISTS treatment_fingerprint TEXT;
ALTER TABLE mip_app.campaigns
    ADD COLUMN IF NOT EXISTS treatment_source_snapshot_id TEXT;
ALTER TABLE mip_app.campaigns
    ADD COLUMN IF NOT EXISTS treatment_delta_version BIGINT;
ALTER TABLE mip_app.campaigns
    ADD COLUMN IF NOT EXISTS treatment_assignment_digest TEXT;
ALTER TABLE mip_app.campaigns
    ADD COLUMN IF NOT EXISTS treatment_candidate_count BIGINT;
ALTER TABLE mip_app.campaigns
    ADD COLUMN IF NOT EXISTS treatment_selected_primary_count BIGINT;
ALTER TABLE mip_app.campaigns
    ADD COLUMN IF NOT EXISTS treatment_count BIGINT;
ALTER TABLE mip_app.campaigns
    ADD COLUMN IF NOT EXISTS treatment_holdout_count BIGINT;
ALTER TABLE mip_app.campaigns
    ADD COLUMN IF NOT EXISTS treatment_materialized_at TIMESTAMPTZ;
ALTER TABLE mip_app.campaigns
    ADD COLUMN IF NOT EXISTS treatment_build_lease_until TIMESTAMPTZ;
ALTER TABLE mip_app.campaigns
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
-- Existing deployments receive NULL first so the upgrade can distinguish
-- preserved rows from fresh version-1 writes without inspecting legacy JSON.
ALTER TABLE mip_app.campaigns
    ADD COLUMN IF NOT EXISTS json_contract_version SMALLINT;

-- Campaign JSON is a compatibility store, not an untyped API surface. These
-- immutable helpers back deploy-safe CHECK constraints added after the seed.
CREATE OR REPLACE FUNCTION mip_app.campaign_jsonb_has_only_keys(
    document JSONB,
    allowed_keys TEXT[]
)
RETURNS BOOLEAN
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT jsonb_typeof(document) = 'object'
       AND NOT EXISTS (
           SELECT 1
           FROM jsonb_object_keys(document) AS keys(key_name)
           WHERE NOT (key_name = ANY(allowed_keys))
       );
$$;

CREATE OR REPLACE FUNCTION mip_app.campaign_jsonb_text_array_is_reviewed(
    document JSONB,
    value_pattern TEXT,
    max_items INTEGER
)
RETURNS BOOLEAN
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT jsonb_typeof(document) = 'array'
       AND jsonb_array_length(document) <= max_items
       AND NOT EXISTS (
           SELECT 1
           FROM jsonb_array_elements(document) AS items(item)
           WHERE jsonb_typeof(item) <> 'string'
              OR item #>> '{}' !~ value_pattern
       );
$$;

CREATE OR REPLACE FUNCTION mip_app.campaign_portfolio_criteria_is_reviewed(
    document JSONB
)
RETURNS BOOLEAN
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT mip_app.campaign_jsonb_has_only_keys(
               document,
               ARRAY[
                   'geography','states','occupancy','lien_status',
                   'lender_relationship','product','loan_product',
                   'origination_channel','target_lender_ref','min_equity_pct',
                   'owner_link','purchase_intent','marketing_eligibility',
                   'consent_status','recency','min_equity_pct_label'
               ]::TEXT[]
           )
       AND (
           NOT document ? 'geography'
           OR (
               jsonb_typeof(document->'geography') = 'string'
               AND length(document->>'geography') BETWEEN 1 AND 160
           )
       )
       AND (
           NOT document ? 'states'
           OR mip_app.campaign_jsonb_text_array_is_reviewed(
               document->'states', '^[A-Z]{2}$', 56
           )
       )
       AND (
           NOT document ? 'occupancy'
           OR document->>'occupancy' = ANY(
               ARRAY['Owner-occupied','Non-owner-occupied','All']::TEXT[]
           )
       )
       AND (
           NOT document ? 'lien_status'
           OR document->>'lien_status' = ANY(
               ARRAY[
                   'Open 1st lien','Open first lien','Open HELOC',
                   'Free & clear','Free and clear','Any'
               ]::TEXT[]
           )
       )
       AND (
           NOT document ? 'lender_relationship'
           OR document->>'lender_relationship' = ANY(
               ARRAY[
                   'All','Current customer','Former customer',
                   'Competitor customer','Competitor'
               ]::TEXT[]
           )
       )
       AND (
           NOT document ? 'product'
           OR document->>'product' = ANY(
               ARRAY['All products','Refi','HELOC','Cash-out','Purchase','Retention']::TEXT[]
           )
       )
       AND (
           NOT document ? 'loan_product'
           OR document->>'loan_product' = ANY(
               ARRAY[
                   'All loan products','Conventional','Jumbo','FHA','VA',
                   'Other','Unknown'
               ]::TEXT[]
           )
       )
       AND (
           NOT document ? 'origination_channel'
           OR document->>'origination_channel' = ANY(
               ARRAY[
                   'All channels','Loan officer','Digital','Branch',
                   'Call center','Unknown'
               ]::TEXT[]
           )
       )
       AND (
           NOT document ? 'target_lender_ref'
           OR (
               jsonb_typeof(document->'target_lender_ref') = 'string'
               AND length(document->>'target_lender_ref') BETWEEN 1 AND 80
           )
       )
       AND (
           NOT document ? 'min_equity_pct'
           OR (
               jsonb_typeof(document->'min_equity_pct') = 'number'
               AND (document->>'min_equity_pct')::NUMERIC BETWEEN 0 AND 100
           )
       )
       AND (
           NOT document ? 'owner_link'
           OR document->>'owner_link' = ANY(
               ARRAY[
                   'All','Single-property owner','Multi-property (2-4)',
                   'Portfolio investor (5+)'
               ]::TEXT[]
           )
       )
       AND (
           NOT document ? 'purchase_intent'
           OR document->>'purchase_intent' = ANY(
               ARRAY['All','Listed for sale','HELOC intent','Both']::TEXT[]
           )
       )
       AND (
           NOT document ? 'marketing_eligibility'
           OR document->>'marketing_eligibility' = ANY(
               ARRAY['Eligible only','Any','Suppressed only']::TEXT[]
           )
       )
       AND (
           NOT document ? 'consent_status'
           OR document->>'consent_status' = ANY(
               ARRAY['Opt-in','Opt-out','Unknown','Any']::TEXT[]
           )
       )
       AND (
           NOT document ? 'recency'
           OR document->>'recency' = ANY(
               ARRAY['Untouched 30d','Untouched 60d','Untouched 90d','Any']::TEXT[]
           )
       )
       AND (
           NOT document ? 'min_equity_pct_label'
           OR document->>'min_equity_pct_label' = ANY(
               ARRAY['≥ 15%','≥ 25%','≥ 40%','Any']::TEXT[]
           )
       );
$$;

CREATE OR REPLACE FUNCTION mip_app.campaign_replay_filters_are_reviewed(
    document JSONB
)
RETURNS BOOLEAN
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT mip_app.campaign_jsonb_has_only_keys(
               document,
               ARRAY[
                   'zips','county','counties','states','segment_codes',
                   'segment_mode','target_lender_ref','portfolio_criteria',
                   'borrower_ids','source'
               ]::TEXT[]
           )
       AND (
           NOT document ? 'zips'
           OR mip_app.campaign_jsonb_text_array_is_reviewed(
               document->'zips', '^[0-9]{5}$', 500
           )
       )
       AND (
           NOT document ? 'county'
           OR (
               jsonb_typeof(document->'county') = 'string'
               AND document->>'county' ~ '^[0-9]{5}$'
           )
       )
       AND (
           NOT document ? 'counties'
           OR mip_app.campaign_jsonb_text_array_is_reviewed(
               document->'counties', '^[0-9]{5}$', 500
           )
       )
       AND (
           NOT document ? 'states'
           OR mip_app.campaign_jsonb_text_array_is_reviewed(
               document->'states', '^[A-Z]{2}$', 56
           )
       )
       AND (
           NOT document ? 'segment_codes'
           OR mip_app.campaign_jsonb_text_array_is_reviewed(
               document->'segment_codes',
               '^(itm|listed|permit|investor|equity|retention)$',
               6
           )
       )
       AND (
           NOT document ? 'segment_mode'
           OR document->>'segment_mode' = ANY(ARRAY['all','any']::TEXT[])
       )
       AND (
           NOT document ? 'target_lender_ref'
           OR (
               jsonb_typeof(document->'target_lender_ref') = 'string'
               AND length(document->>'target_lender_ref') BETWEEN 1 AND 80
           )
       )
       AND (
           NOT document ? 'portfolio_criteria'
           OR mip_app.campaign_portfolio_criteria_is_reviewed(
               document->'portfolio_criteria'
           )
       )
       AND (
           NOT document ? 'borrower_ids'
           OR mip_app.campaign_jsonb_text_array_is_reviewed(
               document->'borrower_ids', '^B-[0-9A-Z]{13}$', 500
           )
       )
       AND (
           NOT document ? 'source'
           OR document->>'source' = ANY(ARRAY['genie','trusted_sql']::TEXT[])
       );
$$;

CREATE OR REPLACE FUNCTION mip_app.campaign_criteria_is_reviewed(
    document JSONB
)
RETURNS BOOLEAN
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT CASE
        WHEN mip_app.campaign_portfolio_criteria_is_reviewed(document) THEN TRUE
        WHEN document ? 'segment' THEN
            mip_app.campaign_jsonb_has_only_keys(
                document,
                ARRAY[
                    'segment','min_spread_bps','min_equity_pct',
                    'heloc_equity_min_pct','heloc_propensity_min','intent_signal',
                    'filed_permits','states','marketing_eligibility',
                    'consent_status','recency'
                ]::TEXT[]
            )
            AND document->>'segment' = ANY(ARRAY['itm','cashout','heloc']::TEXT[])
            AND (
                NOT document ? 'states'
                OR mip_app.campaign_jsonb_text_array_is_reviewed(
                    document->'states', '^[A-Z]{2}$', 56
                )
            )
            AND (
                NOT document ? 'min_spread_bps'
                OR (
                    jsonb_typeof(document->'min_spread_bps') = 'number'
                    AND (document->>'min_spread_bps')::NUMERIC BETWEEN 0 AND 2000
                )
            )
            AND (
                NOT document ? 'min_equity_pct'
                OR (
                    jsonb_typeof(document->'min_equity_pct') = 'number'
                    AND (document->>'min_equity_pct')::NUMERIC BETWEEN 0 AND 100
                )
            )
            AND (
                NOT document ? 'heloc_equity_min_pct'
                OR (
                    jsonb_typeof(document->'heloc_equity_min_pct') = 'number'
                    AND (document->>'heloc_equity_min_pct')::NUMERIC BETWEEN 0 AND 100
                )
            )
            AND (
                NOT document ? 'heloc_propensity_min'
                OR (
                    jsonb_typeof(document->'heloc_propensity_min') = 'number'
                    AND (document->>'heloc_propensity_min')::NUMERIC BETWEEN 0 AND 1000
                )
            )
            AND (
                NOT document ? 'intent_signal'
                OR document->>'intent_signal' = 'cotality_heloc_propensity'
            )
            AND (
                NOT document ? 'filed_permits'
                OR document->>'filed_permits' = 'pending_not_inferred'
            )
            AND (
                NOT document ? 'marketing_eligibility'
                OR document->>'marketing_eligibility' = 'Eligible only'
            )
            AND (
                NOT document ? 'consent_status'
                OR document->>'consent_status' = ANY(
                    ARRAY['Opt-in','Opt-out','Unknown','Any']::TEXT[]
                )
            )
            AND (
                NOT document ? 'recency'
                OR document->>'recency' = ANY(
                    ARRAY['Untouched 30d','Untouched 60d','Untouched 90d','Any']::TEXT[]
                )
            )
        WHEN document ? 'source' THEN
            mip_app.campaign_jsonb_has_only_keys(
                document,
                ARRAY[
                    'source','borrower_ids','criteria_hash','criteria_keys',
                    'source_assets','visualization_kind','conversation_id',
                    'message_id','question_hash','row_count','route',
                    'result_filters','sql_hash'
                ]::TEXT[]
            )
            AND document->>'source' = ANY(ARRAY['genie','trusted_sql']::TEXT[])
            AND (
                NOT document ? 'borrower_ids'
                OR mip_app.campaign_jsonb_text_array_is_reviewed(
                    document->'borrower_ids', '^B-[0-9A-Z]{13}$', 500
                )
            )
            AND (
                NOT document ? 'criteria_keys'
                OR mip_app.campaign_jsonb_text_array_is_reviewed(
                    document->'criteria_keys', '^[a-z][a-z0-9_]{0,63}$', 50
                )
            )
            AND (
                NOT document ? 'source_assets'
                OR mip_app.campaign_jsonb_text_array_is_reviewed(
                    document->'source_assets', '^[A-Za-z0-9_.]{1,160}$', 10
                )
            )
            AND (
                NOT document ? 'result_filters'
                OR mip_app.campaign_replay_filters_are_reviewed(
                    document->'result_filters'
                )
            )
            AND (
                NOT document ? 'row_count'
                OR (
                    jsonb_typeof(document->'row_count') = 'number'
                    AND (document->>'row_count')::NUMERIC =
                        trunc((document->>'row_count')::NUMERIC)
                    AND (document->>'row_count')::NUMERIC BETWEEN 0 AND 10000000
                )
            )
            AND (
                NOT document ? 'route'
                OR document->'route' = 'null'::jsonb
                OR (
                    jsonb_typeof(document->'route') = 'string'
                    AND (
                        document->>'route' = '/lead-queue'
                        OR document->>'route' LIKE '/lead-queue?%'
                    )
                    AND length(document->>'route') <= 2000
                )
            )
            AND (
                NOT document ? 'visualization_kind'
                OR document->'visualization_kind' = 'null'::jsonb
                OR document->>'visualization_kind' = ANY(
                    ARRAY['bar','line','metric','pie','scatter','table']::TEXT[]
                )
            )
            AND (
                NOT document ? 'criteria_hash'
                OR document->'criteria_hash' = 'null'::jsonb
                OR (
                    jsonb_typeof(document->'criteria_hash') = 'string'
                    AND document->>'criteria_hash' ~ '^[A-Za-z0-9_-]{1,128}$'
                )
            )
            AND (
                NOT document ? 'sql_hash'
                OR document->'sql_hash' = 'null'::jsonb
                OR (
                    jsonb_typeof(document->'sql_hash') = 'string'
                    AND document->>'sql_hash' ~ '^[A-Za-z0-9_-]{1,128}$'
                )
            )
            AND (
                NOT document ? 'question_hash'
                OR document->'question_hash' = 'null'::jsonb
                OR (
                    jsonb_typeof(document->'question_hash') = 'string'
                    AND document->>'question_hash' ~ '^[A-Za-z0-9_-]{1,128}$'
                )
            )
            AND (
                NOT document ? 'conversation_id'
                OR document->'conversation_id' = 'null'::jsonb
                OR (
                    jsonb_typeof(document->'conversation_id') = 'string'
                    AND length(document->>'conversation_id') BETWEEN 1 AND 256
                )
            )
            AND (
                NOT document ? 'message_id'
                OR document->'message_id' = 'null'::jsonb
                OR (
                    jsonb_typeof(document->'message_id') = 'string'
                    AND length(document->>'message_id') BETWEEN 1 AND 256
                )
            )
            AND (
                (
                    document ? 'borrower_ids'
                    AND jsonb_array_length(document->'borrower_ids') > 0
                )
                OR (
                    document ? 'result_filters'
                    AND document->'result_filters' <> '{}'::jsonb
                )
            )
        ELSE FALSE
    END;
$$;

CREATE OR REPLACE FUNCTION mip_app.campaign_suppression_policy_is_reviewed(
    document JSONB
)
RETURNS BOOLEAN
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT mip_app.campaign_jsonb_has_only_keys(
               document,
               ARRAY[
                   'default','require_marketing_eligible',
                   'marketing_eligibility','frequency_cap_days'
               ]::TEXT[]
           )
       AND (NOT document ? 'default' OR document->>'default' = 'eligible_only')
       AND (
           NOT document ? 'require_marketing_eligible'
           OR jsonb_typeof(document->'require_marketing_eligible') = 'boolean'
       )
       AND (
           NOT document ? 'marketing_eligibility'
           OR document->>'marketing_eligibility' = 'Eligible only'
       )
       AND (
           NOT document ? 'frequency_cap_days'
           OR (
               jsonb_typeof(document->'frequency_cap_days') = 'number'
               AND (document->>'frequency_cap_days')::NUMERIC =
                   trunc((document->>'frequency_cap_days')::NUMERIC)
               AND (document->>'frequency_cap_days')::NUMERIC BETWEEN 30 AND 365
           )
       );
$$;

CREATE OR REPLACE FUNCTION mip_app.campaign_channel_cascade_is_reviewed(
    document JSONB
)
RETURNS BOOLEAN
LANGUAGE plpgsql
IMMUTABLE
PARALLEL SAFE
AS $$
DECLARE
    item JSONB;
    step_number INTEGER;
    seen_steps INTEGER[] := ARRAY[]::INTEGER[];
BEGIN
    IF jsonb_typeof(document) <> 'array' OR jsonb_array_length(document) > 6 THEN
        RETURN FALSE;
    END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(document) AS entries(value)
    LOOP
        IF NOT mip_app.campaign_jsonb_has_only_keys(
            item, ARRAY['channel','step','after_days']::TEXT[]
        ) THEN
            RETURN FALSE;
        END IF;
        IF NOT item ? 'channel' OR NOT item ? 'step' THEN
            RETURN FALSE;
        END IF;
        IF jsonb_typeof(item->'channel') <> 'string'
           OR NOT (item->>'channel' = ANY(ARRAY['email','sms','direct_mail']::TEXT[])) THEN
            RETURN FALSE;
        END IF;
        IF jsonb_typeof(item->'step') <> 'number'
           OR (item->>'step')::NUMERIC <> trunc((item->>'step')::NUMERIC)
           OR (item->>'step')::NUMERIC NOT BETWEEN 1 AND 100 THEN
            RETURN FALSE;
        END IF;
        step_number := (item->>'step')::INTEGER;
        IF step_number = ANY(seen_steps) THEN
            RETURN FALSE;
        END IF;
        seen_steps := array_append(seen_steps, step_number);
        IF item ? 'after_days' AND (
            jsonb_typeof(item->'after_days') <> 'number'
            OR (item->>'after_days')::NUMERIC <> trunc((item->>'after_days')::NUMERIC)
            OR (item->>'after_days')::NUMERIC NOT BETWEEN 0 AND 365
        ) THEN
            RETURN FALSE;
        END IF;
    END LOOP;
    RETURN TRUE;
END;
$$;

CREATE OR REPLACE FUNCTION mip_app.campaign_send_window_is_reviewed(
    document JSONB
)
RETURNS BOOLEAN
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT document = '{}'::jsonb
       OR (
           mip_app.campaign_jsonb_has_only_keys(
               document,
               ARRAY['days','timezone','start_local','end_local']::TEXT[]
           )
           AND mip_app.campaign_jsonb_text_array_is_reviewed(
               document->'days',
               '^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)$',
               7
           )
           AND jsonb_array_length(document->'days') > 0
           AND document->>'timezone' = 'borrower_local'
           AND document->>'start_local' ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'
           AND document->>'end_local' ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'
           AND document->>'start_local' < document->>'end_local'
       );
$$;

CREATE OR REPLACE FUNCTION mip_app.campaign_holdout_is_reviewed(
    document JSONB
)
RETURNS BOOLEAN
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT mip_app.campaign_jsonb_has_only_keys(
               document, ARRAY['method','size_pct']::TEXT[]
           )
       AND document->>'method' = 'hash_modulo'
       AND jsonb_typeof(document->'size_pct') = 'number'
       AND (document->>'size_pct')::NUMERIC BETWEEN 0 AND 50;
$$;

CREATE OR REPLACE FUNCTION mip_app.campaign_roi_assumptions_is_reviewed(
    document JSONB
)
RETURNS BOOLEAN
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT mip_app.campaign_jsonb_has_only_keys(
               document,
               ARRAY[
                   'budget_usd','expected_conversion_rate',
                   'expected_conversion_rate_pct','lo_capacity',
                   'cost_per_contact_usd','source'
               ]::TEXT[]
           )
       AND NOT EXISTS (
           SELECT 1
           FROM jsonb_each(document) AS entry(key_name, item)
           WHERE key_name = ANY(
               ARRAY[
                   'budget_usd','expected_conversion_rate',
                   'expected_conversion_rate_pct','lo_capacity'
               ]::TEXT[]
           )
             AND (
                 jsonb_typeof(item) <> 'number'
                 OR (item #>> '{}')::NUMERIC < 0
                 OR (
                     key_name = ANY(
                         ARRAY[
                             'expected_conversion_rate',
                             'expected_conversion_rate_pct'
                         ]::TEXT[]
                     )
                     AND (item #>> '{}')::NUMERIC > 100
                 )
             )
       )
       AND (
           NOT document ? 'cost_per_contact_usd'
           OR (
               jsonb_typeof(document->'cost_per_contact_usd') = 'number'
               AND (document->>'cost_per_contact_usd')::NUMERIC >= 0
           )
           OR (
               mip_app.campaign_jsonb_has_only_keys(
                   document->'cost_per_contact_usd',
                   ARRAY['email','sms','direct_mail']::TEXT[]
               )
               AND NOT EXISTS (
                   SELECT 1
                   FROM jsonb_each(document->'cost_per_contact_usd') AS cost(channel, amount)
                   WHERE jsonb_typeof(amount) <> 'number'
                      OR (amount #>> '{}')::NUMERIC < 0
               )
           )
       )
       AND (
           NOT document ? 'source'
           OR document->>'source' = ANY(
               ARRAY[
                   'operator_configured',
                   'operator_required_before_live_send'
               ]::TEXT[]
           )
       );
$$;

CREATE OR REPLACE FUNCTION mip_app.campaign_json_contract_is_reviewed(
    criteria_document JSONB,
    suppression_document JSONB,
    cascade_document JSONB,
    send_window_document JSONB,
    holdout_document JSONB,
    roi_document JSONB
)
RETURNS BOOLEAN
LANGUAGE SQL
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT mip_app.campaign_criteria_is_reviewed(criteria_document) IS TRUE
       AND mip_app.campaign_suppression_policy_is_reviewed(suppression_document) IS TRUE
       AND mip_app.campaign_channel_cascade_is_reviewed(cascade_document) IS TRUE
       AND mip_app.campaign_send_window_is_reviewed(send_window_document) IS TRUE
       AND (
           holdout_document IS NULL
           OR mip_app.campaign_holdout_is_reviewed(holdout_document) IS TRUE
       )
       AND (
           roi_document IS NULL
           OR mip_app.campaign_roi_assumptions_is_reviewed(roi_document) IS TRUE
       );
$$;

-- Rows that predate the explicit contract remain readable and status-
-- transitionable. Fresh installs already hold version 1 from CREATE TABLE;
-- only rows encountered during an in-place upgrade receive legacy version 0.
UPDATE mip_app.campaigns
SET json_contract_version = 0
WHERE json_contract_version IS NULL;
ALTER TABLE mip_app.campaigns
    ALTER COLUMN json_contract_version SET DEFAULT 1;
ALTER TABLE mip_app.campaigns
    ALTER COLUMN json_contract_version SET NOT NULL;

CREATE OR REPLACE FUNCTION mip_app.enforce_campaign_json_contract()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.json_contract_version := 1;
        IF mip_app.campaign_json_contract_is_reviewed(
            NEW.criteria,
            NEW.suppression_policy,
            NEW.channel_cascade,
            NEW.send_window,
            NEW.holdout,
            NEW.roi_assumptions
        ) IS NOT TRUE THEN
            RAISE EXCEPTION 'campaign JSON does not satisfy reviewed contract version 1'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.json_contract_version IS DISTINCT FROM OLD.json_contract_version
       OR NEW.criteria IS DISTINCT FROM OLD.criteria
       OR NEW.suppression_policy IS DISTINCT FROM OLD.suppression_policy
       OR NEW.channel_cascade IS DISTINCT FROM OLD.channel_cascade
       OR NEW.send_window IS DISTINCT FROM OLD.send_window
       OR NEW.holdout IS DISTINCT FROM OLD.holdout
       OR NEW.roi_assumptions IS DISTINCT FROM OLD.roi_assumptions THEN
        IF mip_app.campaign_json_contract_is_reviewed(
            NEW.criteria,
            NEW.suppression_policy,
            NEW.channel_cascade,
            NEW.send_window,
            NEW.holdout,
            NEW.roi_assumptions
        ) IS NOT TRUE THEN
            RAISE EXCEPTION 'modified campaign JSON must satisfy reviewed contract version 1'
                USING ERRCODE = '23514';
        END IF;
        NEW.json_contract_version := 1;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_campaigns_json_contract_enforcement
    ON mip_app.campaigns;
CREATE TRIGGER trg_campaigns_json_contract_enforcement
    BEFORE INSERT OR UPDATE ON mip_app.campaigns
    FOR EACH ROW
    EXECUTE FUNCTION mip_app.enforce_campaign_json_contract();

ALTER TABLE mip_app.campaigns
    DROP CONSTRAINT IF EXISTS campaigns_treatment_state_chk;
ALTER TABLE mip_app.campaigns
    ADD CONSTRAINT campaigns_treatment_state_chk
    CHECK (treatment_state IN ('legacy_unbound','building','ready','failed'));
ALTER TABLE mip_app.campaigns
    DROP CONSTRAINT IF EXISTS campaigns_treatment_counts_chk;
ALTER TABLE mip_app.campaigns
    ADD CONSTRAINT campaigns_treatment_counts_chk
    CHECK (
        treatment_candidate_count IS NULL
        OR (
            treatment_candidate_count >= 0
            AND treatment_selected_primary_count >= 0
            AND treatment_count >= 0
            AND treatment_holdout_count >= 0
            AND treatment_selected_primary_count <= treatment_candidate_count
            AND treatment_selected_primary_count = treatment_count + treatment_holdout_count
        )
    );
ALTER TABLE mip_app.campaigns
    DROP CONSTRAINT IF EXISTS campaigns_ready_treatment_manifest_chk;
ALTER TABLE mip_app.campaigns
    ADD CONSTRAINT campaigns_ready_treatment_manifest_chk
    CHECK (
        treatment_state <> 'ready'
        OR (
            treatment_materialization_id IS NOT NULL
            AND treatment_algorithm_version = 'campaign-treatment-v2'
            AND treatment_contract_fingerprint ~ '^[0-9a-f]{64}$'
            AND treatment_fingerprint ~ '^[0-9a-f]{64}$'
            AND treatment_source_snapshot_id ~ '^[0-9a-f]{64}$'
            AND treatment_delta_version >= 0
            AND treatment_assignment_digest ~ '^[0-9a-f]{64}$'
            AND treatment_candidate_count IS NOT NULL
            AND treatment_selected_primary_count IS NOT NULL
            AND treatment_count IS NOT NULL
            AND treatment_holdout_count IS NOT NULL
            AND treatment_materialized_at IS NOT NULL
        )
    );

CREATE OR REPLACE FUNCTION mip_app.enforce_campaign_treatment_boundary()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.treatment_state = 'ready' THEN
            RAISE EXCEPTION 'campaign treatment must pass through building before ready'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.treatment_state IN ('building','ready','failed') AND (
        NEW.criteria IS DISTINCT FROM OLD.criteria
        OR NEW.json_contract_version IS DISTINCT FROM OLD.json_contract_version
        OR NEW.suppression_policy IS DISTINCT FROM OLD.suppression_policy
        OR NEW.holdout IS DISTINCT FROM OLD.holdout
        OR NEW.household_dedup IS DISTINCT FROM OLD.household_dedup
        OR NEW.treatment_algorithm_version IS DISTINCT FROM OLD.treatment_algorithm_version
        OR NEW.treatment_contract_fingerprint IS DISTINCT FROM OLD.treatment_contract_fingerprint
    ) THEN
        RAISE EXCEPTION 'campaign treatment contract is immutable after reservation'
            USING ERRCODE = '55000';
    END IF;

    IF NEW.treatment_materialization_id IS DISTINCT FROM OLD.treatment_materialization_id
       AND NOT (
           OLD.treatment_state = 'building'
           AND NEW.treatment_state = 'building'
           AND OLD.treatment_build_lease_until <= now()
           AND OLD.treatment_fingerprint IS NULL
           AND OLD.treatment_delta_version IS NULL
       ) THEN
        RAISE EXCEPTION 'campaign materialization id may rotate only during an expired build reclaim'
            USING ERRCODE = '55000';
    END IF;

    IF OLD.treatment_state = 'legacy_unbound' AND NEW.treatment_state <> 'legacy_unbound' THEN
        RAISE EXCEPTION 'legacy campaigns must be rebuilt with a new campaign id'
            USING ERRCODE = '55000';
    END IF;

    IF OLD.treatment_state = 'ready' AND (
        NEW.treatment_state IS DISTINCT FROM OLD.treatment_state
        OR NEW.treatment_fingerprint IS DISTINCT FROM OLD.treatment_fingerprint
        OR NEW.treatment_source_snapshot_id IS DISTINCT FROM OLD.treatment_source_snapshot_id
        OR NEW.treatment_delta_version IS DISTINCT FROM OLD.treatment_delta_version
        OR NEW.treatment_assignment_digest IS DISTINCT FROM OLD.treatment_assignment_digest
        OR NEW.treatment_candidate_count IS DISTINCT FROM OLD.treatment_candidate_count
        OR NEW.treatment_selected_primary_count IS DISTINCT FROM OLD.treatment_selected_primary_count
        OR NEW.treatment_count IS DISTINCT FROM OLD.treatment_count
        OR NEW.treatment_holdout_count IS DISTINCT FROM OLD.treatment_holdout_count
        OR NEW.treatment_materialized_at IS DISTINCT FROM OLD.treatment_materialized_at
    ) THEN
        RAISE EXCEPTION 'ready campaign treatment manifest is immutable'
            USING ERRCODE = '55000';
    END IF;

    IF NEW.treatment_state = 'ready' AND OLD.treatment_state <> 'building' THEN
        RAISE EXCEPTION 'only a building campaign treatment may become ready'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_campaigns_treatment_boundary ON mip_app.campaigns;
CREATE TRIGGER trg_campaigns_treatment_boundary
    BEFORE INSERT OR UPDATE ON mip_app.campaigns
    FOR EACH ROW
    EXECUTE FUNCTION mip_app.enforce_campaign_treatment_boundary();

-- Activation may only consume an immutable T0 treatment. Quarantine any
-- pre-treatment campaigns that were historically seeded or promoted as
-- active, then make the invariant database-enforced for every future writer.
UPDATE mip_app.campaigns
SET status = 'archived',
    updated_at = now()
WHERE status = 'active'
  AND treatment_state <> 'ready';

ALTER TABLE mip_app.campaigns
    DROP CONSTRAINT IF EXISTS campaigns_active_requires_ready_treatment_chk;
ALTER TABLE mip_app.campaigns
    ADD CONSTRAINT campaigns_active_requires_ready_treatment_chk
    CHECK (status <> 'active' OR treatment_state = 'ready');

-- The post-seed NOT VALID checks retain the legacy-version escape hatch on
-- unchanged payloads. The trigger above closes that hatch for inserts and any
-- governed JSON modification, promoting successful remediations to version 1.
ALTER TABLE mip_app.campaigns
    DROP CONSTRAINT IF EXISTS campaigns_criteria_reviewed_shape_chk;
ALTER TABLE mip_app.campaigns
    DROP CONSTRAINT IF EXISTS campaigns_suppression_policy_reviewed_shape_chk;
ALTER TABLE mip_app.campaigns
    DROP CONSTRAINT IF EXISTS campaigns_channel_cascade_reviewed_shape_chk;
ALTER TABLE mip_app.campaigns
    DROP CONSTRAINT IF EXISTS campaigns_send_window_reviewed_shape_chk;
ALTER TABLE mip_app.campaigns
    DROP CONSTRAINT IF EXISTS campaigns_holdout_reviewed_shape_chk;
ALTER TABLE mip_app.campaigns
    DROP CONSTRAINT IF EXISTS campaigns_roi_assumptions_reviewed_shape_chk;

CREATE INDEX IF NOT EXISTS idx_campaigns_owner
    ON mip_app.campaigns (owner_email, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_campaigns_status
    ON mip_app.campaigns (status, updated_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_campaigns_owner_idempotency
    ON mip_app.campaigns (owner_email, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

INSERT INTO mip_app.schema_migrations (version, description)
VALUES (
    '2026_07_14_campaign_idempotency',
    'Add owner-scoped campaign idempotency keys and request payload hashes'
)
ON CONFLICT (version) DO NOTHING;

INSERT INTO mip_app.schema_migrations (version, description)
VALUES (
    '2026_07_15_campaign_t0_treatment_boundary',
    'Reserve campaigns in Lakebase and bind ready execution to an immutable Unity Catalog treatment snapshot'
)
ON CONFLICT (version) DO NOTHING;

CREATE TABLE IF NOT EXISTS mip_app.campaign_message_variants (
    campaign_id  UUID NOT NULL REFERENCES mip_app.campaigns(campaign_id) ON DELETE CASCADE,
    variant_name TEXT NOT NULL,
    channel      TEXT NOT NULL CHECK (channel IN ('email','sms','direct_mail')),
    subject      TEXT,
    body         TEXT NOT NULL CHECK (length(body) <= 5000),
    weight_pct   NUMERIC,
    generation_mode TEXT NOT NULL DEFAULT 'operator'
                    CHECK (generation_mode IN ('supervisor','reviewed_fallback','operator')),
    generator_label TEXT NOT NULL DEFAULT 'Operator edited',
    provenance_key_id TEXT,
    provenance_issued_at TIMESTAMPTZ,
    provenance_expires_at TIMESTAMPTZ,
    provenance_copy_hash TEXT,
    provenance_criteria_fingerprint TEXT,
    provenance_performance_fingerprint TEXT,
    provenance_token_digest TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (campaign_id, variant_name, channel)
);
ALTER TABLE mip_app.campaign_message_variants
    ADD COLUMN IF NOT EXISTS generation_mode TEXT NOT NULL DEFAULT 'operator';
ALTER TABLE mip_app.campaign_message_variants
    ADD COLUMN IF NOT EXISTS generator_label TEXT NOT NULL DEFAULT 'Operator edited';
ALTER TABLE mip_app.campaign_message_variants
    ADD COLUMN IF NOT EXISTS provenance_key_id TEXT;
ALTER TABLE mip_app.campaign_message_variants
    ADD COLUMN IF NOT EXISTS provenance_issued_at TIMESTAMPTZ;
ALTER TABLE mip_app.campaign_message_variants
    ADD COLUMN IF NOT EXISTS provenance_expires_at TIMESTAMPTZ;
ALTER TABLE mip_app.campaign_message_variants
    ADD COLUMN IF NOT EXISTS provenance_copy_hash TEXT;
ALTER TABLE mip_app.campaign_message_variants
    ADD COLUMN IF NOT EXISTS provenance_criteria_fingerprint TEXT;
ALTER TABLE mip_app.campaign_message_variants
    ADD COLUMN IF NOT EXISTS provenance_performance_fingerprint TEXT;
ALTER TABLE mip_app.campaign_message_variants
    ADD COLUMN IF NOT EXISTS provenance_token_digest TEXT;
-- Recurring seed installation retains three historical operator rows. Drop
-- the forward-write guard only inside this migration transaction; it is
-- restored after the insert-only seed block below.
ALTER TABLE mip_app.campaign_message_variants
    DROP CONSTRAINT IF EXISTS campaign_message_variants_server_owned_proof_chk;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'campaign_message_variants_generation_mode_chk'
          AND conrelid = 'mip_app.campaign_message_variants'::regclass
    ) THEN
        ALTER TABLE mip_app.campaign_message_variants
            ADD CONSTRAINT campaign_message_variants_generation_mode_chk
            CHECK (generation_mode IN ('supervisor','reviewed_fallback','operator'));
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS mip_app.tenant_disclosures (
    -- Per-deployment disclosure namespace. Summit dev seeds use "summit";
    -- customer deploys should set MIP_TENANT_ID or use the slug derived
    -- from MIP_LENDER_NAME and seed their own approved disclosures.
    tenant_id           TEXT NOT NULL DEFAULT 'summit',
    state               TEXT NOT NULL,
    channel             TEXT NOT NULL CHECK (channel IN ('email','sms','direct_mail')),
    disclosure_version  TEXT NOT NULL,
    body                TEXT NOT NULL CHECK (length(body) BETWEEN 20 AND 2000),
    active              BOOLEAN NOT NULL DEFAULT true,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, state, channel, disclosure_version)
);
CREATE INDEX IF NOT EXISTS idx_tenant_disclosures_active
    ON mip_app.tenant_disclosures (tenant_id, state, channel, active, updated_at DESC);

-- Sales operations ----------------------------------------------------
-- Thin Module 0 work-management layer for the named Sales Manager
-- persona. These tables store internal staff identity and synthetic
-- borrower ids only. Borrower names, emails, phone numbers, street
-- addresses, and raw Cotality identifiers do not belong here.
CREATE TABLE IF NOT EXISTS mip_app.sales_team (
    email            TEXT PRIMARY KEY,
    display_label    TEXT NOT NULL,
    role             TEXT NOT NULL DEFAULT 'loan_officer'
                     CHECK (role IN ('loan_officer','sales_manager','admin')),
    manager_email    TEXT,
    region           TEXT,
    capacity_per_day INTEGER NOT NULL DEFAULT 35
                     CHECK (capacity_per_day BETWEEN 0 AND 250),
    active           BOOLEAN NOT NULL DEFAULT true,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sales_team_manager
    ON mip_app.sales_team (manager_email, active, display_label);

CREATE TABLE IF NOT EXISTS mip_app.lead_assignments (
    assignment_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    borrower_id       TEXT NOT NULL,
    assigned_to_email TEXT NOT NULL REFERENCES mip_app.sales_team(email),
    assigned_by       TEXT NOT NULL,
    assigned_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at        TIMESTAMPTZ,
    released_at       TIMESTAMPTZ,
    strategy          TEXT NOT NULL DEFAULT 'manual'
                      CHECK (strategy IN ('manual','round_robin','score_balanced')),
    request_id        TEXT,
    assignment_scope  TEXT NOT NULL DEFAULT 'single'
                      CHECK (assignment_scope IN ('single','distribution'))
);
ALTER TABLE mip_app.lead_assignments
    ADD COLUMN IF NOT EXISTS assignment_scope TEXT NOT NULL DEFAULT 'single';
UPDATE mip_app.lead_assignments
SET assignment_scope = 'distribution'
WHERE request_id IS NOT NULL
  AND request_id IN (
      SELECT request_id
      FROM mip_app.lead_assignments
      WHERE request_id IS NOT NULL
      GROUP BY request_id
      HAVING COUNT(*) > 1
  );
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_lead_assignments_assignment_scope'
          AND conrelid = 'mip_app.lead_assignments'::regclass
    ) THEN
        ALTER TABLE mip_app.lead_assignments
            ADD CONSTRAINT ck_lead_assignments_assignment_scope
            CHECK (assignment_scope IN ('single','distribution'));
    END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS idx_lead_assignments_active_borrower
    ON mip_app.lead_assignments (borrower_id)
    WHERE released_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_lead_assignments_assignee
    ON mip_app.lead_assignments (assigned_to_email, assigned_at DESC)
    WHERE released_at IS NULL;
DROP INDEX IF EXISTS mip_app.idx_lead_assignments_request_id;
CREATE UNIQUE INDEX IF NOT EXISTS idx_lead_assignments_request_borrower
    ON mip_app.lead_assignments (request_id, borrower_id)
    WHERE request_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_lead_assignments_single_request_id
    ON mip_app.lead_assignments (request_id)
    WHERE request_id IS NOT NULL AND assignment_scope = 'single';

CREATE TABLE IF NOT EXISTS mip_app.call_dispositions (
    disposition_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    borrower_id    TEXT NOT NULL,
    lo_email       TEXT NOT NULL REFERENCES mip_app.sales_team(email),
    outcome        TEXT NOT NULL CHECK (
        outcome IN (
            'called_no_answer','called_left_voicemail','connected',
            'callback_scheduled','application_started','not_interested',
            'not_now','dead'
        )
    ),
    attempt_number INTEGER NOT NULL DEFAULT 1 CHECK (attempt_number > 0),
    occurred_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    callback_at    TIMESTAMPTZ,
    notes          TEXT CHECK (notes IS NULL OR length(notes) <= 500),
    audit_event_id UUID,
    request_id     TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Recurring schema application may need to install upgraded proof guards.
-- PostgreSQL DDL is transactional, so a later failure restores the prior
-- triggers together with the rest of the migration.
DROP TRIGGER IF EXISTS trg_call_dispositions_finalize_only
    ON mip_app.call_dispositions;
DROP TRIGGER IF EXISTS trg_call_dispositions_no_remove
    ON mip_app.call_dispositions;
CREATE INDEX IF NOT EXISTS idx_call_dispositions_borrower
    ON mip_app.call_dispositions (borrower_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_call_dispositions_lo
    ON mip_app.call_dispositions (lo_email, occurred_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_call_dispositions_request_id
    ON mip_app.call_dispositions (request_id)
    WHERE request_id IS NOT NULL;

-- Approvals -----------------------------------------------------------
-- One row per human-in-the-loop decision on an outreach draft.
-- `borrower_id` is the synthetic `B-#####` stable id today; production
-- can swap to `clip_ref`. `action` is the approve / reject / hold verb.
CREATE TABLE IF NOT EXISTS mip_app.approvals (
    approval_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id   UUID,
    variant_name  TEXT,
    channel       TEXT CHECK (channel IN ('email','sms','direct_mail')),
    borrower_id   TEXT NOT NULL,
    offer_code    TEXT,
    action        TEXT NOT NULL CHECK (action IN ('approve','reject','hold')),
    actor_email   TEXT NOT NULL,
    rationale     TEXT,
    request_id    TEXT,
    decision_intent TEXT,
    decision_payload_hash TEXT,
    decision_response JSONB,
    audit_event_id UUID,
    decided_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- The schema is reapplied transactionally. Drop the proof guards only so
-- explicit compatibility backfills below can run; they are recreated before
-- commit. Historical evidence is never deleted by a recurring deployment.
DROP TRIGGER IF EXISTS trg_approvals_finalize_only ON mip_app.approvals;
DROP TRIGGER IF EXISTS trg_approvals_no_remove ON mip_app.approvals;
DROP TRIGGER IF EXISTS trg_approvals_campaign_lifecycle ON mip_app.approvals;
-- R5-01 idempotency key: when the backend retries an approve/reject after
-- a lost 503 response, the re-POSTed ``request_id`` collides on this
-- partial unique index so we don't write a duplicate decision row. The
-- NULL-exempt filter preserves back-compat with legacy callers that
-- don't pass a request_id yet (they still insert, they just don't get
-- the retry-safe guarantee).
ALTER TABLE mip_app.approvals
    ADD COLUMN IF NOT EXISTS request_id TEXT;
ALTER TABLE mip_app.approvals
    ADD COLUMN IF NOT EXISTS decision_intent TEXT;
ALTER TABLE mip_app.approvals
    ADD COLUMN IF NOT EXISTS decision_payload_hash TEXT;
ALTER TABLE mip_app.approvals
    ADD COLUMN IF NOT EXISTS decision_response JSONB;
ALTER TABLE mip_app.approvals
    ADD COLUMN IF NOT EXISTS audit_event_id UUID;
ALTER TABLE mip_app.approvals
    ADD COLUMN IF NOT EXISTS variant_name TEXT;
ALTER TABLE mip_app.approvals
    ADD COLUMN IF NOT EXISTS channel TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_approvals_request_id
    ON mip_app.approvals (request_id) WHERE request_id IS NOT NULL;
-- Feature C (loan-officer assignment + follow-up reminder): persist the
-- approver's chosen LO and an optional "follow up in N days" timestamp on
-- the approval decision. Both are nullable so legacy callers (and the bulk
-- approve path) keep inserting without them. Reminder DELIVERY is out of
-- scope -- we only persist the assignment + the computed follow_up_at.
ALTER TABLE mip_app.approvals
    ADD COLUMN IF NOT EXISTS assigned_to_email TEXT;
ALTER TABLE mip_app.approvals
    ADD COLUMN IF NOT EXISTS follow_up_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_approvals_campaign
    ON mip_app.approvals (campaign_id, decided_at DESC);
CREATE INDEX IF NOT EXISTS idx_approvals_borrower
    ON mip_app.approvals (borrower_id, decided_at DESC);

-- Saved workspace ----------------------------------------------------
-- Actor-scoped saved leads and draft outreach copy. These tables back
-- the in-app inbox/workspace; they intentionally store synthetic
-- borrower ids plus coarse location / offer metadata only. Raw names,
-- street addresses, emails, phone numbers, and raw Cotality CLIPs do
-- not belong here.
CREATE TABLE IF NOT EXISTS mip_app.saved_leads (
    actor_email       TEXT NOT NULL,
    borrower_id       TEXT NOT NULL,
    city              TEXT,
    state             TEXT,
    zip               TEXT,
    recommended_offer TEXT,
    opportunity_score INTEGER CHECK (opportunity_score BETWEEN 0 AND 100),
    confidence        INTEGER CHECK (confidence BETWEEN 0 AND 100),
    saved_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at        TIMESTAMPTZ,
    PRIMARY KEY (actor_email, borrower_id)
);
CREATE INDEX IF NOT EXISTS idx_saved_leads_actor_updated
    ON mip_app.saved_leads (actor_email, updated_at DESC)
    WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS mip_app.outreach_drafts (
    actor_email  TEXT NOT NULL,
    borrower_id  TEXT NOT NULL,
    generation_id UUID,
    response_hash TEXT,
    offer_code   TEXT,
    channel      TEXT NOT NULL DEFAULT 'email' CHECK (channel IN ('email','sms','direct_mail')),
    subject      TEXT CHECK (subject IS NULL OR length(subject) <= 120),
    body         TEXT NOT NULL CHECK (length(body) <= 5000),
    status       TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','released')),
    saved_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at   TIMESTAMPTZ,
    PRIMARY KEY (actor_email, borrower_id, channel)
);
ALTER TABLE mip_app.outreach_drafts
    ADD COLUMN IF NOT EXISTS generation_id UUID;
ALTER TABLE mip_app.outreach_drafts
    ADD COLUMN IF NOT EXISTS response_hash TEXT;
ALTER TABLE mip_app.outreach_drafts
    DROP CONSTRAINT IF EXISTS outreach_drafts_generation_proof_check;
ALTER TABLE mip_app.outreach_drafts
    ADD CONSTRAINT outreach_drafts_generation_proof_check CHECK (
        (generation_id IS NULL AND response_hash IS NULL)
        OR (
            generation_id IS NOT NULL
            AND response_hash ~ '^[0-9a-f]{64}$'
        )
    );
ALTER TABLE mip_app.outreach_drafts
    ADD COLUMN IF NOT EXISTS subject TEXT;
ALTER TABLE mip_app.outreach_drafts
    DROP CONSTRAINT IF EXISTS outreach_drafts_subject_check;
ALTER TABLE mip_app.outreach_drafts
    ADD CONSTRAINT outreach_drafts_subject_check
    CHECK (subject IS NULL OR length(subject) <= 120);
ALTER TABLE mip_app.outreach_drafts
    DROP CONSTRAINT IF EXISTS outreach_drafts_channel_check;
ALTER TABLE mip_app.outreach_drafts
    ADD CONSTRAINT outreach_drafts_channel_check CHECK (channel IN ('email','sms','direct_mail'));
CREATE INDEX IF NOT EXISTS idx_outreach_drafts_actor_updated
    ON mip_app.outreach_drafts (actor_email, updated_at DESC)
    WHERE deleted_at IS NULL;

INSERT INTO mip_app.schema_migrations (version, description)
VALUES (
    '2026_07_13_outreach_draft_subject',
    'Persist the exact email or direct-mail subject through workspace review and approval audit'
)
ON CONFLICT (version) DO NOTHING;

-- Activation / customer writeback --------------------------------------
-- Product boundary: Module 0 can stage an approved lead or campaign for a
-- customer destination, but it does not auto-send email/SMS or write to an
-- external CRM/CDP/LOS/POS until a customer-specific connector is configured
-- and separately approved. These tables are the governed outbox contract:
-- synthetic borrower ids, public-safe campaign/approval ids, offer/channel
-- metadata, and delivery status only. Raw owner names, contact data, street
-- addresses, raw CLIPs, account numbers, and destination credentials do not
-- belong here.
CREATE TABLE IF NOT EXISTS mip_app.activation_destinations (
    destination_key  TEXT PRIMARY KEY,
    destination_type TEXT NOT NULL CHECK (
        destination_type IN ('salesforce','crm_cdp','los_pos','servicing','webhook')
    ),
    display_name     TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'not_configured'
                     CHECK (status IN ('not_configured','dry_run','connected','disabled')),
    allowed_actions  TEXT[] NOT NULL DEFAULT ARRAY['stage_lead']::TEXT[],
    pii_policy       JSONB NOT NULL DEFAULT '{"borrower_contact_fields":"blocked"}'::jsonb,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_activation_destinations_status
    ON mip_app.activation_destinations (status, destination_type);

INSERT INTO mip_app.activation_destinations (
    destination_key, destination_type, display_name, status, allowed_actions, pii_policy
) VALUES
    (
        'salesforce_crm',
        'salesforce',
        'Salesforce CRM',
        'not_configured',
        ARRAY['stage_lead','stage_campaign']::TEXT[],
        '{"borrower_contact_fields":"blocked","copy_mode":"approved_draft_reference_only"}'::jsonb
    ),
    (
        'customer_cdp',
        'crm_cdp',
        'Customer CDP',
        'not_configured',
        ARRAY['stage_lead','stage_campaign']::TEXT[],
        '{"borrower_contact_fields":"blocked","copy_mode":"approved_draft_reference_only"}'::jsonb
    ),
    (
        'los_pos',
        'los_pos',
        'LOS / POS',
        'not_configured',
        ARRAY['stage_lead']::TEXT[],
        '{"borrower_contact_fields":"blocked","application_event_fields":"hashed_only"}'::jsonb
    ),
    (
        'servicing_platform',
        'servicing',
        'Servicing Platform',
        'not_configured',
        ARRAY['stage_lead']::TEXT[],
        '{"borrower_contact_fields":"blocked","loan_account_fields":"hashed_only"}'::jsonb
    )
ON CONFLICT (destination_key) DO NOTHING;

CREATE TABLE IF NOT EXISTS mip_app.activation_outbox (
    activation_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    destination_key   TEXT NOT NULL REFERENCES mip_app.activation_destinations(destination_key),
    entity_type       TEXT NOT NULL CHECK (entity_type IN ('borrower','campaign','cohort')),
    entity_id         TEXT NOT NULL,
    borrower_id       TEXT NOT NULL,
    campaign_id       UUID,
    approval_id       UUID NOT NULL REFERENCES mip_app.approvals(approval_id),
    offer_code        TEXT,
    channel           TEXT NOT NULL CHECK (channel IN ('email','sms','direct_mail')),
    status            TEXT NOT NULL DEFAULT 'dry_run'
                      CHECK (status IN ('dry_run','staged','delivered','failed','cancelled')),
    request_id        TEXT NOT NULL,
    created_by        TEXT NOT NULL,
    payload_json      JSONB NOT NULL DEFAULT '{}'::jsonb,
    delivery_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE mip_app.activation_outbox
    ALTER COLUMN borrower_id SET NOT NULL;
ALTER TABLE mip_app.activation_outbox
    ALTER COLUMN approval_id SET NOT NULL;
ALTER TABLE mip_app.activation_outbox
    ALTER COLUMN channel SET NOT NULL;
ALTER TABLE mip_app.activation_outbox
    ALTER COLUMN request_id SET NOT NULL;
ALTER TABLE mip_app.activation_outbox
    DROP CONSTRAINT IF EXISTS activation_outbox_approval_fk;
ALTER TABLE mip_app.activation_outbox
    ADD CONSTRAINT activation_outbox_approval_fk
    FOREIGN KEY (approval_id) REFERENCES mip_app.approvals(approval_id);
ALTER TABLE mip_app.activation_outbox
    DROP CONSTRAINT IF EXISTS activation_outbox_channel_check;
ALTER TABLE mip_app.activation_outbox
    ADD CONSTRAINT activation_outbox_channel_check
    CHECK (channel IN ('email','sms','direct_mail'));
DROP INDEX IF EXISTS mip_app.idx_activation_outbox_request_id;
CREATE UNIQUE INDEX IF NOT EXISTS idx_activation_outbox_request_id
    ON mip_app.activation_outbox (request_id);

-- Install the delivery-time campaign proof gate exactly once. schema.sql is
-- intentionally replayable on every deploy, so an unguarded UPDATE here would
-- cancel rows produced by the new proof-bound writer on every later release.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM mip_app.schema_migrations
        WHERE version = '2026_07_25_campaign_activation_delivery_reproof'
    ) THEN
        UPDATE mip_app.activation_outbox
        SET status = 'cancelled',
            delivery_metadata = COALESCE(delivery_metadata, '{}'::jsonb)
                || '{"cancelled_reason":"campaign_treatment_reproof_required"}'::jsonb,
            updated_at = now()
        WHERE campaign_id IS NOT NULL
          AND status IN ('dry_run','staged','failed');

        INSERT INTO mip_app.schema_migrations (version, description)
        VALUES (
            '2026_07_25_campaign_activation_delivery_reproof',
            'Cancel only pre-gate campaign activation rows before delivery-time treatment reproof becomes authoritative'
        );
    END IF;
END;
$$;

-- A historical failed handoff may share a business key with another active
-- row because the former index excluded failures. Preserve the oldest,
-- highest-authority row (and therefore its stable external idempotency key)
-- and cancel only additional duplicates before widening the unique boundary.
WITH ranked_activation_business_keys AS (
    SELECT activation_id,
           ROW_NUMBER() OVER (
               PARTITION BY destination_key, approval_id, borrower_id, channel
               ORDER BY
                   CASE WHEN status = 'delivered' THEN 0 ELSE 1 END,
                   created_at ASC,
                   activation_id ASC
           ) AS business_key_rank
    FROM mip_app.activation_outbox
    WHERE status IN ('dry_run','staged','failed','delivered')
)
UPDATE mip_app.activation_outbox AS activation
SET status = 'cancelled',
    delivery_metadata = COALESCE(activation.delivery_metadata, '{}'::jsonb)
        || '{"cancelled_reason":"duplicate_business_key_reconciled"}'::jsonb,
    updated_at = now()
FROM ranked_activation_business_keys AS ranked
WHERE activation.activation_id = ranked.activation_id
  AND ranked.business_key_rank > 1;

DROP INDEX IF EXISTS mip_app.idx_activation_outbox_business_key;
CREATE UNIQUE INDEX IF NOT EXISTS idx_activation_outbox_business_key
    ON mip_app.activation_outbox (destination_key, approval_id, borrower_id, channel)
    WHERE status IN ('dry_run','staged','failed','delivered');
CREATE INDEX IF NOT EXISTS idx_activation_outbox_created
    ON mip_app.activation_outbox (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activation_outbox_borrower
    ON mip_app.activation_outbox (borrower_id, created_at DESC)
    WHERE borrower_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_activation_outbox_destination
    ON mip_app.activation_outbox (destination_key, status, created_at DESC);

-- Closed-loop outcome ingestion --------------------------------------
-- Customer LOS/POS/CRM/servicing systems can report what happened after a
-- lead was approved, assigned, and activated. This is the governed
-- scorekeeper table discussed in the Movement walkthrough: which leads
-- submitted an application, closed/funded, withdrew, failed qualification, or
-- were lost to a competitor. Keep it public-id only. Raw borrower names,
-- contact fields, street addresses, account numbers, full external payloads,
-- and raw CLIPs do not belong here.
CREATE TABLE IF NOT EXISTS mip_app.lead_outcomes (
    outcome_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    borrower_id             TEXT NOT NULL,
    outcome_type            TEXT NOT NULL CHECK (
        outcome_type IN (
            'application_submitted',
            'closed_funded',
            'lost_to_competitor',
            'withdrawn',
            'not_qualified'
        )
    ),
    source_system           TEXT NOT NULL CHECK (
        source_system IN (
            'salesforce',
            'crm_cdp',
            'los_pos',
            'servicing',
            'webhook',
            'manual_import'
        )
    ),
    source_record_ref       TEXT,
    assigned_to_email       TEXT REFERENCES mip_app.sales_team(email),
    campaign_id             UUID REFERENCES mip_app.campaigns(campaign_id) ON DELETE SET NULL,
    loan_amount             INTEGER CHECK (loan_amount IS NULL OR loan_amount BETWEEN 0 AND 100000000),
    competitor_lender_label TEXT,
    occurred_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_id              TEXT,
    payload_json            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by              TEXT NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    audit_event_id          UUID,
    CONSTRAINT ck_lead_outcomes_idempotency_key CHECK (
        request_id IS NOT NULL OR source_record_ref IS NOT NULL
    )
);
DROP TRIGGER IF EXISTS trg_lead_outcomes_finalize_only
    ON mip_app.lead_outcomes;
DROP TRIGGER IF EXISTS trg_lead_outcomes_no_remove
    ON mip_app.lead_outcomes;
COMMENT ON TABLE mip_app.lead_outcomes IS
    'PII-safe closed-loop lead outcomes imported from customer CRM/LOS/POS/servicing systems.';
COMMENT ON COLUMN mip_app.lead_outcomes.payload_json IS
    'Reviewed, non-PII context only. Raw borrower contact data and account numbers are forbidden.';
UPDATE mip_app.lead_outcomes
SET competitor_lender_label = 'Competitor Other'
WHERE competitor_lender_label IS NOT NULL
  AND competitor_lender_label !~ '^Competitor ([A-Z]|Other)$';
UPDATE mip_app.lead_outcomes
SET request_id = 'auto-' || md5(outcome_id::text)
WHERE request_id IS NULL
  AND source_record_ref IS NULL;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_lead_outcomes_idempotency_key'
          AND conrelid = 'mip_app.lead_outcomes'::regclass
    ) THEN
        ALTER TABLE mip_app.lead_outcomes
            ADD CONSTRAINT ck_lead_outcomes_idempotency_key
            CHECK (request_id IS NOT NULL OR source_record_ref IS NOT NULL);
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_lead_outcomes_competitor_label'
          AND conrelid = 'mip_app.lead_outcomes'::regclass
    ) THEN
        ALTER TABLE mip_app.lead_outcomes
            ADD CONSTRAINT ck_lead_outcomes_competitor_label
            CHECK (
                competitor_lender_label IS NULL
                OR competitor_lender_label ~ '^Competitor ([A-Z]|Other)$'
            );
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_lead_outcomes_source_record_ref'
          AND conrelid = 'mip_app.lead_outcomes'::regclass
    ) THEN
        ALTER TABLE mip_app.lead_outcomes
            ADD CONSTRAINT ck_lead_outcomes_source_record_ref
            CHECK (
                source_record_ref IS NULL
                OR source_record_ref ~ '^auto-[a-f0-9]{32}$'
            ) NOT VALID;
    END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS idx_lead_outcomes_request_id
    ON mip_app.lead_outcomes (request_id)
    WHERE request_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_lead_outcomes_source_record
    ON mip_app.lead_outcomes (source_system, source_record_ref)
    WHERE source_record_ref IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_lead_outcomes_borrower
    ON mip_app.lead_outcomes (borrower_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_lead_outcomes_lo
    ON mip_app.lead_outcomes (assigned_to_email, occurred_at DESC)
    WHERE assigned_to_email IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_lead_outcomes_type
    ON mip_app.lead_outcomes (outcome_type, occurred_at DESC);

-- Action audit --------------------------------------------------------
-- The append-only ledger governance §4 requires. `event_type` is the
-- canonical verb: VIEW_BORROWER, VIEW_LEADS, APPROVE, DRAFT_OUTREACH,
-- RECOMMEND_OFFER, RUN_GENIE. `entity_type` / `entity_id` preserve the
-- existing AuditStore contract (routers don't change). `subject_clip`
-- is a redacted clip_ref (12-char hash), never the raw Cotality CLIP;
-- `subject_segment` is the lowercased segment code (itm, heloc, ...).
-- `metadata` JSONB is for score components / thresholds / evidence_ids
-- -- NO PII (no owner names, no street addresses).
CREATE TABLE IF NOT EXISTS mip_app.action_audit (
    audit_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    audit_sequence  BIGSERIAL NOT NULL,
    event_type      TEXT NOT NULL,
    actor_email     TEXT NOT NULL,
    entity_type     TEXT NOT NULL DEFAULT 'borrower',
    entity_id       TEXT NOT NULL DEFAULT '',
    subject_clip    TEXT,
    subject_segment TEXT,
    request_id      TEXT,
    correlation_id  TEXT,
    evidence_ids    TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    event_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE mip_app.action_audit
    ADD COLUMN IF NOT EXISTS correlation_id TEXT;
ALTER TABLE mip_app.action_audit
    ADD COLUMN IF NOT EXISTS audit_sequence BIGSERIAL;
ALTER TABLE mip_app.action_audit
    ALTER COLUMN audit_sequence SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_action_audit_sequence
    ON mip_app.action_audit (audit_sequence);
CREATE INDEX IF NOT EXISTS idx_action_audit_event_at
    ON mip_app.action_audit (event_at DESC);
CREATE INDEX IF NOT EXISTS idx_action_audit_event_type
    ON mip_app.action_audit (event_type, event_at DESC);
CREATE INDEX IF NOT EXISTS idx_action_audit_actor
    ON mip_app.action_audit (actor_email, event_at DESC);
CREATE INDEX IF NOT EXISTS idx_action_audit_subject_clip
    ON mip_app.action_audit (subject_clip)
    WHERE subject_clip IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_action_audit_correlation
    ON mip_app.action_audit (correlation_id)
    WHERE correlation_id IS NOT NULL;

-- Audit archival run ledger --------------------------------------------
-- The action_audit table remains append-only. Archive jobs copy old rows to
-- governed cold storage, then record the run here instead of deleting from
-- action_audit without a compliance-approved retention change.
CREATE TABLE IF NOT EXISTS mip_app.action_audit_archive_runs (
    archive_run_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cutoff_event_at     TIMESTAMPTZ NOT NULL,
    destination_uri     TEXT NOT NULL,
    row_count           BIGINT NOT NULL DEFAULT 0 CHECK (row_count >= 0),
    requested_by        TEXT NOT NULL DEFAULT 'system@databricks-apps',
    status              TEXT NOT NULL DEFAULT 'completed'
                        CHECK (status IN ('completed','failed')),
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    completed_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_action_audit_archive_runs_completed
    ON mip_app.action_audit_archive_runs (completed_at DESC);
-- Genie action idempotency: the server issues request ids inside the
-- HMAC confirmation token, and Lakebase enforces one audited mutation per
-- actor/request/event. The partial predicate keeps legacy non-Genie audit
-- rows append-only while giving governed Genie actions retry safety.
CREATE UNIQUE INDEX IF NOT EXISTS idx_action_audit_genie_request_actor_event
    ON mip_app.action_audit (actor_email, request_id, event_type)
    WHERE request_id IS NOT NULL AND event_type LIKE 'GENIE_ACTION_%';
CREATE UNIQUE INDEX IF NOT EXISTS idx_action_audit_genie_request_actor_event_v2
    ON mip_app.action_audit (actor_email, request_id, event_type)
    WHERE request_id IS NOT NULL AND left(event_type, 13) = 'GENIE_ACTION_';
-- Admin refresh launches use the caller-supplied request id as both the
-- Lakebase lifecycle key and the Databricks Jobs idempotency token. Keep one
-- requested and one terminal run record per actor/request even when a retry is
-- handled by another app process.
CREATE UNIQUE INDEX IF NOT EXISTS idx_action_audit_admin_request_actor_event
    ON mip_app.action_audit (actor_email, request_id, event_type)
    WHERE request_id IS NOT NULL
      AND event_type IN ('ADMIN_OPERATION_REQUESTED', 'ADMIN_OPERATION_RUN');

-- Append-only enforcement must not rely solely on GRANT shape. The
-- Databricks Apps / migration identity can own or receive broader table
-- privileges on bundle-provisioned Lakebase, so a statement-level trigger
-- blocks UPDATE/DELETE even when Postgres would otherwise authorize them.
-- Statement-level is intentional: UPDATE ... WHERE false and DELETE ...
-- WHERE false still prove mutation privilege and must be rejected.
CREATE OR REPLACE FUNCTION mip_app.prevent_action_audit_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'mip_app.action_audit is append-only; % is not allowed', TG_OP
        USING ERRCODE = '42501';
END;
$$;

DROP TRIGGER IF EXISTS trg_action_audit_append_only ON mip_app.action_audit;
CREATE TRIGGER trg_action_audit_append_only
    BEFORE UPDATE OR DELETE OR TRUNCATE ON mip_app.action_audit
    FOR EACH STATEMENT
    EXECUTE FUNCTION mip_app.prevent_action_audit_mutation();

-- Business outcome rows are immutable proof once inserted. Their writers use
-- one narrow follow-up UPDATE in the same transaction to attach the audit row;
-- the database permits only that NULL -> non-NULL audit_event_id transition.
CREATE OR REPLACE FUNCTION mip_app.enforce_audit_event_finalize_only()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF (to_jsonb(NEW) - 'audit_event_id')
       IS DISTINCT FROM
       (to_jsonb(OLD) - 'audit_event_id')
       OR OLD.audit_event_id IS NOT NULL
       OR NEW.audit_event_id IS NULL THEN
        RAISE EXCEPTION
            '%.% is immutable except for one-time audit_event_id finalization',
            TG_TABLE_SCHEMA, TG_TABLE_NAME
            USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'call_dispositions_audit_event_id_fkey'
          AND conrelid = 'mip_app.call_dispositions'::regclass
    ) THEN
        ALTER TABLE mip_app.call_dispositions
            ADD CONSTRAINT call_dispositions_audit_event_id_fkey
            FOREIGN KEY (audit_event_id) REFERENCES mip_app.action_audit(audit_id)
            NOT VALID;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'lead_outcomes_audit_event_id_fkey'
          AND conrelid = 'mip_app.lead_outcomes'::regclass
    ) THEN
        ALTER TABLE mip_app.lead_outcomes
            ADD CONSTRAINT lead_outcomes_audit_event_id_fkey
            FOREIGN KEY (audit_event_id) REFERENCES mip_app.action_audit(audit_id)
            NOT VALID;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'approvals_audit_event_id_fkey'
          AND conrelid = 'mip_app.approvals'::regclass
    ) THEN
        ALTER TABLE mip_app.approvals
            ADD CONSTRAINT approvals_audit_event_id_fkey
            FOREIGN KEY (audit_event_id) REFERENCES mip_app.action_audit(audit_id);
    END IF;
END $$;

-- Generated outreach proof ----------------------------------------------
-- Each /outreach/draft response is committed with its exact approved-safe
-- response JSON and audit id before it is returned. This remains separate
-- from mutable workspace drafts so generation never overwrites operator work.
CREATE TABLE IF NOT EXISTS mip_app.generated_outreach_drafts (
    generation_id UUID PRIMARY KEY,
    audit_event_id UUID NOT NULL UNIQUE REFERENCES mip_app.action_audit(audit_id),
    actor_email    TEXT NOT NULL,
    borrower_id    TEXT NOT NULL,
    campaign_id    UUID,
    variant_name   TEXT,
    channel        TEXT NOT NULL CHECK (channel IN ('email','sms','direct_mail')),
    offer_code     TEXT NOT NULL,
    generation_mode TEXT NOT NULL CHECK (generation_mode IN ('supervisor','governed_fallback')),
    response_hash  TEXT NOT NULL CHECK (response_hash ~ '^[0-9a-f]{64}$'),
    response_json  JSONB NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE mip_app.outreach_drafts
    DROP CONSTRAINT IF EXISTS outreach_drafts_generation_id_fkey;
ALTER TABLE mip_app.outreach_drafts
    ADD CONSTRAINT outreach_drafts_generation_id_fkey
    FOREIGN KEY (generation_id)
    REFERENCES mip_app.generated_outreach_drafts(generation_id)
    NOT VALID;
ALTER TABLE mip_app.outreach_drafts
    VALIDATE CONSTRAINT outreach_drafts_generation_id_fkey;

-- Existing immutable triggers are transactionally removed only after their
-- table exists. The post-seed finalization block restores them before commit.
DROP TRIGGER IF EXISTS trg_generated_outreach_drafts_immutable
    ON mip_app.generated_outreach_drafts;

-- Existing deployments stored campaign_id as nullable TEXT. Convert without
-- losing evidence: NULL remains NULL, while malformed or orphaned non-NULL
-- values stop the migration before any type change. The exclusive lock closes
-- the validation/ALTER race with a still-running app process.
DO $$
DECLARE
    campaign_id_type REGTYPE;
BEGIN
    SELECT a.atttypid::regtype
    INTO campaign_id_type
    FROM pg_attribute a
    WHERE a.attrelid = 'mip_app.generated_outreach_drafts'::regclass
      AND a.attname = 'campaign_id'
      AND NOT a.attisdropped;

    IF campaign_id_type IS DISTINCT FROM 'uuid'::regtype THEN
        IF campaign_id_type NOT IN ('text'::regtype, 'character varying'::regtype) THEN
            RAISE EXCEPTION
                'Unsupported generated_outreach_drafts.campaign_id type: %',
                campaign_id_type;
        END IF;

        LOCK TABLE mip_app.generated_outreach_drafts IN ACCESS EXCLUSIVE MODE;

        IF EXISTS (
            SELECT 1
            FROM mip_app.generated_outreach_drafts
            WHERE campaign_id IS NOT NULL
              AND campaign_id !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        ) THEN
            RAISE EXCEPTION
                'Cannot migrate generated_outreach_drafts.campaign_id: malformed UUID value';
        END IF;

        IF EXISTS (
            SELECT 1
            FROM mip_app.generated_outreach_drafts d
            LEFT JOIN mip_app.campaigns c
              ON c.campaign_id = d.campaign_id::uuid
            WHERE d.campaign_id IS NOT NULL
              AND c.campaign_id IS NULL
        ) THEN
            RAISE EXCEPTION
                'Cannot migrate generated_outreach_drafts.campaign_id: orphaned campaign reference';
        END IF;

        ALTER TABLE mip_app.generated_outreach_drafts
            ALTER COLUMN campaign_id TYPE UUID USING campaign_id::uuid;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'generated_outreach_drafts_campaign_id_fkey'
          AND conrelid = 'mip_app.generated_outreach_drafts'::regclass
          AND confrelid = 'mip_app.campaigns'::regclass
          AND contype = 'f'
    ) THEN
        ALTER TABLE mip_app.generated_outreach_drafts
            ADD CONSTRAINT generated_outreach_drafts_campaign_id_fkey
            FOREIGN KEY (campaign_id) REFERENCES mip_app.campaigns(campaign_id);
    END IF;
END $$;

-- Remove legacy/simple and previously installed proof constraints inside the
-- migration transaction. ALTER TABLE locks remain held until commit; the
-- post-seed suffix recreates and validates the exact constraint set after all
-- deterministic compatibility updates have completed.
ALTER TABLE mip_app.approvals
    DROP CONSTRAINT IF EXISTS approvals_campaign_id_fkey;
ALTER TABLE mip_app.generated_outreach_drafts
    DROP CONSTRAINT IF EXISTS generated_outreach_drafts_campaign_id_fkey;
ALTER TABLE mip_app.approvals
    DROP CONSTRAINT IF EXISTS approvals_channel_chk;
ALTER TABLE mip_app.approvals
    DROP CONSTRAINT IF EXISTS approvals_channel_required_chk;
ALTER TABLE mip_app.approvals
    DROP CONSTRAINT IF EXISTS approvals_campaign_variant_pair_chk;
ALTER TABLE mip_app.approvals
    DROP CONSTRAINT IF EXISTS approvals_campaign_variant_channel_fkey;
ALTER TABLE mip_app.generated_outreach_drafts
    DROP CONSTRAINT IF EXISTS generated_outreach_campaign_variant_pair_chk;
ALTER TABLE mip_app.generated_outreach_drafts
    DROP CONSTRAINT IF EXISTS generated_outreach_campaign_variant_channel_fkey;

CREATE INDEX IF NOT EXISTS idx_generated_outreach_drafts_actor_created
    ON mip_app.generated_outreach_drafts (actor_email, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_generated_outreach_drafts_borrower_created
    ON mip_app.generated_outreach_drafts (borrower_id, created_at DESC);

-- Generated copy and campaign variants are evidence records, not mutable
-- workspace drafts. The trigger is a second control behind SELECT+INSERT-only
-- runtime grants and still blocks an owner or accidentally elevated role.
CREATE OR REPLACE FUNCTION mip_app.prevent_outreach_evidence_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION '%.% is immutable; % is not allowed',
        TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP
        USING ERRCODE = '42501';
END;
$$;

INSERT INTO mip_app.schema_migrations (version, description)
VALUES (
    '2026_07_13_generated_outreach_draft_proof',
    'Persist exact generated outreach responses and audit linkage before returning copy'
)
ON CONFLICT (version) DO NOTHING;

-- Genie sessions ------------------------------------------------------
-- Durable state for Databricks Genie conversations. These tables store
-- conversation/message identifiers and proof metadata only; they do NOT
-- store raw user prompts or answer text, because those fields can contain
-- free-form PII-like content. The app can recover the latest conversation
-- after reload while the append-only action_audit ledger remains the
-- governed proof of each read/action.
CREATE TABLE IF NOT EXISTS mip_app.genie_sessions (
    actor_email        TEXT NOT NULL,
    conversation_id    TEXT NOT NULL,
    last_message_id    TEXT,
    last_question_hash TEXT,
    source             TEXT,
    trusted_assets     TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (actor_email, conversation_id)
);
CREATE INDEX IF NOT EXISTS idx_genie_sessions_actor_updated
    ON mip_app.genie_sessions (actor_email, updated_at DESC);

CREATE TABLE IF NOT EXISTS mip_app.genie_messages (
    conversation_id    TEXT NOT NULL,
    message_id         TEXT NOT NULL,
    actor_email        TEXT NOT NULL,
    question_hash      TEXT NOT NULL,
    source             TEXT NOT NULL,
    row_count          INTEGER NOT NULL DEFAULT 0 CHECK (row_count >= 0),
    visualization_kind TEXT,
    trusted_assets     TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    request_id         TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (conversation_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_genie_messages_actor_created
    ON mip_app.genie_messages (actor_email, created_at DESC);

-- Genie-governed cohorts ---------------------------------------------
-- One row per confirmed "open this cohort" action. The cohort stores
-- only reviewed filters and optional synthetic borrower ids from the
-- Genie result, never owner names, raw CLIPs, addresses, emails, or
-- phone numbers. Lead Queue replays this Lakebase row by cohort_id so
-- a governed Genie action cannot degrade into the generic lead queue.
CREATE TABLE IF NOT EXISTS mip_app.genie_cohorts (
    cohort_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_email     TEXT NOT NULL,
    request_id      TEXT NOT NULL,
    conversation_id TEXT,
    message_id      TEXT,
    question_hash   TEXT,
    route_filters   JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_assets   TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    sql_hash        TEXT,
    row_count       INTEGER NOT NULL DEFAULT 0 CHECK (row_count >= 0),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (actor_email, request_id)
);
CREATE INDEX IF NOT EXISTS idx_genie_cohorts_actor_created
    ON mip_app.genie_cohorts (actor_email, created_at DESC);

CREATE TABLE IF NOT EXISTS mip_app.genie_cohort_members (
    cohort_id   UUID NOT NULL REFERENCES mip_app.genie_cohorts(cohort_id) ON DELETE CASCADE,
    borrower_id TEXT NOT NULL,
    rank_order  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (cohort_id, borrower_id)
);
CREATE INDEX IF NOT EXISTS idx_genie_cohort_members_borrower
    ON mip_app.genie_cohort_members (borrower_id);

-- Immutability: app writer has INSERT + SELECT only; revoke UPDATE/DELETE.
-- The role may not yet exist at schema-install time; the REVOKE is a no-op
-- when the grantee is absent, but we guard with DO-blocks to avoid
-- "role does not exist" errors on first deploy.
DO $$
BEGIN
    REVOKE UPDATE, DELETE ON mip_app.action_audit FROM PUBLIC;
EXCEPTION WHEN OTHERS THEN NULL;
END $$;

-- Agent sessions ------------------------------------------------------
-- One row per agent orchestrator run (portfolio builder, borrower
-- dossier, offer strategy, outreach writer, supervisor). Used by the
-- activity log in the console right rail.
CREATE TABLE IF NOT EXISTS mip_app.agent_sessions (
    session_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_email  TEXT NOT NULL,
    route        TEXT,
    outcome      TEXT,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_agent_sessions_actor
    ON mip_app.agent_sessions (actor_email, started_at DESC);

-- Mortgage Growth Agent runs -----------------------------------------
-- Durable run ledger for governed growth-agent workflows. Rows contain
-- reviewed workflow ids, public route filters, source assets, and counts
-- only; no raw Genie prompts, raw CLIPs, owner names, addresses, phones, or
-- emails. The append-only action_audit row is still the compliance ledger;
-- audit_event_id links the product-facing run to that immutable proof.
CREATE TABLE IF NOT EXISTS mip_app.growth_agent_runs (
    run_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_email      TEXT NOT NULL,
    request_id       TEXT,
    workflow_id      TEXT NOT NULL
                     CHECK (workflow_id IN (
                       'daily_refi_brief',
                       'borrower_dossier_review',
                       'listing_watch',
                       'competitor_recapture_monitor',
                       'high_equity_heloc_watch',
                       'branch_capacity_review',
                       'source_freshness_sentinel',
                       'custom_segment_watch'
                     )),
    workflow_title   TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'completed'
                     CHECK (status IN ('completed','failed')),
    criteria         JSONB NOT NULL DEFAULT '{}'::jsonb,
    broad_total      INTEGER NOT NULL DEFAULT 0 CHECK (broad_total >= 0),
    actionable_total INTEGER NOT NULL DEFAULT 0 CHECK (actionable_total >= 0),
    broad_avg_score  DOUBLE PRECISION,
    actionable_avg_score DOUBLE PRECISION,
    avg_rate_spread_bps DOUBLE PRECISION,
    avg_equity_pct   DOUBLE PRECISION,
    route            TEXT NOT NULL,
    source_assets    TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    tool_steps       JSONB NOT NULL DEFAULT '[]'::jsonb,
    policy_checks    JSONB NOT NULL DEFAULT '[]'::jsonb,
    trace_id         TEXT,
    tool_result_hash TEXT,
    specialist_agent TEXT,
    agent_evidence   JSONB NOT NULL DEFAULT '{}'::jsonb,
    governance_chips JSONB NOT NULL DEFAULT '[]'::jsonb,
    audit_event_id   UUID REFERENCES mip_app.action_audit(audit_id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
DROP TRIGGER IF EXISTS trg_growth_agent_runs_finalize_only
    ON mip_app.growth_agent_runs;
DROP TRIGGER IF EXISTS trg_growth_agent_runs_no_remove
    ON mip_app.growth_agent_runs;
ALTER TABLE mip_app.growth_agent_runs
    ADD COLUMN IF NOT EXISTS request_id TEXT;
ALTER TABLE mip_app.growth_agent_runs
    ADD COLUMN IF NOT EXISTS broad_avg_score DOUBLE PRECISION;
ALTER TABLE mip_app.growth_agent_runs
    ADD COLUMN IF NOT EXISTS actionable_avg_score DOUBLE PRECISION;
ALTER TABLE mip_app.growth_agent_runs
    ADD COLUMN IF NOT EXISTS avg_rate_spread_bps DOUBLE PRECISION;
ALTER TABLE mip_app.growth_agent_runs
    ADD COLUMN IF NOT EXISTS avg_equity_pct DOUBLE PRECISION;
ALTER TABLE mip_app.growth_agent_runs
    ADD COLUMN IF NOT EXISTS trace_id TEXT;
ALTER TABLE mip_app.growth_agent_runs
    ADD COLUMN IF NOT EXISTS tool_result_hash TEXT;
ALTER TABLE mip_app.growth_agent_runs
    ADD COLUMN IF NOT EXISTS specialist_agent TEXT;
ALTER TABLE mip_app.growth_agent_runs
    ADD COLUMN IF NOT EXISTS agent_evidence JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE mip_app.growth_agent_runs
    ADD COLUMN IF NOT EXISTS governance_chips JSONB NOT NULL DEFAULT '[]'::jsonb;
CREATE INDEX IF NOT EXISTS idx_growth_agent_runs_actor_created
    ON mip_app.growth_agent_runs (actor_email, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_growth_agent_runs_workflow_created
    ON mip_app.growth_agent_runs (workflow_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_growth_agent_runs_actor_request_id
    ON mip_app.growth_agent_runs (actor_email, request_id)
    WHERE request_id IS NOT NULL;

DO $$
BEGIN
    ALTER TABLE mip_app.growth_agent_runs
        DROP CONSTRAINT IF EXISTS growth_agent_runs_workflow_id_check;
    ALTER TABLE mip_app.growth_agent_runs
        DROP CONSTRAINT IF EXISTS ck_growth_agent_runs_workflow_id;
    ALTER TABLE mip_app.growth_agent_runs
        ADD CONSTRAINT ck_growth_agent_runs_workflow_id
        CHECK (workflow_id IN (
          'daily_refi_brief',
          'borrower_dossier_review',
          'listing_watch',
          'competitor_recapture_monitor',
          'high_equity_heloc_watch',
          'branch_capacity_review',
          'source_freshness_sentinel',
          'custom_segment_watch'
        ));
END $$;

-- Mortgage Growth Agent monitors -------------------------------------
-- Saved scheduled-monitor definitions. Scheduling/orchestration can read
-- these rows later, but each saved monitor remains a reviewed filter set,
-- not an outbound activation or auto-send instruction.
CREATE TABLE IF NOT EXISTS mip_app.growth_agent_monitors (
    monitor_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_email      TEXT NOT NULL,
    workflow_id      TEXT NOT NULL
                     CHECK (workflow_id IN (
                       'daily_refi_brief',
                      'borrower_dossier_review',
                      'listing_watch',
                      'competitor_recapture_monitor',
                      'high_equity_heloc_watch',
                      'branch_capacity_review',
                      'source_freshness_sentinel',
                      'custom_segment_watch'
                     )),
    name             TEXT NOT NULL,
    cadence          TEXT NOT NULL CHECK (cadence IN ('daily','weekly')),
    status           TEXT NOT NULL DEFAULT 'active'
                     CHECK (status IN ('active','paused','disabled')),
    criteria         JSONB NOT NULL DEFAULT '{}'::jsonb,
    route            TEXT NOT NULL,
    actionable_total INTEGER NOT NULL DEFAULT 0 CHECK (actionable_total >= 0),
    source_assets    TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    last_run_id      UUID REFERENCES mip_app.growth_agent_runs(run_id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (actor_email, workflow_id, name)
);
CREATE INDEX IF NOT EXISTS idx_growth_agent_monitors_actor_updated
    ON mip_app.growth_agent_monitors (actor_email, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_growth_agent_monitors_actor_due
    ON mip_app.growth_agent_monitors (actor_email, status, updated_at ASC);

DO $$
BEGIN
    ALTER TABLE mip_app.growth_agent_monitors
        DROP CONSTRAINT IF EXISTS growth_agent_monitors_workflow_id_check;
    ALTER TABLE mip_app.growth_agent_monitors
        DROP CONSTRAINT IF EXISTS ck_growth_agent_monitors_workflow_id;
    ALTER TABLE mip_app.growth_agent_monitors
        ADD CONSTRAINT ck_growth_agent_monitors_workflow_id
        CHECK (workflow_id IN (
          'daily_refi_brief',
          'borrower_dossier_review',
          'listing_watch',
          'competitor_recapture_monitor',
          'high_equity_heloc_watch',
          'branch_capacity_review',
          'source_freshness_sentinel',
          'custom_segment_watch'
        ));
END $$;

CREATE TABLE IF NOT EXISTS mip_app.growth_agent_notification_drafts (
    draft_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_email    TEXT NOT NULL,
    monitor_id     UUID NOT NULL REFERENCES mip_app.growth_agent_monitors(monitor_id) ON DELETE CASCADE,
    run_id         UUID NOT NULL REFERENCES mip_app.growth_agent_runs(run_id) ON DELETE CASCADE,
    channel        TEXT NOT NULL CHECK (channel IN ('slack','teams')),
    title          TEXT NOT NULL CHECK (length(title) BETWEEN 5 AND 120),
    body           TEXT NOT NULL CHECK (length(body) BETWEEN 20 AND 2000),
    status         TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','reviewed','cancelled')),
    request_id     TEXT,
    intent_payload TEXT,
    intent_hash    TEXT,
    audit_event_id UUID REFERENCES mip_app.action_audit(audit_id),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE mip_app.growth_agent_notification_drafts
    ADD COLUMN IF NOT EXISTS generation_mode TEXT NOT NULL DEFAULT 'governed_fallback'
    CHECK (generation_mode IN ('supervisor','governed_fallback'));
ALTER TABLE mip_app.growth_agent_notification_drafts
    ADD COLUMN IF NOT EXISTS generator_label TEXT NOT NULL DEFAULT 'Governed notification framework';
ALTER TABLE mip_app.growth_agent_notification_drafts
    ADD COLUMN IF NOT EXISTS strategy_summary TEXT NOT NULL DEFAULT 'Reviewed internal notification framing.';
ALTER TABLE mip_app.growth_agent_notification_drafts
    ADD COLUMN IF NOT EXISTS intent_payload TEXT;
ALTER TABLE mip_app.growth_agent_notification_drafts
    ADD COLUMN IF NOT EXISTS intent_hash TEXT;
ALTER TABLE mip_app.growth_agent_notification_drafts
    ADD COLUMN IF NOT EXISTS audit_event_id UUID;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'growth_agent_notification_drafts_audit_event_id_fkey'
          AND conrelid = 'mip_app.growth_agent_notification_drafts'::regclass
    ) THEN
        ALTER TABLE mip_app.growth_agent_notification_drafts
            ADD CONSTRAINT growth_agent_notification_drafts_audit_event_id_fkey
            FOREIGN KEY (audit_event_id) REFERENCES mip_app.action_audit(audit_id);
    END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS idx_growth_agent_notification_drafts_request
    ON mip_app.growth_agent_notification_drafts (request_id)
    WHERE request_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_growth_agent_notification_drafts_active
    ON mip_app.growth_agent_notification_drafts (actor_email, monitor_id, run_id, channel)
    WHERE status = 'draft';
CREATE INDEX IF NOT EXISTS idx_growth_agent_notification_drafts_actor
    ON mip_app.growth_agent_notification_drafts (actor_email, created_at DESC);

INSERT INTO mip_app.schema_migrations (version, description)
VALUES (
    '2026_06_26_growth_agent_borrower_dossier_review',
    'Allow borrower dossier review Growth Agent runs and monitors'
)
ON CONFLICT (version) DO NOTHING;

INSERT INTO mip_app.schema_migrations (version, description)
VALUES (
    '2026_06_26_growth_agent_custom_segment_watch',
    'Allow governed custom segment Growth Agent runs and monitors'
)
ON CONFLICT (version) DO NOTHING;

INSERT INTO mip_app.schema_migrations (version, description)
VALUES (
    '2026_06_26_agentic_growth_trace',
    'Add specialist workflow ids and trace/hash governance fields to Growth Agent runs'
)
ON CONFLICT (version) DO NOTHING;

-- AI Gateway exact-row proof ledger -----------------------------------
-- Written only by deploy/nightly verifier tooling. Runtime capability
-- probes read this table to decide whether AI Gateway can be claimed for
-- the current deployment SHA; public/user-facing routes must not write it.
CREATE TABLE IF NOT EXISTS mip_app.ai_gateway_proof_ledger (
    proof_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    git_sha             TEXT NOT NULL CHECK (git_sha ~ '^[0-9a-f]{40}$'),
    client_request_id   TEXT NOT NULL UNIQUE CHECK (client_request_id ~ '^mip-capability-[0-9a-f]{40}-[0-9a-f]{16}$'),
    endpoint_name       TEXT NOT NULL CHECK (length(endpoint_name) BETWEEN 3 AND 255),
    inference_table     TEXT NOT NULL CHECK (inference_table ~ '^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$'),
    sent_at             TIMESTAMPTZ NOT NULL,
    verified_at         TIMESTAMPTZ,
    verify_latency_s    DOUBLE PRECISION CHECK (verify_latency_s IS NULL OR verify_latency_s >= 0),
    status              TEXT NOT NULL CHECK (status IN ('pending','verified','failed','expired')),
    attestation_alg     TEXT,
    attestation_key_id  TEXT,
    attestation_signature TEXT,
    CONSTRAINT ck_ai_gateway_proof_verified_fields
      CHECK (
        (status = 'verified' AND verified_at IS NOT NULL AND verify_latency_s IS NOT NULL)
        OR
        (status <> 'verified')
      )
);
ALTER TABLE mip_app.ai_gateway_proof_ledger
    ADD COLUMN IF NOT EXISTS attestation_alg TEXT,
    ADD COLUMN IF NOT EXISTS attestation_key_id TEXT,
    ADD COLUMN IF NOT EXISTS attestation_signature TEXT;

-- Pre-attestation verified rows are intentionally retired. Their exact-row
-- evidence may have been valid, but a Lakebase writer alone could manufacture
-- the same shape. Only newly signed verifier evidence is claimable.
UPDATE mip_app.ai_gateway_proof_ledger
SET status = 'expired',
    verified_at = NULL,
    verify_latency_s = NULL
WHERE status = 'verified'
  AND attestation_signature IS NULL;

ALTER TABLE mip_app.ai_gateway_proof_ledger
    DROP CONSTRAINT IF EXISTS ck_ai_gateway_proof_attestation;
ALTER TABLE mip_app.ai_gateway_proof_ledger
    ADD CONSTRAINT ck_ai_gateway_proof_attestation
    CHECK (
      (
        status = 'verified'
        AND attestation_alg = 'ed25519-v1'
        AND attestation_key_id ~ '^[0-9a-f]{16}$'
        AND attestation_signature ~ '^[A-Za-z0-9_-]{86}$'
      )
      OR
      (
        status <> 'verified'
        AND attestation_alg IS NULL
        AND attestation_key_id IS NULL
        AND attestation_signature IS NULL
      )
    );
CREATE INDEX IF NOT EXISTS idx_ai_gateway_proof_sha_status
    ON mip_app.ai_gateway_proof_ledger (git_sha, status, verified_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_gateway_proof_pending
    ON mip_app.ai_gateway_proof_ledger (status, sent_at)
    WHERE status = 'pending';

-- Proof writers may run on a host whose clock differs slightly from Lakebase.
-- Accept at most five minutes of positive skew; larger future timestamps or
-- reversed send/verify chronology would let evidence outlive its real window.
CREATE OR REPLACE FUNCTION mip_app.enforce_ai_gateway_proof_timestamp_bounds()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    observed_now TIMESTAMPTZ := clock_timestamp();
    clock_tolerance CONSTANT INTERVAL := INTERVAL '5 minutes';
BEGIN
    IF TG_OP = 'INSERT' AND NEW.status = 'verified' THEN
        RAISE EXCEPTION
            'AI Gateway proof must be inserted as pending before verification'
            USING ERRCODE = '42501';
    END IF;
    IF TG_OP = 'UPDATE'
       AND NEW.status = 'verified'
       AND OLD.status <> 'pending' THEN
        RAISE EXCEPTION
            'AI Gateway proof can only transition from pending to verified'
            USING ERRCODE = '42501';
    END IF;
    IF NEW.status IN ('pending', 'verified')
       AND NEW.sent_at > observed_now + clock_tolerance THEN
        RAISE EXCEPTION
            'AI Gateway proof sent_at exceeds the five-minute clock tolerance'
            USING ERRCODE = '22007';
    END IF;
    IF NEW.status IN ('pending', 'verified')
       AND NEW.verified_at IS NOT NULL
       AND NEW.verified_at > observed_now + clock_tolerance THEN
        RAISE EXCEPTION
            'AI Gateway proof verified_at exceeds the five-minute clock tolerance'
            USING ERRCODE = '22007';
    END IF;
    IF NEW.status IN ('pending', 'verified')
       AND NEW.verified_at IS NOT NULL
       AND NEW.verified_at < NEW.sent_at - clock_tolerance THEN
        RAISE EXCEPTION
            'AI Gateway proof verified_at precedes sent_at beyond clock tolerance'
            USING ERRCODE = '22007';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_ai_gateway_proof_timestamp_bounds
    ON mip_app.ai_gateway_proof_ledger;
CREATE TRIGGER trg_ai_gateway_proof_timestamp_bounds
    BEFORE INSERT OR UPDATE ON mip_app.ai_gateway_proof_ledger
    FOR EACH ROW
    EXECUTE FUNCTION mip_app.enforce_ai_gateway_proof_timestamp_bounds();

INSERT INTO mip_app.schema_migrations (version, description)
VALUES (
    '2026_07_02_ai_gateway_exact_proof_ledger',
    'Add AI Gateway exact inference-row proof ledger for strict capability claims'
)
ON CONFLICT (version) DO NOTHING;

INSERT INTO mip_app.schema_migrations (version, description)
VALUES (
    '2026_07_15_ai_gateway_proof_timestamp_bounds',
    'Reject AI Gateway proof timestamps beyond a five-minute positive clock tolerance'
)
ON CONFLICT (version) DO NOTHING;

INSERT INTO mip_app.schema_migrations (version, description)
VALUES (
    '2026_07_15_ai_gateway_proof_ed25519_attestation',
    'Require independently signed verifier evidence for claimable AI Gateway proofs'
)
ON CONFLICT (version) DO NOTHING;

-- Feedback ------------------------------------------------------------
-- Thumbs-up / thumbs-down + free-text from the in-app feedback control.
CREATE TABLE IF NOT EXISTS mip_app.feedback (
    feedback_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    borrower_id   TEXT,
    event_type    TEXT NOT NULL,
    rating        SMALLINT CHECK (rating BETWEEN 1 AND 5),
    comment       TEXT,
    actor_email   TEXT NOT NULL,
    recorded_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_feedback_event_type
    ON mip_app.feedback (event_type, recorded_at DESC);

INSERT INTO mip_app.schema_migrations (version, description)
VALUES (
    '2026_05_18_dr_backup_contract',
    'Lakebase DR backup contract: schema_migrations and audit archive run ledger'
)
ON CONFLICT (version) DO NOTHING;

-- ---------------------------------------------------------------------
-- 2026-06-11 audit P1-5: narrative seed used five known 5-digit borrower IDs
-- that violate the B-[0-9A-Z]{13} contract. Preserve those approval rows and
-- map only the exact stable approval-id/legacy-id pairs to the reviewed gold
-- borrower ids. Any other malformed historical row makes validation fail and
-- rolls back the whole deployment; recurring schema apply never deletes it.
-- ---------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'approvals_borrower_id_format_chk'
          AND conrelid = 'mip_app.approvals'::regclass
    ) THEN
        -- NOT VALID enforces the format on new writes while the deterministic
        -- compatibility update below repairs the five reviewed seed rows.
        ALTER TABLE mip_app.approvals
            ADD CONSTRAINT approvals_borrower_id_format_chk
            CHECK (borrower_id ~ '^B-[0-9A-Z]{13}$') NOT VALID;
    END IF;
END $$;

WITH legacy_seed_approval_map (approval_id, legacy_borrower_id, borrower_id) AS (
    VALUES
        ('44444444-4444-4444-8444-444444444441'::uuid, 'B-48291', 'B-0CPWBTJMAPFY2'),
        ('44444444-4444-4444-8444-444444444442'::uuid, 'B-48294', 'B-1IB0UGBTFYM20'),
        ('44444444-4444-4444-8444-444444444443'::uuid, 'B-48295', 'B-102FL7THC6Q3L'),
        ('44444444-4444-4444-8444-444444444444'::uuid, 'B-48292', 'B-1BCZXFQYCX715'),
        ('44444444-4444-4444-8444-444444444445'::uuid, 'B-48293', 'B-1VU4FO4XBQPC4')
)
UPDATE mip_app.approvals AS approval
SET borrower_id = mapping.borrower_id
FROM legacy_seed_approval_map AS mapping
WHERE approval.approval_id = mapping.approval_id
  AND approval.borrower_id = mapping.legacy_borrower_id;

ALTER TABLE mip_app.approvals
    VALIDATE CONSTRAINT approvals_borrower_id_format_chk;

INSERT INTO mip_app.schema_migrations (version, description)
VALUES (
    '2026_06_11_narrative_seed_real_ids',
    'Audit P1-5: deterministically map five legacy seed borrower ids and validate borrower_id ~ ^B-[0-9A-Z]{13}$ without deleting approvals'
)
ON CONFLICT (version) DO NOTHING;

-- ---------------------------------------------------------------------
-- Re-audit #3 P3 (2026-06-12) originally purged pre-demo approvals here.
-- That behavior is intentionally retired: age and fixed identifiers cannot
-- distinguish test data from a legitimate customer decision. Operational
-- cleanup must be an explicit, separately authorized retention workflow.
-- ---------------------------------------------------------------------

INSERT INTO mip_app.schema_migrations (version, description)
VALUES (
    '2026_06_12_purge_dev_session_approvals',
    'Retired unsafe age-based approval/outbox purge; recurring schema apply preserves all workflow and proof rows'
)
ON CONFLICT (version) DO NOTHING;

INSERT INTO mip_app.schema_migrations (version, description)
VALUES (
    '2026_06_30_growth_agent_monitor_drafts',
    'Persist Growth Agent Slack/Teams review drafts for scheduled monitor runs; draft-only, no connector send path'
)
ON CONFLICT (version) DO NOTHING;

INSERT INTO mip_app.schema_migrations (version, description)
VALUES (
    '2026_07_09_campaign_household_dedup',
    'S1.5: persist default-off household dedup config and evidence-cited suppression summary on campaigns'
)
ON CONFLICT (version) DO NOTHING;

-- Loan officers (S2) ---------------------------------------------------
-- First-class loan-officer entity for assignment routing. The middle
-- path: officers stay joined to the existing mip_app.sales_team roster
-- by email (identity, role, manager scope), while this table owns the
-- coverage footprint the assignment surfaces need. Coverage is stored
-- as plain arrays -- two-letter state codes and 5-digit county FIPS --
-- deliberately NO geometry columns; geo drill-down joins these codes
-- against the existing gold geography rollups (S9).
CREATE TABLE IF NOT EXISTS mip_app.loan_officers (
    loan_officer_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email             TEXT NOT NULL UNIQUE REFERENCES mip_app.sales_team(email),
    display_name      TEXT NOT NULL,
    coverage_states   TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    coverage_counties TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    active            BOOLEAN NOT NULL DEFAULT true,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_loan_officers_active
    ON mip_app.loan_officers (active, display_name);

-- Assignment lifecycle (S2). Existing mip_app.lead_assignments rows are
-- point-in-time distribution records; S2 adds the reviewed lifecycle the
-- approval funnel (S6), geo assigned-vs-unattended (S9), campaigns (S10)
-- and handoffs (S12) consume:
--   assigned -> contact_drafted -> approved -> actioned -> outcome_recorded
-- The repo's enum idiom is TEXT + named CHECK constraint (see
-- call_dispositions.outcome, approvals.action); a Postgres ENUM type
-- cannot be extended idempotently inside a re-runnable script.
ALTER TABLE mip_app.lead_assignments
    ADD COLUMN IF NOT EXISTS loan_officer_id UUID REFERENCES mip_app.loan_officers(loan_officer_id);
ALTER TABLE mip_app.lead_assignments
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'assigned';
ALTER TABLE mip_app.lead_assignments
    ADD COLUMN IF NOT EXISTS status_updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_lead_assignments_status'
          AND conrelid = 'mip_app.lead_assignments'::regclass
    ) THEN
        ALTER TABLE mip_app.lead_assignments
            ADD CONSTRAINT ck_lead_assignments_status
            CHECK (status IN ('assigned','contact_drafted','approved','actioned','outcome_recorded'));
    END IF;
END $$;
-- Masked-borrower-id format guard, same NOT VALID + tolerant VALIDATE
-- pattern as approvals_borrower_id_format_chk: new writes are enforced
-- immediately; one unexpected legacy row cannot fail the migrate job.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'lead_assignments_borrower_id_format_chk'
          AND conrelid = 'mip_app.lead_assignments'::regclass
    ) THEN
        ALTER TABLE mip_app.lead_assignments
            ADD CONSTRAINT lead_assignments_borrower_id_format_chk
            CHECK (borrower_id ~ '^B-[0-9A-Z]{13}$') NOT VALID;
    END IF;
END $$;
DO $$
BEGIN
    ALTER TABLE mip_app.lead_assignments
        VALIDATE CONSTRAINT lead_assignments_borrower_id_format_chk;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'lead_assignments_borrower_id_format_chk left NOT VALID (new writes still enforced): %', SQLERRM;
END $$;
CREATE INDEX IF NOT EXISTS idx_lead_assignments_lo_status
    ON mip_app.lead_assignments (loan_officer_id, status)
    WHERE released_at IS NULL;

INSERT INTO mip_app.schema_migrations (version, description)
VALUES (
    '2026_07_10_s2_loan_officer_entity',
    'S2: loan_officers entity with array coverage (no geometry) + assigned->contact_drafted->approved->actioned->outcome_recorded lifecycle on lead_assignments'
)
ON CONFLICT (version) DO NOTHING;

-- =====================================================================
-- S6 APPENDIX -- assignment outcome recording on the feedback table.
-- (Appended at end of file on purpose: S2/S3 slices also append here;
-- keeping S6 DDL in one clearly-bounded block minimises merge friction.)
--
-- The approval funnel's terminal stage (outcome_recorded) captures what
-- happened after an actioned assignment: success | no_response | declined.
-- Outcomes reuse the EXISTING mip_app.feedback row shape (event_type +
-- borrower_id + actor_email); the outcome value is encoded in event_type
-- ('assignment_outcome_success' / 'assignment_outcome_no_response' /
-- 'assignment_outcome_declined') so per-outcome counts stay a GROUP BY on
-- the already-indexed event_type column. Three additive nullable columns
-- join the row back to governance objects; legacy feedback writers keep
-- inserting without them:
--   * assignment_id   -- the lifecycle row this outcome closed.
--   * request_id      -- retry idempotency key (same partial-unique idiom
--                        as approvals.request_id / call_dispositions).
--   * audit_event_id  -- the LEAD_OUTCOME_RECORDED action_audit row written
--                        in the SAME transaction as the status change.
-- No CHECK on feedback.event_type: the table stays a generic feedback
-- ledger; the outcome vocabulary is enforced by the API schema and by the
-- partial-unique index predicate below (one recorded outcome per
-- assignment).
-- =====================================================================
ALTER TABLE mip_app.feedback
    ADD COLUMN IF NOT EXISTS assignment_id UUID;
ALTER TABLE mip_app.feedback
    ADD COLUMN IF NOT EXISTS request_id TEXT;
ALTER TABLE mip_app.feedback
    ADD COLUMN IF NOT EXISTS audit_event_id UUID;
CREATE UNIQUE INDEX IF NOT EXISTS idx_feedback_request_id
    ON mip_app.feedback (request_id)
    WHERE request_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_feedback_assignment_outcome
    ON mip_app.feedback (assignment_id)
    WHERE assignment_id IS NOT NULL
      AND event_type LIKE 'assignment_outcome_%';
CREATE INDEX IF NOT EXISTS idx_feedback_assignment
    ON mip_app.feedback (assignment_id, recorded_at DESC)
    WHERE assignment_id IS NOT NULL;

INSERT INTO mip_app.schema_migrations (version, description)
VALUES (
    '2026_07_11_s6_assignment_outcome_feedback',
    'S6: record assignment outcomes (success/no_response/declined) on mip_app.feedback -- additive assignment_id/request_id/audit_event_id columns, retry-safe request index, one outcome per assignment'
)
ON CONFLICT (version) DO NOTHING;

-- KPI snapshots ---------------------------------------------------------
-- S3: one row per day capturing the S1 headline aggregates measured over
-- mip.semantics.portfolio_headline_metric_view (the named semantic home for
-- every demoed headline KPI). Written by the mip_kpi_snapshot bundle job
-- (jobs/kpi_snapshot.py) with a per-day upsert; the deploy script runs the
-- job once post-gold-refresh so a fresh install never has an empty table.
-- S4 ("since your last login" deltas) queries "the snapshot nearest a given
-- past timestamp" -- the snapshot_at index makes both sides of that lookup
-- (latest at-or-before / earliest after) an index-ordered LIMIT 1.
-- Aggregates only: no borrower ids, owner names, or Cotality identifiers
-- belong in this table.
CREATE TABLE IF NOT EXISTS mip_app.kpi_snapshots (
    snapshot_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_date          DATE NOT NULL,
    snapshot_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_view            TEXT NOT NULL DEFAULT 'portfolio_headline_metric_view',
    marketable_population  BIGINT NOT NULL CHECK (marketable_population >= 0),
    refi_economics_screen  BIGINT NOT NULL CHECK (refi_economics_screen >= 0),
    high_opportunity       BIGINT NOT NULL CHECK (high_opportunity >= 0),
    offers_available       BIGINT NOT NULL CHECK (offers_available >= 0),
    offers_recommended     BIGINT NOT NULL CHECK (offers_recommended >= 0),
    avg_opportunity_score  DOUBLE PRECISION CHECK (
        avg_opportunity_score IS NULL
        OR (avg_opportunity_score >= 0 AND avg_opportunity_score <= 100)
    ),
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Per-day idempotency: jobs upsert via ON CONFLICT (snapshot_date), so a
-- re-run (or a deploy backfill on a day the scheduled job already ran)
-- updates the day's row instead of duplicating it.
CREATE UNIQUE INDEX IF NOT EXISTS idx_kpi_snapshots_snapshot_date
    ON mip_app.kpi_snapshots (snapshot_date);
CREATE INDEX IF NOT EXISTS idx_kpi_snapshots_snapshot_at
    ON mip_app.kpi_snapshots (snapshot_at DESC);
COMMENT ON TABLE mip_app.kpi_snapshots IS
    'Daily aggregates of mip.semantics.portfolio_headline_metric_view headline KPIs; upserted per day by the mip_kpi_snapshot job. No borrower-level data.';

-- User visits -----------------------------------------------------------
-- S3: authenticated app visits recorded by the backend visit-tracking
-- middleware (backend/services/visit_tracking.py). One row per actor per
-- dedupe window (default 15 minutes), never one row per request. Stores
-- ONLY the existing actor identity model (workspace email forwarded by the
-- Databricks Apps edge) + a timestamp -- no routes, IPs, user agents, or
-- any borrower-linked data.
CREATE TABLE IF NOT EXISTS mip_app.user_visits (
    actor_email TEXT NOT NULL,
    visited_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (actor_email, visited_at)
);
CREATE INDEX IF NOT EXISTS idx_user_visits_actor_visited
    ON mip_app.user_visits (actor_email, visited_at DESC);
COMMENT ON TABLE mip_app.user_visits IS
    'Throttled authenticated-visit ledger (actor email + timestamp only) backing "since your last login" KPI deltas.';

INSERT INTO mip_app.schema_migrations (version, description)
VALUES (
    '2026_07_10_s3_kpi_snapshots_user_visits',
    'S3: daily headline-KPI snapshot table (per-day upsert target for mip_kpi_snapshot job) and throttled user_visits ledger for last-login deltas'
)
ON CONFLICT (version) DO NOTHING;

-- Native Genie feedback delivery --------------------------------------
-- Durable intent precedes the upstream rating side effect. Client request
-- ids make retries collapse onto one row; a short IN_FLIGHT lease permits
-- recovery after a process dies with an uncertain upstream result. Replays
-- only set the same native rating. Optional comment text is never persisted.
CREATE TABLE IF NOT EXISTS mip_app.genie_feedback_requests (
    feedback_request_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_email           TEXT NOT NULL,
    request_id            TEXT NOT NULL,
    conversation_id       TEXT NOT NULL,
    message_id            TEXT NOT NULL,
    rating                TEXT NOT NULL CHECK (rating IN ('POSITIVE', 'NEGATIVE')),
    comment_present       BOOLEAN NOT NULL DEFAULT false,
    status                TEXT NOT NULL DEFAULT 'PENDING'
                          CHECK (status IN (
                              'PENDING', 'IN_FLIGHT', 'SUCCEEDED', 'RETRYABLE_FAILED'
                          )),
    attempt_count         INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    intent_audit_event_id UUID REFERENCES mip_app.action_audit(audit_id),
    audit_event_id        UUID REFERENCES mip_app.action_audit(audit_id),
    last_error_code       TEXT CHECK (
                              last_error_code IS NULL
                              OR last_error_code ~ '^[a-z0-9_]{1,64}$'
                          ),
    last_attempt_at       TIMESTAMPTZ,
    succeeded_at          TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_genie_feedback_actor_request UNIQUE (actor_email, request_id),
    CONSTRAINT fk_genie_feedback_message FOREIGN KEY (conversation_id, message_id)
        REFERENCES mip_app.genie_messages(conversation_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_genie_feedback_message
    ON mip_app.genie_feedback_requests (
        actor_email, conversation_id, message_id, created_at DESC
    );
CREATE INDEX IF NOT EXISTS idx_genie_feedback_retryable
    ON mip_app.genie_feedback_requests (status, updated_at)
    WHERE status IN ('PENDING', 'IN_FLIGHT', 'RETRYABLE_FAILED');
COMMENT ON TABLE mip_app.genie_feedback_requests IS
    'PII-minimized native Genie feedback intent and delivery state; optional comment text is never stored.';

INSERT INTO mip_app.schema_migrations (version, description)
VALUES (
    '2026_07_13_native_genie_feedback',
    'Durable actor/message-owned native Genie feedback intents with replay-safe request ids and audited delivery state'
)
ON CONFLICT (version) DO NOTHING;

-- MIP_LAKEBASE_POST_SEED_BEGIN

-- Historical reviewed seed rows remain readable evidence, but every new
-- variant must carry structurally complete server-owned generation proof.
-- NOT VALID preserves those legacy operator rows while still enforcing this
-- check for every INSERT/UPDATE after the migration commits.
ALTER TABLE mip_app.campaign_message_variants
    ADD CONSTRAINT campaign_message_variants_server_owned_proof_chk
    CHECK (
        generation_mode IN ('supervisor', 'reviewed_fallback')
        AND length(btrim(generator_label)) BETWEEN 1 AND 80
        AND provenance_key_id IS NOT NULL
        AND provenance_key_id ~ '^[A-Za-z0-9._-]{1,64}$'
        AND provenance_issued_at IS NOT NULL
        AND provenance_expires_at IS NOT NULL
        AND provenance_expires_at > provenance_issued_at
        AND provenance_copy_hash IS NOT NULL
        AND provenance_copy_hash ~ '^[0-9a-f]{64}$'
        AND provenance_criteria_fingerprint IS NOT NULL
        AND provenance_criteria_fingerprint ~ '^[0-9a-f]{64}$'
        AND (
            provenance_performance_fingerprint IS NULL
            OR provenance_performance_fingerprint ~ '^[0-9a-f]{64}$'
        )
        AND provenance_token_digest IS NOT NULL
        AND provenance_token_digest ~ '^[0-9a-f]{64}$'
    ) NOT VALID;
ALTER TABLE mip_app.campaign_message_variants
    ALTER COLUMN generation_mode DROP DEFAULT,
    ALTER COLUMN generator_label DROP DEFAULT;

INSERT INTO mip_app.schema_migrations (version, description)
VALUES (
    '2026_07_18_campaign_variant_server_owned_proof',
    'Reject new operator-authored campaign variants and require complete server-owned proof'
)
ON CONFLICT (version) DO NOTHING;
-- jobs/lakebase_migrate.py executes the deterministic seed immediately before
-- this suffix, in the same transaction. That ordering makes reviewed campaign
-- variants available for legacy proof backfills before hard validation.

ALTER TABLE mip_app.campaigns
    ADD CONSTRAINT campaigns_criteria_reviewed_shape_chk
    CHECK (
        json_contract_version = 0
        OR mip_app.campaign_criteria_is_reviewed(criteria) IS TRUE
    )
    NOT VALID;
ALTER TABLE mip_app.campaigns
    ADD CONSTRAINT campaigns_suppression_policy_reviewed_shape_chk
    CHECK (
        json_contract_version = 0
        OR mip_app.campaign_suppression_policy_is_reviewed(suppression_policy) IS TRUE
    )
    NOT VALID;
ALTER TABLE mip_app.campaigns
    ADD CONSTRAINT campaigns_channel_cascade_reviewed_shape_chk
    CHECK (
        json_contract_version = 0
        OR mip_app.campaign_channel_cascade_is_reviewed(channel_cascade) IS TRUE
    )
    NOT VALID;
ALTER TABLE mip_app.campaigns
    ADD CONSTRAINT campaigns_send_window_reviewed_shape_chk
    CHECK (
        json_contract_version = 0
        OR mip_app.campaign_send_window_is_reviewed(send_window) IS TRUE
    )
    NOT VALID;
ALTER TABLE mip_app.campaigns
    ADD CONSTRAINT campaigns_holdout_reviewed_shape_chk
    CHECK (
        json_contract_version = 0
        OR holdout IS NULL
        OR mip_app.campaign_holdout_is_reviewed(holdout) IS TRUE
    )
    NOT VALID;
ALTER TABLE mip_app.campaigns
    ADD CONSTRAINT campaigns_roi_assumptions_reviewed_shape_chk
    CHECK (
        json_contract_version = 0
        OR roi_assumptions IS NULL
        OR mip_app.campaign_roi_assumptions_is_reviewed(roi_assumptions) IS TRUE
    )
    NOT VALID;

INSERT INTO mip_app.schema_migrations (version, description)
VALUES (
    '2026_07_15_campaign_json_reviewed_shapes',
    'Enforce exact reviewed shapes for new and changed campaign JSON fields'
)
ON CONFLICT (version) DO NOTHING;

INSERT INTO mip_app.schema_migrations (version, description)
VALUES (
    '2026_07_15_campaign_json_contract_version',
    'Allow status-only updates on untouched legacy campaign JSON while enforcing reviewed version 1 on inserts and payload changes'
)
ON CONFLICT (version) DO NOTHING;

-- Upgrade historical campaign evidence only when the missing value has one
-- possible immutable variant. Ambiguous or orphaned history remains unchanged
-- and fails the explicit proof check below before the deployment can commit.
WITH unique_campaign_variant AS (
    SELECT campaign_id, MIN(variant_name) AS variant_name, MIN(channel) AS channel
    FROM mip_app.campaign_message_variants
    GROUP BY campaign_id
    HAVING COUNT(*) = 1
)
UPDATE mip_app.approvals AS approval
SET variant_name = variant.variant_name,
    channel = variant.channel
FROM unique_campaign_variant AS variant
WHERE approval.campaign_id = variant.campaign_id
  AND approval.variant_name IS NULL
  AND approval.channel IS NULL;

WITH unique_named_variant AS (
    SELECT campaign_id, variant_name, MIN(channel) AS channel
    FROM mip_app.campaign_message_variants
    GROUP BY campaign_id, variant_name
    HAVING COUNT(*) = 1
)
UPDATE mip_app.approvals AS approval
SET channel = variant.channel
FROM unique_named_variant AS variant
WHERE approval.campaign_id = variant.campaign_id
  AND approval.variant_name = variant.variant_name
  AND approval.channel IS NULL;

WITH unique_channel_variant AS (
    SELECT campaign_id, channel, MIN(variant_name) AS variant_name
    FROM mip_app.campaign_message_variants
    GROUP BY campaign_id, channel
    HAVING COUNT(*) = 1
)
UPDATE mip_app.approvals AS approval
SET variant_name = variant.variant_name
FROM unique_channel_variant AS variant
WHERE approval.campaign_id = variant.campaign_id
  AND approval.channel = variant.channel
  AND approval.variant_name IS NULL;

WITH unique_channel_variant AS (
    SELECT campaign_id, channel, MIN(variant_name) AS variant_name
    FROM mip_app.campaign_message_variants
    GROUP BY campaign_id, channel
    HAVING COUNT(*) = 1
)
UPDATE mip_app.generated_outreach_drafts AS draft
SET variant_name = variant.variant_name
FROM unique_channel_variant AS variant
WHERE draft.campaign_id = variant.campaign_id
  AND draft.channel = variant.channel
  AND draft.variant_name IS NULL;

ALTER TABLE mip_app.approvals
    ADD CONSTRAINT approvals_channel_chk
    CHECK (channel IN ('email','sms','direct_mail')) NOT VALID;
ALTER TABLE mip_app.approvals
    ADD CONSTRAINT approvals_channel_required_chk
    CHECK (campaign_id IS NULL OR channel IS NOT NULL) NOT VALID;
ALTER TABLE mip_app.approvals
    ADD CONSTRAINT approvals_campaign_variant_pair_chk
    CHECK ((campaign_id IS NULL) = (variant_name IS NULL)) NOT VALID;
ALTER TABLE mip_app.generated_outreach_drafts
    ADD CONSTRAINT generated_outreach_campaign_variant_pair_chk
    CHECK ((campaign_id IS NULL) = (variant_name IS NULL)) NOT VALID;
ALTER TABLE mip_app.approvals
    ADD CONSTRAINT approvals_campaign_variant_channel_fkey
    FOREIGN KEY (campaign_id, variant_name, channel)
    REFERENCES mip_app.campaign_message_variants(campaign_id, variant_name, channel)
    NOT VALID;
ALTER TABLE mip_app.generated_outreach_drafts
    ADD CONSTRAINT generated_outreach_campaign_variant_channel_fkey
    FOREIGN KEY (campaign_id, variant_name, channel)
    REFERENCES mip_app.campaign_message_variants(campaign_id, variant_name, channel)
    NOT VALID;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM mip_app.approvals AS approval
        WHERE (approval.campaign_id IS NULL) <> (approval.variant_name IS NULL)
           OR (approval.campaign_id IS NOT NULL AND approval.channel IS NULL)
           OR (
               approval.campaign_id IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1
                   FROM mip_app.campaign_message_variants AS variant
                   WHERE variant.campaign_id = approval.campaign_id
                     AND variant.variant_name = approval.variant_name
                     AND variant.channel = approval.channel
               )
           )
    ) THEN
        RAISE EXCEPTION
            'Cannot validate approval proof binding: legacy rows are ambiguous or orphaned';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM mip_app.generated_outreach_drafts AS draft
        WHERE (draft.campaign_id IS NULL) <> (draft.variant_name IS NULL)
           OR (
               draft.campaign_id IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1
                   FROM mip_app.campaign_message_variants AS variant
                   WHERE variant.campaign_id = draft.campaign_id
                     AND variant.variant_name = draft.variant_name
                     AND variant.channel = draft.channel
               )
           )
    ) THEN
        RAISE EXCEPTION
            'Cannot validate generated outreach binding: legacy rows are ambiguous or orphaned';
    END IF;

    ALTER TABLE mip_app.approvals VALIDATE CONSTRAINT approvals_channel_chk;
    ALTER TABLE mip_app.approvals VALIDATE CONSTRAINT approvals_channel_required_chk;
    ALTER TABLE mip_app.approvals VALIDATE CONSTRAINT approvals_campaign_variant_pair_chk;
    ALTER TABLE mip_app.approvals VALIDATE CONSTRAINT approvals_campaign_variant_channel_fkey;
    ALTER TABLE mip_app.call_dispositions
        VALIDATE CONSTRAINT call_dispositions_audit_event_id_fkey;
    ALTER TABLE mip_app.lead_outcomes
        VALIDATE CONSTRAINT lead_outcomes_audit_event_id_fkey;
    ALTER TABLE mip_app.generated_outreach_drafts
        VALIDATE CONSTRAINT generated_outreach_campaign_variant_pair_chk;
    ALTER TABLE mip_app.generated_outreach_drafts
        VALIDATE CONSTRAINT generated_outreach_campaign_variant_channel_fkey;
END $$;

CREATE TRIGGER trg_generated_outreach_drafts_immutable
    BEFORE UPDATE OR DELETE OR TRUNCATE ON mip_app.generated_outreach_drafts
    FOR EACH STATEMENT
    EXECUTE FUNCTION mip_app.prevent_outreach_evidence_mutation();

DROP TRIGGER IF EXISTS trg_campaign_message_variants_immutable
    ON mip_app.campaign_message_variants;
CREATE TRIGGER trg_campaign_message_variants_immutable
    BEFORE UPDATE OR DELETE OR TRUNCATE ON mip_app.campaign_message_variants
    FOR EACH STATEMENT
    EXECUTE FUNCTION mip_app.prevent_outreach_evidence_mutation();

-- A campaign-bound decision must serialize with campaign lifecycle/treatment
-- mutation. The row-share lock conflicts with UPDATE, so either the decision
-- commits while the campaign is eligible or it observes the newer ineligible
-- state and fails; no post-revocation evidence row can slip through.
CREATE OR REPLACE FUNCTION mip_app.enforce_campaign_decision_lifecycle()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    campaign_status TEXT;
    campaign_owner_email TEXT;
    campaign_treatment_state TEXT;
    campaign_treatment_algorithm_version TEXT;
    campaign_treatment_fingerprint TEXT;
    decision_document JSONB;
    decision_owner_email TEXT;
    decision_treatment_fingerprint TEXT;
BEGIN
    IF NEW.campaign_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT status, owner_email, treatment_state,
           treatment_algorithm_version, treatment_fingerprint
    INTO campaign_status, campaign_owner_email, campaign_treatment_state,
         campaign_treatment_algorithm_version, campaign_treatment_fingerprint
    FROM mip_app.campaigns
    WHERE campaign_id = NEW.campaign_id
    FOR SHARE;

    IF NOT FOUND
       OR campaign_treatment_state IS DISTINCT FROM 'ready'
       OR campaign_treatment_algorithm_version IS DISTINCT FROM 'campaign-treatment-v2'
       OR NEW.action NOT IN ('approve', 'reject')
       OR (
           NEW.action = 'approve'
           AND campaign_status NOT IN ('approved', 'live', 'active')
       )
       OR (
           NEW.action = 'reject'
           AND campaign_status NOT IN (
               'draft', 'pending_review', 'approved', 'live', 'active'
           )
       ) THEN
        RAISE EXCEPTION
            'campaign lifecycle state does not allow this outreach decision'
            USING ERRCODE = '23514';
    END IF;

    BEGIN
        decision_document := NEW.decision_intent::jsonb;
        IF jsonb_typeof(decision_document) IS DISTINCT FROM 'object' THEN
            RAISE EXCEPTION 'campaign decision intent must be a JSON object'
                USING ERRCODE = '23514';
        END IF;
        decision_owner_email :=
            decision_document->>'campaign_owner_email';
        decision_treatment_fingerprint :=
            decision_document->>'campaign_treatment_fingerprint';
    EXCEPTION
        WHEN OTHERS THEN
            RAISE EXCEPTION
                'campaign decision intent is not valid JSON'
                USING ERRCODE = '23514';
    END;
    IF decision_document->>'action' IS DISTINCT FROM NEW.action
       OR lower(btrim(decision_document->>'actor'))
          IS DISTINCT FROM lower(btrim(NEW.actor_email))
       OR decision_document->>'borrower_id' IS DISTINCT FROM NEW.borrower_id
       OR decision_document->>'campaign_id' IS DISTINCT FROM NEW.campaign_id::TEXT
       OR decision_document->>'variant_name' IS DISTINCT FROM NEW.variant_name
       OR decision_document->>'channel' IS DISTINCT FROM NEW.channel
       OR decision_document->>'offer_code' IS DISTINCT FROM NEW.offer_code
       OR NEW.decision_payload_hash IS DISTINCT FROM
          encode(sha256(convert_to(NEW.decision_intent, 'UTF8')), 'hex')
       OR decision_owner_email IS NULL
       OR lower(btrim(decision_owner_email))
          IS DISTINCT FROM lower(btrim(campaign_owner_email))
       OR decision_treatment_fingerprint IS NULL
       OR decision_treatment_fingerprint IS DISTINCT FROM campaign_treatment_fingerprint THEN
        RAISE EXCEPTION
            'campaign treatment proof changed before outreach decision commit'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_approvals_campaign_lifecycle
    ON mip_app.approvals;
CREATE TRIGGER trg_approvals_campaign_lifecycle
    BEFORE INSERT ON mip_app.approvals
    FOR EACH ROW
    EXECUTE FUNCTION mip_app.enforce_campaign_decision_lifecycle();

-- Approval decisions are evidence. The app needs one narrowly-scoped UPDATE
-- to atomically attach the response and audit row after the idempotent INSERT;
-- every business/proof-binding column is immutable, and removal is forbidden.
CREATE OR REPLACE FUNCTION mip_app.enforce_approval_finalize_only()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF (to_jsonb(NEW) - ARRAY['decision_response', 'audit_event_id'])
       IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['decision_response', 'audit_event_id'])
       OR OLD.decision_response IS NOT NULL
       OR OLD.audit_event_id IS NOT NULL
       OR NEW.decision_response IS NULL
       OR NEW.audit_event_id IS NULL THEN
        RAISE EXCEPTION
            'mip_app.approvals is immutable except for its one-time audit finalization'
            USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_approvals_finalize_only
    BEFORE UPDATE ON mip_app.approvals
    FOR EACH ROW
    EXECUTE FUNCTION mip_app.enforce_approval_finalize_only();

CREATE TRIGGER trg_approvals_no_remove
    BEFORE DELETE OR TRUNCATE ON mip_app.approvals
    FOR EACH STATEMENT
    EXECUTE FUNCTION mip_app.prevent_outreach_evidence_mutation();

CREATE TRIGGER trg_call_dispositions_finalize_only
    BEFORE UPDATE ON mip_app.call_dispositions
    FOR EACH ROW
    EXECUTE FUNCTION mip_app.enforce_audit_event_finalize_only();

CREATE TRIGGER trg_call_dispositions_no_remove
    BEFORE DELETE OR TRUNCATE ON mip_app.call_dispositions
    FOR EACH STATEMENT
    EXECUTE FUNCTION mip_app.prevent_outreach_evidence_mutation();

CREATE TRIGGER trg_lead_outcomes_finalize_only
    BEFORE UPDATE ON mip_app.lead_outcomes
    FOR EACH ROW
    EXECUTE FUNCTION mip_app.enforce_audit_event_finalize_only();

CREATE TRIGGER trg_lead_outcomes_no_remove
    BEFORE DELETE OR TRUNCATE ON mip_app.lead_outcomes
    FOR EACH STATEMENT
    EXECUTE FUNCTION mip_app.prevent_outreach_evidence_mutation();

-- Growth-agent runs are born terminal in the current contract. Preserve every
-- completed/failed result and its evidence, allowing only the same one-time
-- audit attachment used by the runtime transaction.
CREATE TRIGGER trg_growth_agent_runs_finalize_only
    BEFORE UPDATE ON mip_app.growth_agent_runs
    FOR EACH ROW
    EXECUTE FUNCTION mip_app.enforce_audit_event_finalize_only();

CREATE TRIGGER trg_growth_agent_runs_no_remove
    BEFORE DELETE OR TRUNCATE ON mip_app.growth_agent_runs
    FOR EACH STATEMENT
    EXECUTE FUNCTION mip_app.prevent_outreach_evidence_mutation();

INSERT INTO mip_app.schema_migrations (version, description)
VALUES (
    '2026_07_14_outreach_evidence_immutability',
    'Convert generated draft campaign ids to governed UUID foreign keys and make generated drafts and campaign variants immutable'
)
ON CONFLICT (version) DO NOTHING;

INSERT INTO mip_app.schema_migrations (version, description)
VALUES (
    '2026_07_14_outreach_variant_binding',
    'Deterministically bind legacy outreach proof to one exact variant and validate every proof constraint'
)
ON CONFLICT (version) DO NOTHING;

INSERT INTO mip_app.schema_migrations (version, description)
VALUES (
    '2026_07_14_approval_proof_guards',
    'Require channels for campaign-bound approvals, preserve campaign-less legacy proof, allow only one-time audit finalization, and block proof removal'
)
ON CONFLICT (version) DO NOTHING;

INSERT INTO mip_app.schema_migrations (version, description)
VALUES (
    '2026_07_14_outcome_and_agent_run_immutability',
    'Make call dispositions, lead outcomes, and terminal growth-agent runs immutable except for one-time audit linkage'
)
ON CONFLICT (version) DO NOTHING;

INSERT INTO mip_app.schema_migrations (version, description)
VALUES (
    '2026_07_14_hmac_outcome_source_reference',
    'Enforce HMAC-derived auto-<32 lowercase hex> lead outcome source references on new and updated rows while preserving legacy history'
)
ON CONFLICT (version) DO NOTHING;
