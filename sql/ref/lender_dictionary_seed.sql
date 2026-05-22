-- =============================================================================
-- lender_dictionary_seed.sql
-- -----------------------------------------------------------------------------
-- Purpose:   Seed `mip.ref.lender_dictionary` with the canonical entries
--            from the inline Python fallback (`_LENDER_REF_MAP` in
--            `backend/services/pii_redaction.py`) plus additional raw-key
--            variants observed in the Cotality lien data.
--
-- Provenance posture:
--            * `source = 'manual_seed'` on every row -- every label was hand-
--              curated and reviewed against the raw share strings we observed
--              in slices 1-3. When a tenant adds or overrides an entry via
--              analyst contribution, the row SHOULD carry
--              source = 'analyst_contribution' and be MERGEd through a
--              separate workflow (not this file).
--            * Public-demo display names are controlled aliases
--              (`Competitor A`, `Competitor B`, ...), not competitor brand
--              names. Raw keys remain in UC for governed joins; the app/API
--              never displays the raw string unless Cotality and the lender
--              approve an internal unmasked walkthrough.
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
    -- Canonical entries mirrored from backend.services.pii_redaction._LENDER_REF_MAP
    ('UNITED WHOLESALE MTG',          'Competitor A',                 'wholesale',          TRUE),
    ('WELLS FARGO BK NA',             'Competitor B',                 'bank',               TRUE),
    ('JPMORGAN CHASE BK NA',          'Competitor C',                 'bank',               TRUE),
    ('ROCKET MTG LLC',                'Competitor D',                 'non_bank_fintech',   TRUE),
    ('QUICKEN LNS',                   'Competitor E',                 'non_bank_fintech',   TRUE),
    ('BANK OF AMERICA NA',            'Competitor F',                 'bank',               TRUE),
    ('GUARANTEED RATE INC',           'Competitor G',                 'non_bank_fintech',   TRUE),
    ('LOANDEPOT.COM LLC',             'Competitor H',                 'non_bank_fintech',   TRUE),
    ('CALIBER HM LOANS INC',          'Competitor I',                 'non_bank_fintech',   TRUE),
    ('FAIRWAY INDEPENDENT MTG CORP',  'Competitor J',                 'non_bank_fintech',   TRUE),
    ('SUMMIT MTG',                    'Summit Mortgage',              'non_bank_fintech',   FALSE),
    ('SUMMIT MORTGAGE',               'Summit Mortgage',              'non_bank_fintech',   FALSE),
    ('SUMMIT MTG CORP',               'Summit Mortgage',              'non_bank_fintech',   FALSE),
    ('SUMMIT MORTGAGE CORP',          'Summit Mortgage',              'non_bank_fintech',   FALSE),
    ('PENNYMAC LOAN SVCS LLC',        'Competitor K',                 'non_bank_fintech',   TRUE),
    ('NATIONSTAR MTG LLC',            'Competitor L',                 'non_bank_fintech',   TRUE),
    ('FREEDOM MTG CORP',              'Competitor M',                 'non_bank_fintech',   TRUE),
    ('CITIZENS BK NA',                'Competitor N',                 'bank',               TRUE),
    ('US BK NA',                      'Competitor O',                 'bank',               TRUE),
    ('PNC BK NA',                     'Competitor P',                 'bank',               TRUE),
    ('TRUIST BK',                     'Competitor Q',                 'bank',               TRUE),
    ('FIFTH THIRD BK NA',             'Competitor R',                 'bank',               TRUE),
    ('REGIONS BK',                    'Competitor S',                 'bank',               TRUE),
    ('FLAGSTAR BK FSB',               'Competitor T',                 'bank',               TRUE),
    ('NEW AMERICAN FUNDING',          'Competitor U',                 'non_bank_fintech',   TRUE),
    ('NAVY FCU',                      'Competitor V',                 'credit_union',       TRUE)
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
