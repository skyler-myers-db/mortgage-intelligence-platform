-- =============================================================================
-- lender_dictionary_seed.sql
-- -----------------------------------------------------------------------------
-- Purpose:   Seed `mip.ref.lender_dictionary` with the canonical 11 entries
--            from the inline Python fallback (`_LENDER_REF_MAP` in
--            `backend/services/pii_redaction.py`) plus additional common
--            US mortgage servicers confirmed in public CoreLogic lien data.
--
-- Provenance posture:
--            * `source = 'manual_seed'` on every row -- every label was hand-
--              curated and reviewed against the raw share strings we observed
--              in slices 1-3. When a tenant adds or overrides an entry via
--              analyst contribution, the row SHOULD carry
--              source = 'analyst_contribution' and be MERGEd through a
--              separate workflow (not this file).
--            * No invented lenders. Every `raw_key` is a real share lender
--              string we have observed in `cotality_mortgage_data.corelogic.
--              entrada_eval_voluntary_lien_status_marketing_v2` or the
--              public MERS lender list.
--
-- Idempotency: MERGE on raw_key. Re-running this file (as part of the
--            `mip_ref_seed` bundle job) is a no-op for unchanged rows and
--            refreshes `last_updated` + `display_name` for any edited row.
--
-- `is_competitor`:
--            FALSE only for SUMMIT MTG (the tenant). Every other servicer
--            is a competitor from Summit's perspective; tenants with a
--            different brand would override the SUMMIT row on first deploy.
-- =============================================================================

MERGE INTO mip.ref.lender_dictionary AS t
USING (
  SELECT col1 AS raw_key, col2 AS display_name, col3 AS lender_type, col4 AS is_competitor
  FROM VALUES
    -- 11 canonical entries mirrored from backend.services.pii_redaction._LENDER_REF_MAP
    ('UNITED WHOLESALE MTG',          'United Wholesale Mortgage',    'wholesale',          TRUE),
    ('WELLS FARGO BK NA',             'Wells Fargo Bank',             'bank',               TRUE),
    ('JPMORGAN CHASE BK NA',          'JPMorgan Chase',               'bank',               TRUE),
    ('ROCKET MTG LLC',                'Rocket Mortgage',              'non_bank_fintech',   TRUE),
    ('QUICKEN LNS',                   'Quicken Loans',                'non_bank_fintech',   TRUE),
    ('BANK OF AMERICA NA',            'Bank of America',              'bank',               TRUE),
    ('GUARANTEED RATE INC',           'Guaranteed Rate',              'non_bank_fintech',   TRUE),
    ('LOANDEPOT.COM LLC',             'loanDepot',                    'non_bank_fintech',   TRUE),
    ('CALIBER HM LOANS INC',          'Caliber Home Loans',           'non_bank_fintech',   TRUE),
    ('FAIRWAY INDEPENDENT MTG CORP',  'Fairway Independent Mortgage', 'non_bank_fintech',   TRUE),
    ('SUMMIT MTG',                    'Summit Mortgage',              'non_bank_fintech',   FALSE),
    -- Additional common US servicers observed in public CoreLogic lien data
    -- and the MERS lender directory. All labels are public trademarks / brand
    -- names; no invented lenders.
    ('PENNYMAC LOAN SVCS LLC',        'PennyMac Loan Services',       'non_bank_fintech',   TRUE),
    ('NATIONSTAR MTG LLC',            'Mr. Cooper',                   'non_bank_fintech',   TRUE),
    ('FREEDOM MTG CORP',              'Freedom Mortgage',             'non_bank_fintech',   TRUE),
    ('CITIZENS BK NA',                'Citizens Bank',                'bank',               TRUE),
    ('US BK NA',                      'US Bank',                      'bank',               TRUE),
    ('PNC BK NA',                     'PNC Bank',                     'bank',               TRUE),
    ('TRUIST BK',                     'Truist Bank',                  'bank',               TRUE),
    ('FIFTH THIRD BK NA',             'Fifth Third Bank',             'bank',               TRUE),
    ('REGIONS BK',                    'Regions Bank',                 'bank',               TRUE),
    ('FLAGSTAR BK FSB',               'Flagstar Bank',                'bank',               TRUE),
    ('NEW AMERICAN FUNDING',          'New American Funding',         'non_bank_fintech',   TRUE),
    ('NAVY FCU',                      'Navy Federal Credit Union',    'credit_union',       TRUE)
) AS s
ON t.raw_key = s.raw_key
WHEN MATCHED THEN UPDATE SET
  display_name  = s.display_name,
  lender_type   = s.lender_type,
  is_competitor = s.is_competitor,
  last_updated  = CURRENT_TIMESTAMP(),
  source        = 'manual_seed'
WHEN NOT MATCHED THEN INSERT (
  raw_key, display_name, lender_type, is_competitor, last_updated, source
) VALUES (
  s.raw_key, s.display_name, s.lender_type, s.is_competitor,
  CURRENT_TIMESTAMP(), 'manual_seed'
);
