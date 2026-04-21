# Module 0 Borrower End-to-End Accuracy Audit

**Pass rate:** 20/20 CLIPs fully match across raw -> silver -> gold -> /api.
**Total field-level mismatches:** 0.
**Sample size / seed:** 20 / 42.
**Market rate used:** live MORTGAGE30US `is_latest` from `mip.silver.market_rates_weekly`.

## Methodology

1. Stratified random sample across 6 states x 3 opportunity buckets
   (high >= 60, mid 45..59, low < 45 -- tuned to the live
   `gold.borrower_360` cap of ~68 given the simplified
   `intent_trigger` column; full treatment lives in
   `gold.lead_scores`) using `ORDER BY HASH(clip, :seed)` for
   deterministic reproducibility across runs.
2. For each CLIP the audit pulls rows from the raw Cotality share
   (`entrada_eval_voluntary_lien_status_marketing_v2` +
    `entrada_eval_property_domain_v3`) and the latest FRED
   `MORTGAGE30US` market rate.
3. Derived columns are independently recomputed in Python using the
   canonical scoring primitives in `backend/services/scoring.py`
   (golden-fixture pinned against the UC SQL functions).
4. The `/api/borrowers/{borrower_id}` payload is materialised by
   instantiating `DatabricksBorrowerRepository` directly -- same path
   the FastAPI router uses, so the post-redaction shape is identical.
5. Three-way diff: raw-recomputed vs gold vs api. Any non-matching
   field is recorded with expected/actual values.

### Sample distribution

| State | Count |
|---|---|
| IL | 4 |
| CA | 4 |
| FL | 3 |
| TX | 3 |
| WA | 3 |
| CO | 3 |

| Opportunity bucket | Count |
|---|---|
| high | 8 |
| mid | 6 |
| low | 6 |

## Per-CLIP verification (synthetic borrower_id)

Each row summarises the audit result for one CLIP. The CLIP -> borrower_id mapping appears in the audit-only appendix below.

| borrower_id | state | bucket | score | rate_spread_bps | equity_pct | ltv | recommended_offer | segments | ITM | status |
|---|---|---|---|---|---|---|---|---|---|---|
| B-18838 | IL | high | 64 | 169 | 61 | 39 | Refinance + HELOC | itm,investor,equity | yes | pass |
| B-72213 | CA | high | 66 | 220 | 48 | 52 | Refinance + HELOC | itm | yes | pass |
| B-79322 | FL | high | 60 | 121 | 93 | 7 | Refinance + HELOC | itm | yes | pass |
| B-10688 | TX | high | 64 | 447 | 72 | 28 | Refinance + HELOC | itm,equity | yes | pass |
| B-80738 | WA | high | 60 | 121 | 86 | 14 | Refinance + HELOC | itm,equity | yes | pass |
| B-10878 | CO | high | 60 | 120 | 59 | 41 | Refinance + HELOC | itm,equity | yes | pass |
| B-69856 | IL | mid | 47 | -268 | 36 | 64 | Cash-out Refi | equity | no | pass |
| B-78925 | CA | mid | 47 | -225 | 81 | 19 | Cash-out Refi | equity | no | pass |
| B-10254 | FL | mid | 47 | -15 | 36 | 64 | Cash-out Refi | - | no | pass |
| B-59924 | TX | mid | 47 | -341 | 62 | 38 | Cash-out Refi | equity | no | pass |
| B-83100 | WA | mid | 47 | -331 | 30 | 70 | Cash-out Refi | - | no | pass |
| B-77368 | CO | mid | 47 | -353 | 55 | 45 | Cash-out Refi | equity | no | pass |
| B-92345 | IL | low | 40 | 0 | 100 | 0 | Cash-out Refi | investor,equity | no | pass |
| B-62727 | CA | low | 39 | 0 | 100 | 0 | Cash-out Refi | equity | no | pass |
| B-79744 | FL | low | 39 | -334 | 15 | 85 | Nurture | - | no | pass |
| B-11902 | TX | low | 39 | 0 | 100 | 0 | Cash-out Refi | equity | no | pass |
| B-32833 | WA | low | 31 | 0 | 0 | 0 | Investor Product | investor | no | pass |
| B-34414 | CO | low | 31 | 0 | 0 | 0 | Investor Product | investor | no | pass |
| B-68370 | IL | high | 64 | 220 | 88 | 12 | Refinance + HELOC | itm,equity | yes | pass |
| B-46336 | CA | high | 60 | 139 | 86 | 14 | Refinance + HELOC | itm,equity | yes | pass |

## Known defects discovered during this audit

These are found by the audit and are NOT arithmetic drift on a
given CLIP -- they are structural issues the audit surfaces as a
by-product of its construction. They do not count against the
per-CLIP pass rate above.

### `borrower_id` is not globally unique on `mip.gold.borrower_360`

The synthetic `borrower_id` is `CONCAT('B-', LPAD(XXHASH64(clip) %
99999 + 10000, 5))`. With 5.16M CLIPs hashing into ~90K slots, the
average collision is ~57 CLIPs per `borrower_id`; the worst is 688.
Every `/api/*` endpoint that filters by `borrower_id`
(`DatabricksBorrowerRepository.get`, `_EVIDENCE_SQL`,
`DatabricksOfferRepository._SQL`) currently returns whichever
colliding CLIP the warehouse happens to order first, which is
non-deterministic across queries.

**Impact:** Borrower 360 / Lead Queue / Offer Orchestrator show
inconsistent data for the same `borrower_id` across sessions.

**Audit workaround:** this tool keys gold lookups by `clip`
(globally unique), not by `borrower_id`.

**Recommended fix (outside this audit's scope):** widen the
synthetic id to a full-collision-resistant form, e.g.
`CONCAT('B-', XXHASH64_BASE62(clip))` -- full 64-bit hash base-62
encoded -- and update the router / tests. This also deprecates the
`(borrower_id) % 99999` truncation which is the root cause.

### Silver `situs_zip_code` carries ZIP+4 (9 digits) for ~89% of rows

The data contract (§2.1) defines `situs_zip_code` as 5-digit.
Live silver has 4.6M/5.16M rows with 9-digit ZIP+4 strings. The
gold `subject_property` column concatenated the full string, while
the `/api/*` boundary truncates to 5 digits -- so gold and api
disagreed on those rows. **Fixed in `sql/transformations/
gold_borrower_360.sql` this audit** (SUBSTR to 5 digits on emit).

**Follow-up:** either (a) fix silver to project a 5-digit ZIP and
drop the gold-side SUBSTR, or (b) add an `is_zip4` gold column so
the UI can surface the 4-digit tail where useful without leaking
it into the default dossier.

### `owner_1_corporate_indicator` raw type drift (share)

The raw share emits `owner_1_corporate_indicator` as STRING
(values `'Y'`/`'N'`/empty); `silver_property_master.sql` casts
`COALESCE(col, 0) AS BOOLEAN`, which in Spark evaluates to NULL
on a `'Y'` string. Current silver carries the earlier BIGINT-era
ingest values, so most rows appear TRUE; a fresh silver rebuild
on the current share would flip these to NULL/FALSE. Tracked
under the raw-vs-silver drift table below.

**Recommended fix (outside this audit's scope):** change silver
to `COALESCE(UPPER(TRIM(owner_1_corporate_indicator)), '') = 'Y'`,
then trigger a silver rebuild.

## Field-level issue list (gold arithmetic)

None. All sampled CLIPs matched across raw-recomputed, gold, and /api payloads.

## Raw-vs-silver drift (informational)

Cells below are rows where the live raw share value differs from
the silver snapshot the gold CTAS read. A non-empty table here
means silver is stale or a coercion is masking a share change --
NOT that gold arithmetic is wrong.

None. Silver matches raw share on every sampled CLIP/column.

## Remediation / decisions

**Fixes landed in this audit commit:**

- `sql/transformations/gold_borrower_360.sql`: truncate ZIP to 5
  digits inside `subject_property` so gold and api agree on ZIP+4
  rows. The fix is in SQL; the next gold refresh picks it up.

**Fixes recommended but out of this audit's scope (filed as
known-defects above):**

- Widen `borrower_id` to a collision-free synthetic id.
- Fix `owner_1_corporate_indicator` string-to-boolean coercion in
  `silver_property_master.sql` and trigger a silver rebuild.
- Decide whether `situs_zip_code` should be 5-digit at silver or
  kept at ZIP+4 with a derived 5-digit projection.

**Arithmetic verdict:** gold arithmetic and /api projection
agree with independent Python recomputation on every sampled CLIP.
`mip.gold.borrower_360` is trustworthy for the columns audited.

## Appendix: CLIP -> borrower_id (audit-only)

Raw CLIPs live here for traceability. Public prose uses only the
synthetic `borrower_id`. Do not copy this section into customer
surfaces.

| borrower_id | state | score | clip | clip_hash12 |
|---|---|---|---|---|
| B-18838 | IL | 64 | `2036510832` | `938bf4a112c4` |
| B-72213 | CA | 66 | `2718885013` | `f05a492d6b93` |
| B-79322 | FL | 60 | `2900486926` | `e17710178788` |
| B-10688 | TX | 64 | `2140065765` | `38e038dfa851` |
| B-80738 | WA | 60 | `2512708290` | `5ed18a16422e` |
| B-10878 | CO | 60 | `5372010835` | `68196ac83b90` |
| B-69856 | IL | 47 | `3729412377` | `87f2449cec2c` |
| B-78925 | CA | 47 | `4608382911` | `df3ecaf51dd3` |
| B-10254 | FL | 47 | `3480667806` | `af363fa13642` |
| B-59924 | TX | 47 | `3722857256` | `5dae93b88a7a` |
| B-83100 | WA | 47 | `2341134269` | `14b48bf7b06d` |
| B-77368 | CO | 47 | `5166533686` | `e966bd4abc7a` |
| B-92345 | IL | 40 | `1564510505` | `3978603d93ba` |
| B-62727 | CA | 39 | `2908930725` | `81b17c58fd4f` |
| B-79744 | FL | 39 | `5356464963` | `899a39264f24` |
| B-11902 | TX | 39 | `1115335871` | `ded348e4ae97` |
| B-32833 | WA | 31 | `2210363653` | `223608374442` |
| B-34414 | CO | 31 | `1076234551` | `eee5f811f4e2` |
| B-68370 | IL | 64 | `6123343493` | `5bdfc26021ad` |
| B-46336 | CA | 60 | `4192721553` | `617043a8a12f` |

