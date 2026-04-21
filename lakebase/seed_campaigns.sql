-- Deterministic starter seed -- Summit Mortgage campaigns
-- and five approval rows. Idempotent via stable UUIDs + ON CONFLICT.
-- Run after schema.sql.

SET search_path TO mip_app, public;

-- Campaigns -----------------------------------------------------------
-- Fixed UUIDs so re-running the seed is a no-op and so approvals below
-- can reference the same campaigns without a lookup.
INSERT INTO mip_app.campaigns (campaign_id, name, owner_email, status, criteria, created_at)
VALUES
    (
        '11111111-1111-4111-8111-111111111111',
        'Summit Mortgage Refi — In the Money Q2',
        'skyler@entrada.ai',
        'active',
        '{"segment": "itm", "min_spread_bps": 75, "states": ["IL","CA","WA","CO"]}'::jsonb,
        now() - interval '7 days'
    ),
    (
        '22222222-2222-4222-8222-222222222222',
        'Summit Mortgage Cash-Out — High Equity',
        'skyler@entrada.ai',
        'active',
        '{"segment": "cashout", "min_equity_pct": 25, "states": ["IL","FL","TX"]}'::jsonb,
        now() - interval '5 days'
    ),
    (
        '33333333-3333-4333-8333-333333333333',
        'Summit Mortgage HELOC — Permit-Triggered',
        'skyler@entrada.ai',
        'active',
        '{"segment": "heloc", "heloc_equity_min_pct": 35, "requires_permit": true}'::jsonb,
        now() - interval '3 days'
    )
ON CONFLICT (campaign_id) DO NOTHING;

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
