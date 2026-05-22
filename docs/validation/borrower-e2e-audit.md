# Module 0 Borrower End-to-End Accuracy Audit

> **Internal implementation artifact. Not approved for public release.**
> Current release evidence must be regenerated from `tools/e2e_borrower_audit.py`
> against the target workspace after each gold refresh.

This checked-in page is no longer a static pass/fail transcript. The audit
tool now derives the state footprint from refreshed gold geography, compares
every sampled field directly, and emits only mismatches observed in the current
run. It does not carry fixed state lists, generic remediation claims, or
pre-declared defects.

Current generator contract:

- Coverage comes from `mip.gold.county_rollup`, with `mip.gold.borrower_360`
  as the data-bearing fallback if the geography rollup is unavailable.
- Sampling is stratified across the current discovered states and opportunity
  score buckets, not a hardcoded demo footprint.
- Gold/API parity is compared directly, including `subject_property`.
- Any active drift must appear as a field-level mismatch in the regenerated
  output.

Run:

```bash
python tools/e2e_borrower_audit.py --sample-size 20 --seed 42
```
