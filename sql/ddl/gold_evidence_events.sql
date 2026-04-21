-- =============================================================================
-- gold_evidence_events.sql
-- -----------------------------------------------------------------------------
-- Purpose:   DDL for `mip_demo.gold.evidence_events` -- one row per
--            (CLIP, signal_type) carrying the source Cotality signal that
--            backed a given lead score contribution. THIS is what the
--            EvidenceDrawer renders in the UI, and what gold.borrower_360
--            points to via the evidence_ids ARRAY and trigger_timeline_json
--            columns.
--
-- Grain:     One row per (clip, evidence_id). evidence_id is deterministic
--            from (clip, signal_type, timestamp) so re-runs emit stable ids.
-- PK:        (clip, evidence_id).
-- Clustering: Liquid cluster on (clip). Gold.borrower_360 looks up by clip;
--            the timeline query ORDER BYs by signal_rank within a clip.
--
-- Data contract reference: docs/data-contract-module0.md §3.4.
-- Slice:     module0-real-data-slice3 (gold layer build).
-- Pydantic target: backend.schemas.common.EvidenceEvent (router drops the
--            `clip` and `signal_rank` columns before emission).
--
-- Source unions (the transformation file executes these; this DDL declares
-- the target shape):
--   - silver.lien_current      -> rate_spread, equity, competitor_lien signals.
--   - silver.mortgage_events   -> recent_refi, recent_payoff signals.
--   - silver.owner_transfer_events -> recent_sale, REO signals.
--   - gold.property_owner_bridge   -> multi_property, absentee_mailing,
--                                      corporate_owner signals.
--   - silver.market_rates_weekly   -> market_trend signal.
--
-- BLOCKED signals: `signal_type='permit'` and `signal_type='listing'` are
--            NEVER emitted on the real-data path (data-contract §9 + §3.4).
--            Cotality Permits + MLS Listings are not yet licensed; mock-mode
--            ev-004 (permit) and ev-008 (listing) remain the only such
--            evidence rendered in the booth.
--
-- `confidence` sourcing (per data-contract §3.4):
--   - AVM-backed signals (equity, equity_delta): upstream confidence_score_mktg
--     from silver.lien_current. Coerced to 0..1 (divided by 100 if >1).
--   - Rate_spread / market_trend          : 0.92 (deterministic).
--   - Owner-Link derived (multi_property,
--     absentee_mailing, corporate_owner)  : 0.85 (deterministic).
--   - Recent-event (recent_refi, recent_payoff,
--     recent_sale, competitor_lien, foreclosure_stage) : 0.89 (deterministic).
-- Choice rationale: variable AVM confidence feeds an honest uncertainty
-- ribbon in the UI; count-based rows are effectively boolean so a fixed
-- 0.85-0.92 keeps the UI stable and the ordering deterministic.
--
-- `source_table` MUST be a REAL UC path (EvidenceDrawer shows it). Allowed
-- values (only): 'mip_demo.silver.lien_current', 'mip_demo.silver.property_
-- master', 'mip_demo.silver.mortgage_events', 'mip_demo.silver.owner_
-- transfer_events', 'mip_demo.silver.market_rates_weekly', 'mip_demo.gold.
-- property_owner_bridge'.
--
-- `timestamp` is a STRING (ISO-8601) to match the Pydantic EvidenceEvent
-- declaration (`timestamp: str`), NOT a DATE/TIMESTAMP column. The router
-- passes it through unchanged.
--
-- Idempotency: CREATE TABLE IF NOT EXISTS; populated via CTAS in the
--            transformation file.
-- =============================================================================

CREATE TABLE IF NOT EXISTS mip_demo.gold.evidence_events (
  clip           STRING NOT NULL COMMENT 'Cotality CLIP. Not in Pydantic EvidenceEvent (router strips); used for join / filter.',
  evidence_id    STRING NOT NULL COMMENT 'Deterministic: "ev-" || substr(sha2(clip || signal_type || timestamp, 256), 1, 12). Stable across refreshes so Borrower360.evidence_ids stays consistent.',
  source_product STRING NOT NULL COMMENT 'Human label: Voluntary Lien / AVM / Owner Link / Mortgage Domain / Owner Transfer / Market Rates.',
  source_table   STRING NOT NULL COMMENT 'Real UC path. Shown verbatim in EvidenceDrawer -- must be a resolvable mip_demo.silver.* or mip_demo.gold.* path.',
  signal_type    STRING NOT NULL COMMENT 'Controlled vocab: rate_spread / equity / equity_delta / competitor_lien / multi_property / absentee_mailing / corporate_owner / foreclosure_stage / recent_refi / recent_payoff / recent_sale / market_trend. BLOCKED vocab (permit, listing) NEVER emitted.',
  signal_value   STRING NOT NULL COMMENT 'Human-readable value: "+88 bps", "$285K", "3 properties", "competitor refi".',
  display_text   STRING NOT NULL COMMENT 'One-sentence deterministic template per signal_type. No PII.',
  confidence     DOUBLE NOT NULL COMMENT '0..1. Per-signal: AVM uses upstream confidence_score_mktg; count-based rows 0.85-0.92 (see header).',
  `timestamp`    STRING NOT NULL COMMENT 'ISO-8601 STRING (matches Pydantic EvidenceEvent.timestamp: str).',
  signal_rank    INT    NOT NULL COMMENT 'Deterministic priority order for Borrower360.evidence_ids: rate_spread=1, equity=2, market_trend=3, etc. Smaller = higher priority. Gold-only.'
)
USING DELTA
CLUSTER BY (clip)
COMMENT 'Per-(CLIP, signal) evidence rows. ONE ROW = ONE EvidenceEvent in the Pydantic layer. Unions: silver.lien_current + silver.mortgage_events + silver.owner_transfer_events + gold.property_owner_bridge + silver.market_rates_weekly. permit / listing signal_types are BLOCKED on real data (data-contract §9). See docs/data-contract-module0.md §3.4.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);
