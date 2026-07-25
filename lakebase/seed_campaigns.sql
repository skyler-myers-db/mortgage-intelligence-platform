-- Deterministic starter seed -- Summit Mortgage campaigns
-- and five approval rows. Idempotent via stable UUIDs + ON CONFLICT.
-- Run after schema.sql.

SET search_path TO mip_app, public;

-- Sales team ----------------------------------------------------------
-- Synthetic internal staff identities used by the Sales Manager surface.
-- These are not borrower contact emails. They provide a governed roster for
-- assignment, distribution, and disposition validation.
INSERT INTO mip_app.sales_team (
    email, display_label, role, manager_email, region, capacity_per_day, active
)
VALUES
    ('sam.manager@summit.example', 'Summit Sales Manager', 'sales_manager', NULL, 'National', 0, true),
    ('skyler@entrada.ai', 'Entrada Demo Operator', 'admin', NULL, 'National', 0, true),
    ('lo01@summit.example', 'Summit LO 01', 'loan_officer', 'sam.manager@summit.example', 'IL', 35, true),
    ('lo02@summit.example', 'Summit LO 02', 'loan_officer', 'sam.manager@summit.example', 'CA', 35, true),
    ('lo03@summit.example', 'Summit LO 03', 'loan_officer', 'sam.manager@summit.example', 'TX', 35, true),
    ('lo04@summit.example', 'Summit LO 04', 'loan_officer', 'sam.manager@summit.example', 'FL', 30, true),
    ('lo05@summit.example', 'Summit LO 05', 'loan_officer', 'sam.manager@summit.example', 'WA', 30, true),
    ('lo06@summit.example', 'Summit LO 06', 'loan_officer', 'sam.manager@summit.example', 'CO', 25, true)
ON CONFLICT (email)
DO UPDATE SET
    display_label = EXCLUDED.display_label,
    role = EXCLUDED.role,
    manager_email = EXCLUDED.manager_email,
    region = EXCLUDED.region,
    capacity_per_day = EXCLUDED.capacity_per_day,
    active = EXCLUDED.active,
    updated_at = now();

-- Loan officers (S2) ---------------------------------------------------
-- Six synthetic officers joined to the sales_team roster above by email.
-- Fixed UUIDs keep re-runs a no-op. Coverage is two-letter state codes
-- plus 5-digit county FIPS -- arrays only, no geometry. Display names
-- mirror sales_team.display_label so assignment chips and the sales
-- surfaces never show two names for the same person.
INSERT INTO mip_app.loan_officers (
    loan_officer_id, email, display_name, coverage_states, coverage_counties, active
)
VALUES
    ('55555555-5555-4555-8555-555555555501', 'lo01@summit.example', 'Summit LO 01',
     ARRAY['IL','IN','WI']::TEXT[], ARRAY['17031','17043','17089']::TEXT[], true),
    ('55555555-5555-4555-8555-555555555502', 'lo02@summit.example', 'Summit LO 02',
     ARRAY['CA','NV']::TEXT[], ARRAY['06037','06059','06073']::TEXT[], true),
    ('55555555-5555-4555-8555-555555555503', 'lo03@summit.example', 'Summit LO 03',
     ARRAY['TX','OK']::TEXT[], ARRAY['48029','48113','48201']::TEXT[], true),
    ('55555555-5555-4555-8555-555555555504', 'lo04@summit.example', 'Summit LO 04',
     ARRAY['FL','GA']::TEXT[], ARRAY['12011','12086','12099']::TEXT[], true),
    ('55555555-5555-4555-8555-555555555505', 'lo05@summit.example', 'Summit LO 05',
     ARRAY['WA','OR','ID']::TEXT[], ARRAY['53033','53053','53061']::TEXT[], true),
    ('55555555-5555-4555-8555-555555555506', 'lo06@summit.example', 'Summit LO 06',
     ARRAY['CO','UT','AZ']::TEXT[], ARRAY['08005','08031','08059']::TEXT[], true)
ON CONFLICT (loan_officer_id)
DO UPDATE SET
    email = EXCLUDED.email,
    display_name = EXCLUDED.display_name,
    coverage_states = EXCLUDED.coverage_states,
    coverage_counties = EXCLUDED.coverage_counties,
    active = EXCLUDED.active,
    updated_at = now();

-- Campaigns -----------------------------------------------------------
-- Fixed UUIDs so re-running the seed is a no-op and so approvals below
-- can reference the same campaigns without a lookup. These predate immutable
-- T0 treatment materialization, so they remain archived narrative evidence;
-- activation must use a newly built treatment-ready campaign.
INSERT INTO mip_app.campaigns (
    campaign_id, name, owner_email, status, criteria,
    suppression_policy, channel_cascade, send_window, created_at
)
VALUES
    (
        '11111111-1111-4111-8111-111111111111',
        'Summit Mortgage Refi — In the Money Q2',
        'skyler@entrada.ai',
        'archived',
        '{"segment": "itm", "min_spread_bps": 75, "states": ["IL","CA","WA","CO"], "marketing_eligibility": "Eligible only", "consent_status": "Opt-in", "recency": "Untouched 30d"}'::jsonb,
        '{"default": "eligible_only", "require_marketing_eligible": true, "frequency_cap_days": 30}'::jsonb,
        '[{"step": 1, "channel": "email"}, {"step": 2, "channel": "sms", "after_days": 3}]'::jsonb,
        '{"days": ["Tuesday", "Wednesday", "Thursday"], "timezone": "borrower_local", "start_local": "09:00", "end_local": "16:00"}'::jsonb,
        now() - interval '7 days'
    ),
    (
        '22222222-2222-4222-8222-222222222222',
        'Summit Mortgage Cash-Out — High Equity',
        'skyler@entrada.ai',
        'archived',
        '{"segment": "cashout", "min_equity_pct": 25, "states": ["IL","FL","TX"], "marketing_eligibility": "Eligible only", "consent_status": "Opt-in", "recency": "Untouched 30d"}'::jsonb,
        '{"default": "eligible_only", "require_marketing_eligible": true, "frequency_cap_days": 30}'::jsonb,
        '[{"step": 1, "channel": "email"}, {"step": 2, "channel": "direct_mail", "after_days": 10}]'::jsonb,
        '{"days": ["Tuesday", "Wednesday", "Thursday"], "timezone": "borrower_local", "start_local": "09:00", "end_local": "16:00"}'::jsonb,
        now() - interval '5 days'
    ),
    (
        '33333333-3333-4333-8333-333333333333',
        'Summit Mortgage HELOC — Equity/Propensity Intent',
        'skyler@entrada.ai',
        'archived',
        '{"segment": "heloc", "heloc_equity_min_pct": 35, "heloc_propensity_min": 700, "intent_signal": "cotality_heloc_propensity", "filed_permits": "pending_not_inferred", "marketing_eligibility": "Eligible only", "consent_status": "Opt-in", "recency": "Untouched 30d"}'::jsonb,
        '{"default": "eligible_only", "require_marketing_eligible": true, "frequency_cap_days": 30}'::jsonb,
        '[{"step": 1, "channel": "email"}, {"step": 2, "channel": "sms", "after_days": 3}]'::jsonb,
        '{"days": ["Tuesday", "Wednesday", "Thursday"], "timezone": "borrower_local", "start_local": "09:00", "end_local": "16:00"}'::jsonb,
        now() - interval '3 days'
    )
ON CONFLICT (campaign_id)
DO UPDATE SET
    name = EXCLUDED.name,
    status = EXCLUDED.status,
    criteria = EXCLUDED.criteria,
    suppression_policy = EXCLUDED.suppression_policy,
    channel_cascade = EXCLUDED.channel_cascade,
    send_window = EXCLUDED.send_window,
    updated_at = now()
WHERE campaigns.treatment_state = 'legacy_unbound';

-- Campaign message variants -------------------------------------------
-- Approval proof must bind to the exact immutable campaign copy and
-- channel reviewed by the operator. Seeds are insert-only because these
-- rows become evidence once referenced; changing copy requires a new
-- variant name instead of mutating historical proof.
INSERT INTO mip_app.campaign_message_variants (
    campaign_id, variant_name, channel, subject, body, weight_pct,
    generation_mode, generator_label
)
VALUES
    (
        '11111111-1111-4111-8111-111111111111',
        'Benefit-led',
        'email',
        'See whether today''s rates could improve your mortgage',
        'A licensed Summit Mortgage loan officer can review your current rate, estimated equity, and available refinance options with you. There is no obligation to proceed.',
        100,
        'operator',
        'Reviewed seed copy'
    ),
    (
        '22222222-2222-4222-8222-222222222222',
        'Benefit-led',
        'email',
        'Explore ways your home equity could support your plans',
        'A licensed Summit Mortgage loan officer can review cash-out refinance options based on your goals, available equity, and current mortgage terms. There is no obligation to proceed.',
        100,
        'operator',
        'Reviewed seed copy'
    ),
    (
        '33333333-3333-4333-8333-333333333333',
        'Benefit-led',
        'email',
        'Review flexible ways to use your home equity',
        'A licensed Summit Mortgage loan officer can compare home-equity and refinance options based on your goals and current mortgage terms. There is no obligation to proceed.',
        100,
        'operator',
        'Reviewed seed copy'
    )
ON CONFLICT (campaign_id, variant_name, channel) DO NOTHING;

-- Approvals (5 sample rows) -------------------------------------------
-- 2026-06-11 audit P1-5: these are REAL mip.gold.borrower_360 borrower
-- IDs (CLIP-hash derived, masked, stable across refreshes while the
-- source CLIPs remain in the Cotality share), selected live on
-- 2026-06-11 so the canonical narrative trio joins to real dossiers and
-- every stat in the rationale matches the proof drawer. The previous
-- 5-digit placeholders (B-48291..B-48295) violated the B-[0-9A-Z]{13}
-- contract, joined to nothing, and skewed approval-rate metrics; the
-- schema.sql migration 2026_06_11_narrative_seed_real_ids maps only the
-- five exact legacy seed rows and fails on any other malformed history.
-- No approval is deleted by recurring schema apply. If a future
-- share refresh drops one of these CLIPs, re-select with
-- tools/select_narrative_borrowers.sql and update BOTH the IDs and the
-- rationale stats together.
INSERT INTO mip_app.approvals (
    approval_id, campaign_id, variant_name, channel, borrower_id,
    offer_code, action, actor_email, rationale, decided_at
)
VALUES
    (
        '44444444-4444-4444-8444-444444444441',
        '11111111-1111-4111-8111-111111111111',
        'Benefit-led',
        'email',
        'B-0CPWBTJMAPFY2',
        'refi',
        'approve',
        'skyler@entrada.ai',
        'Rate spread +401 bps at 26% equity — in-the-money per fn_in_the_money (IL, campaign states); evidence chips all cited.',
        now() - interval '4 days'
    ),
    (
        '44444444-4444-4444-8444-444444444442',
        '22222222-2222-4222-8222-222222222222',
        'Benefit-led',
        'email',
        'B-1IB0UGBTFYM20',
        'cash_out',
        'approve',
        'skyler@entrada.ai',
        'Equity 100% (free-and-clear, TX) clears the 25% cash-out floor; +33 bps spread is below the refi bar — cash-out is the fit per fn_next_best_offer.',
        now() - interval '2 days'
    ),
    (
        '44444444-4444-4444-8444-444444444443',
        '33333333-3333-4333-8333-333333333333',
        'Benefit-led',
        'email',
        'B-102FL7THC6Q3L',
        'refi_plus_heloc',
        'approve',
        'skyler@entrada.ai',
        'Equity 91% clears the 35% HELOC floor with +379 bps refi incentive (IL) — combined Refinance + HELOC per fn_next_best_offer; permit feed pending, equity-led lane.',
        now() - interval '1 day'
    ),
    (
        '44444444-4444-4444-8444-444444444444',
        '11111111-1111-4111-8111-111111111111',
        'Benefit-led',
        'email',
        'B-1BCZXFQYCX715',
        'refi',
        'hold',
        'skyler@entrada.ai',
        'Marginal +87 bps spread at 18% equity (IL) — just over the 75 bps floor; re-review after next FRED publish.',
        now() - interval '12 hours'
    ),
    (
        '44444444-4444-4444-8444-444444444445',
        '22222222-2222-4222-8222-222222222222',
        'Benefit-led',
        'email',
        'B-1VU4FO4XBQPC4',
        'cash_out',
        'reject',
        'skyler@entrada.ai',
        'Owner Link resolves 3,564 related properties (TX) — institutional multi-property profile; routed to investor desk instead of consumer cash-out outreach.',
        now() - interval '6 hours'
    )
ON CONFLICT (approval_id) DO NOTHING;

-- Tenant disclosures --------------------------------------------------
-- Governed, versioned footer blocks used by outreach draft generation.
-- These are synthetic-demo safe tenant strings, not borrower PII. State
-- rows override _ALL. External delivery still stays outside Module 0;
-- drafts must carry the disclosure version used for audit reconstruction.
INSERT INTO mip_app.tenant_disclosures (
    tenant_id, state, channel, disclosure_version, body, active, updated_at
)
VALUES
    ('summit', '_ALL', 'email', 'summit-demo-2026-05-v1',
     'Summit Mortgage, NMLS #123456. Equal Housing Lender. This is not a commitment to lend. Terms subject to credit, collateral, and underwriting approval. To opt out of marketing, reply unsubscribe or contact Summit Mortgage at its governed compliance address.',
     true, now()),
    ('summit', '_ALL', 'direct_mail', 'summit-demo-2026-05-v1',
     'Summit Mortgage, NMLS #123456. Equal Housing Lender. This is not a commitment to lend. Terms subject to credit, collateral, and underwriting approval. To opt out of marketing, contact Summit Mortgage at its governed compliance address.',
     true, now()),
    ('summit', '_ALL', 'sms', 'summit-demo-2026-05-v1',
     'Summit Mortgage NMLS #123456. Equal Housing Lender. Reply STOP to opt out. Msg and data rates may apply.',
     true, now()),
    ('summit', 'CA', 'email', 'summit-demo-ca-2026-05-v1',
     'Summit Mortgage, NMLS #123456. Equal Housing Lender. California residents: this is not a commitment to lend and terms are subject to credit, collateral, and underwriting approval. To opt out of marketing, reply unsubscribe or contact Summit Mortgage at its governed compliance address.',
     true, now()),
    ('summit', 'CA', 'sms', 'summit-demo-ca-2026-05-v1',
     'Summit Mortgage NMLS #123456. Equal Housing Lender. CA residents may reply STOP to opt out. Msg and data rates may apply.',
     true, now()),
    ('summit', 'NY', 'email', 'summit-demo-ny-2026-05-v1',
     'Summit Mortgage, NMLS #123456. Equal Housing Lender. New York residents: mortgage terms are subject to licensed review, credit, collateral, and underwriting approval. To opt out of marketing, reply unsubscribe or contact Summit Mortgage at its governed compliance address.',
     true, now())
ON CONFLICT (tenant_id, state, channel, disclosure_version)
DO UPDATE SET
    body = EXCLUDED.body,
    active = EXCLUDED.active,
    updated_at = EXCLUDED.updated_at;
