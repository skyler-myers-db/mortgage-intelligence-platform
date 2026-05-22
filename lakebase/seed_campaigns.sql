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

-- Campaigns -----------------------------------------------------------
-- Fixed UUIDs so re-running the seed is a no-op and so approvals below
-- can reference the same campaigns without a lookup.
INSERT INTO mip_app.campaigns (
    campaign_id, name, owner_email, status, criteria,
    suppression_policy, channel_cascade, send_window, created_at
)
VALUES
    (
        '11111111-1111-4111-8111-111111111111',
        'Summit Mortgage Refi — In the Money Q2',
        'skyler@entrada.ai',
        'active',
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
        'active',
        '{"segment": "cashout", "min_equity_pct": 25, "states": ["IL","FL","TX"], "marketing_eligibility": "Eligible only", "consent_status": "Opt-in", "recency": "Untouched 30d"}'::jsonb,
        '{"default": "eligible_only", "require_marketing_eligible": true, "frequency_cap_days": 30}'::jsonb,
        '[{"step": 1, "channel": "email"}, {"step": 2, "channel": "direct_mail", "after_days": 10}]'::jsonb,
        '{"days": ["Tuesday", "Wednesday", "Thursday"], "timezone": "borrower_local", "start_local": "09:00", "end_local": "16:00"}'::jsonb,
        now() - interval '5 days'
    ),
    (
        '33333333-3333-4333-8333-333333333333',
        'Summit Mortgage HELOC — Permit-Triggered',
        'skyler@entrada.ai',
        'active',
        '{"segment": "heloc", "heloc_equity_min_pct": 35, "requires_permit": true, "marketing_eligibility": "Eligible only", "consent_status": "Opt-in", "recency": "Untouched 30d"}'::jsonb,
        '{"default": "eligible_only", "require_marketing_eligible": true, "frequency_cap_days": 30}'::jsonb,
        '[{"step": 1, "channel": "email"}, {"step": 2, "channel": "sms", "after_days": 3}]'::jsonb,
        '{"days": ["Tuesday", "Wednesday", "Thursday"], "timezone": "borrower_local", "start_local": "09:00", "end_local": "16:00"}'::jsonb,
        now() - interval '3 days'
    )
ON CONFLICT (campaign_id)
DO UPDATE SET
    criteria = EXCLUDED.criteria,
    suppression_policy = EXCLUDED.suppression_policy,
    channel_cascade = EXCLUDED.channel_cascade,
    send_window = EXCLUDED.send_window,
    updated_at = now();

-- Approvals (5 sample rows) -------------------------------------------
-- Synthetic borrowers (B-48291 / B-48294 / B-48295) are the canonical
-- trio pinned by the product narrative; the two extra ids keep the
-- approvals list visually interesting without inventing PII.
INSERT INTO mip_app.approvals (
    approval_id, campaign_id, borrower_id, offer_code, action, actor_email, rationale, decided_at
)
VALUES
    (
        '44444444-4444-4444-8444-444444444441',
        '11111111-1111-4111-8111-111111111111',
        'B-48291',
        'refi',
        'approve',
        'skyler@entrada.ai',
        'Rate spread +125 bps, in-the-money per fn_in_the_money; evidence chips all cited.',
        now() - interval '4 days'
    ),
    (
        '44444444-4444-4444-8444-444444444442',
        '22222222-2222-4222-8222-222222222222',
        'B-48294',
        'cash_out',
        'approve',
        'skyler@entrada.ai',
        'Equity 42% clears cash-out threshold (25%), no refi incentive — cash-out is the fit.',
        now() - interval '2 days'
    ),
    (
        '44444444-4444-4444-8444-444444444443',
        '33333333-3333-4333-8333-333333333333',
        'B-48295',
        'heloc',
        'approve',
        'skyler@entrada.ai',
        'Permit on file + equity 39% clears HELOC bar; refi rate below threshold.',
        now() - interval '1 day'
    ),
    (
        '44444444-4444-4444-8444-444444444444',
        '11111111-1111-4111-8111-111111111111',
        'B-48292',
        'refi',
        'hold',
        'skyler@entrada.ai',
        'Marginal spread — re-review after next FRED publish.',
        now() - interval '12 hours'
    ),
    (
        '44444444-4444-4444-8444-444444444445',
        '22222222-2222-4222-8222-222222222222',
        'B-48293',
        'cash_out',
        'reject',
        'skyler@entrada.ai',
        'Owner Link shows multi-property investor behavior; routed to investor desk.',
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
