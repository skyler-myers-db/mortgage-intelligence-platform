---
name: permit-segment-is-heloc-propensity
description: The 'permit' segment_code does NOT mean filed building permits — it equals has_heloc_propensity_trigger
metadata:
  type: project
---

The `'permit'` segment_code in gold is the backward-compatible code for "HELOC Intent", NOT filed building permits.

**Fact:** In `sql/transformations/gold_borrower_360.sql:533-534`, segment_codes gets `'permit'` when `(s.has_permit OR s.has_heloc_propensity_trigger)`. Since `has_permit` is the literal `CAST(FALSE AS BOOLEAN)` (line 447, no permit source exists yet), `'permit'` segment membership is exactly `has_heloc_propensity_trigger` (heloc_propensity_score >= 700). `gold_segment_population.sql:175` labels code `'permit'` as "HELOC Intent". The `'equity'` segment (line 537) is `equity_pct >= heloc_equity_min_applied (35) AND COALESCE(second_pos_amount,0)=0`.

**Why:** Filed Building Permits feed is pending; Cotality HELOC propensity is integrated as a separate model signal under the legacy `permit` code to avoid claiming a permit was filed. See [[slice13-accuracy]] context for the has_permit=FALSE honesty posture.

**How to apply:** When auditing any code that screens `has_heloc_propensity_trigger` and routes to `segment_codes=permit`, the two ARE consistent — `permit` is the HELOC-propensity segment, not an always-empty set. Do NOT flag "permit is empty so HELOC borrowers are dropped" — that is false. The real reconciliation gap is the equity-pct threshold mismatch (broad `equity_pct>=35` vs actionable equity segment needing clean 2nd-lien too) and the borrower_360-vs-lead_population base population (lead_population is filtered to opportunity_score>=50).
