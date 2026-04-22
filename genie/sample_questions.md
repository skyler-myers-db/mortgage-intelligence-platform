# Sample Questions — Mortgage Lead Intelligence Genie Space

These questions anchor the Module 0 talk track and are wired into the
Genie Space's "suggested questions" via `tools/databricks/provision_genie_space.py`.
Each one is a realistic ask from a Head of Growth, VP Mortgage Lending,
Marketing Leader, or Sales Manager, and is answerable from the trusted
assets listed in `trusted_assets.md`.

The corpus is scoped to the real Cotality Delta Share footprint:
**6 states (IL, CA, FL, TX, WA, CO), 5.16M property snapshots, 3.1M with
open liens** — per `docs/data-sources-gap-analysis.md §1`. Questions mix
national / multi-state / per-state / per-metro drill-downs so the audience
sees the real geography surface, not a single-metro filter.

The 25 prompts below are grouped into 7 categories that mirror the
shapes a lender will actually throw at the space:

1. **Population sizing** — absolute counts over the addressable market.
2. **Ranked queries** — top-N borrowers / ZIPs / investors.
3. **Segment drill-downs** — segment-level aggregates and breakdowns.
4. **Temporal queries** — "today / yesterday / last 30 days / vs. last week".
5. **Offer + next-best-offer** — product mix + projected savings + NBO recs.
6. **Lock-in cohort** — the sub-3% 2020-2022 retention cohort.
7. **Cross-asset joins** — evidence × segment × scores / lender retention.

Each entry has:

- an expected-answer **skeleton** (shape, not exact numbers — live gold
  drifts with every refresh),
- a **SQL hint** the grader can use to validate the answer's shape
  without depending on an exact value.

---

## Category 1 — Population sizing

1. **How many borrowers across the 6-state footprint are currently in-the-money, and what is the average rate spread?**
   Intent: size the top-of-funnel opportunity for a rate-term refi campaign at national scale.
   Expected skeleton: one row `{count, avg_spread_bps}`. `count` within
   `[1, 4_000_000]`; `avg_spread_bps` within `[1, 500]`.
   SQL hint: `SELECT count(*), avg(rate_spread_bps) FROM mip.gold.lead_scores WHERE in_the_money = true`.
   Source: `mip.gold.lead_scores`, `mip.semantics.lead_generation_metric_view`.

2. **How many borrowers in Illinois are in the money right now?**
   Intent: single-state sizing for IL (the largest share state by count).
   Expected skeleton: one integer ≥ 0, ≤ IL share footprint (~1.86M).
   SQL hint: `WHERE state = 'IL' AND in_the_money = true`.
   Source: `mip.gold.lead_scores`.

3. **How many HELOC candidates have more than 35% equity across the 6-state footprint?**
   Intent: right-size the HELOC campaign based on equity gate.
   Expected skeleton: one integer ≥ 0, ≤ `lead_population` row count.
   SQL hint: `SELECT count(*) FROM mip.gold.borrower_360 WHERE array_contains(segment_codes, 'equity') AND equity_pct > 35`.
   Source: `mip.gold.borrower_360`.

4. **What is the addressable market size — how many eligible borrowers across all six states?**
   Intent: denominator for every funnel metric.
   Expected skeleton: one integer within `[100_000, 4_000_000]`.
   SQL hint: `SELECT count(*) FROM mip.gold.lead_population`.
   Source: `mip.gold.lead_population`, `mip.semantics.lead_generation_metric_view`.

---

## Category 2 — Ranked queries

5. **Show the top 10 borrowers by lead score in Texas.**
   Intent: Lead Queue prioritization for the TX book.
   Expected skeleton: 10 rows, `borrower_id` matches `^B-\d{5}$`,
   `lead_score` descending, values in `[0, 100]`. No PII columns.
   SQL hint: `WHERE state='TX' ORDER BY lead_score DESC LIMIT 10`.
   Source: `mip.gold.lead_scores`, `mip.gold.borrower_360`.

6. **Which 5 ZIP codes have the most in-the-money borrowers across the 6-state footprint?**
   Intent: geographic heatmap anchor; feeds territory planning.
   Expected skeleton: 5 rows, each `{zip_code, count}`, zip is 5-digit.
   SQL hint: `GROUP BY zip_code ORDER BY count(*) DESC LIMIT 5` over `lead_scores`.
   Source: `mip.gold.lead_scores`, `mip.semantics.borrower_opportunity_metric_view`.

7. **Show the top 10 cash-out candidates in Florida by estimated equity.**
   Intent: HELOC / cash-out prioritization in the FL book (0.76M properties, avg rate 4.71%).
   Expected skeleton: 10 rows with `borrower_id` and equity figures.
   SQL hint: `SELECT borrower_id, equity_pct, recommended_offer FROM mip.gold.borrower_360 WHERE state='FL' AND recommended_offer_code IN ('cash_out','heloc','refi_plus_heloc') ORDER BY equity_pct DESC LIMIT 10`.
   Source: `mip.gold.borrower_360`.

8. **Top 20 investors by property count in the Investor/Multi-Property segment.**
   Intent: investor / multi-property prioritization (Owner Link surfaces multi-property).
   Expected skeleton: 20 rows `{borrower_id, related_property_count}`,
   related_property_count ≥ 2.
   SQL hint: `SELECT borrower_id, related_property_count FROM mip.gold.borrower_360 WHERE array_contains(segment_codes, 'investor') ORDER BY related_property_count DESC LIMIT 20`.
   Source: `mip.gold.borrower_360`.

---

## Category 3 — Segment drill-downs

9. **Break down the In-the-Money segment by state.**
   Intent: where is the ITM opportunity concentrated?
   Expected skeleton: ≤ 6 rows, one per state in {IL, CA, FL, TX, WA, CO}.
   SQL hint: `FROM mip.semantics.segment_performance_metric_view WHERE segment='In-the-Money' GROUP BY state`.
   Source: `mip.semantics.segment_performance_metric_view`.

10. **What is the mean rate spread by segment across the 6-state footprint?**
    Intent: is the ITM segment actually above-market on rate?
    Expected skeleton: one row per segment (5 segments); mean rate spread
    a signed float in `[-200, 500]` bps.
    SQL hint: `SELECT segment, avg(rate_spread_bps) GROUP BY segment`.
    Source: `mip.semantics.segment_performance_metric_view`.

11. **Which segments have the highest approval rate?**
    Intent: where is the operational team converting recommendations
    into approved outreach fastest?
    Expected skeleton: segments ranked by approval_rate descending,
    approval_rate in `[0, 1]`.
    SQL hint: `ORDER BY approval_rate DESC` over
    `mip.semantics.segment_performance_metric_view`.
    Source: `mip.semantics.segment_performance_metric_view`.

12. **Compare mean lead score by state across the 6-state share footprint.**
    Intent: geographic heatmap for executive dashboard and campaign
    budget allocation across IL / CA / FL / TX / WA / CO.
    Expected skeleton: six rows, one per state; mean score in `[0, 100]`.
    SQL hint: `FROM mip.semantics.borrower_opportunity_metric_view GROUP BY state`.
    Source: `mip.semantics.borrower_opportunity_metric_view`.

---

## Category 4 — Temporal queries

13. **How many evidence events were recorded yesterday, grouped by trigger type?**
    Intent: operational sanity check — signal freshness and data ingestion health.
    Expected skeleton: one row per trigger type
    (rate-drop, equity-crossed, permit-filed, listed-for-sale, lien-change).
    SQL hint: `WHERE event_ts::date = current_date - interval '1 day' GROUP BY trigger_type`.
    Source: `mip.gold.evidence_events`.

14. **Compare this week's lead score distribution to last week's.**
    Intent: week-over-week trend detection (is scoring drifting?).
    Expected skeleton: at least two date buckets with aggregate stats
    (mean/p50/p90). Numbers in `[0, 100]`.
    SQL hint: `GROUP BY date_trunc('week', scored_at)` over `lead_scores`
    or the funnel metric view.
    Source: `mip.semantics.lead_generation_metric_view`, `mip.gold.lead_scores`.

15. **What is the approval trend over the last 30 days?**
    Intent: are approvers keeping up with the recommendation fire hose?
    Expected skeleton: ≤ 30 daily rows `{date, approvals}` OR a single
    summary with a slope / direction label; all counts ≥ 0.
    SQL hint: `GROUP BY approved_at::date` over
    `mip.semantics.lead_generation_metric_view`.
    Source: `mip.semantics.lead_generation_metric_view`.

16. **How many new evidence events have fired this quarter, grouped by trigger type?**
    Intent: quarter-to-date funnel visibility; what triggered the book
    this quarter?
    Expected skeleton: one row per trigger type; counts ≥ 0.
    SQL hint: `WHERE event_ts >= date_trunc('quarter', current_date) GROUP BY trigger_type`.
    Source: `mip.gold.evidence_events`.

---

## Category 5 — Offer + next-best-offer

17. **What offer mix is recommended for the In-the-Money segment?**
    Intent: before we launch the ITM campaign, what's the NBO blend?
    Expected skeleton: one row per `recommended_offer_code`
    (`refi`, `refi_plus_heloc`, `heloc`, `cash_out`, `purchase`,
    `investor`, `retention`, `nurture`); counts ≥ 0; sum ≤ ITM segment size.
    SQL hint: `SELECT recommended_offer_code, count(*) FROM mip.gold.borrower_360 WHERE array_contains(segment_codes, 'itm') GROUP BY recommended_offer_code`.
    Source: `mip.gold.borrower_360`.

18. **What is the average projected monthly savings for approved refis?**
    Intent: marketing tag-line — "average member saves $X/month on the
    refi we recommended and they approved". Note: a projected-savings
    column is on the roadmap and NOT in any trusted asset today, so the
    honest answer surface is the approval-rate KPI on the segment metric
    view (the "how many of our refi recs turned into approvals?" half of
    the same question). Genie should acknowledge the missing measure and
    pivot to approval_rate, or return principled zero.
    Expected skeleton: either (a) an acknowledgment that
    `projected_monthly_savings_usd` is not a trusted column today, OR
    (b) one row `{approval_rate}` in `[0, 100]` (percent, 2dp per the
    metric view definition) from `segment_performance_metric_view`.
    SQL hint: `SELECT approval_rate FROM mip.semantics.segment_performance_metric_view WHERE segment_code IN ('itm','equity') AND state='_ALL'`.
    Source: `mip.semantics.segment_performance_metric_view`.

19. **Which borrowers got a HELOC recommendation in Florida?**
    Intent: surface the FL HELOC queue for the regional sales lead.
    Expected skeleton: N rows with `borrower_id` matching `^B-\d{5}$`.
    No PII columns. Count bounded by FL HELOC segment size.
    SQL hint: `SELECT borrower_id, recommended_offer FROM mip.gold.borrower_360 WHERE state='FL' AND recommended_offer_code IN ('heloc','refi_plus_heloc')`.
    Source: `mip.gold.borrower_360`.

20. **Break down the Listed-for-Sale segment by loan product and average current rate.**
    Intent: purchase-mortgage opportunity sizing by product mix. Note: MLS data is on
    the Cotality roadmap — this segment returns zero on real data until the MLS product lands.
    Expected skeleton: zero or near-zero rows OR an explicit
    acknowledgment that MLS data is not yet live.
    SQL hint: `SELECT recommended_offer_code, avg(current_rate) FROM mip.gold.borrower_360 WHERE array_contains(segment_codes, 'listed') GROUP BY recommended_offer_code`.
    Source: `mip.gold.borrower_360`, `mip.gold.lead_population`.

---

## Category 6 — Lock-in cohort

21. **How big is the 2020–2022 sub-3% lock-in cohort across all six states?**
    Intent: size the retention + cash-out pool (~669K borrowers in the live
    `mip.gold.lockin_cohort` materialization, per the slice13 refresh on
    2026-04-21), the cohort that will *not* refi but is highly HELOC-shoppable.
    Expected skeleton: one integer in `[500_000, 900_000]` (drift
    tolerance ±30% around 669,320).
    SQL hint: `SELECT count(*) FROM mip.gold.lockin_cohort`.
    Source: `mip.gold.lockin_cohort` (pre-materialised from `silver.lien_current`
    by `sql/transformations/gold_lockin_cohort.sql`).

22. **What is the median rate of the lock-in cohort?**
    Intent: confirm the cohort really is sub-3% as the label claims
    (sanity check on the gold materialization).
    Expected skeleton: one row `{median_rate_pct}`; value in `[1.0, 3.5]`
    (allows a small halo above 3% for post-2022 top-up cases).
    SQL hint: `SELECT percentile(first_pos_rate, 0.5) FROM mip.gold.lockin_cohort`.
    Source: `mip.gold.lockin_cohort`.

23. **Break down the lock-in cohort by state.**
    Intent: regional concentration of the sub-3% cohort — where do we
    pivot to a HELOC / cash-out pitch instead of rate-and-term?
    Expected skeleton: ≤ 6 rows, one per state; counts ≥ 0; sum equals
    the total cohort size from Q21.
    SQL hint: `GROUP BY state` over `mip.gold.lockin_cohort`.
    Source: `mip.gold.lockin_cohort`.

---

## Category 7 — Cross-asset joins

24. **Which borrowers on our retention list have a competitor lien filed in the last 30 days?**
    Intent: recapture — catch refinance-to-competitor before it closes. Servicer-transferred
    pool is 263K across the share per gap-analysis §1.
    Expected skeleton: N rows of `borrower_id` matching `^B-\d{5}$`; no
    PII columns; every row must have a matching evidence event with
    `signal_type='competitor_lien'`.
    SQL hint: `SELECT DISTINCT b.borrower_id FROM mip.gold.borrower_360 b JOIN mip.gold.evidence_events e USING (clip) WHERE array_contains(b.segment_codes, 'retention') AND e.signal_type='competitor_lien' AND e.timestamp >= CAST(current_date - interval 30 days AS STRING)`.
    Source: `mip.gold.borrower_360`, `mip.gold.evidence_events`.

25. **Which borrowers have both a permit signal and an equity-crossing event in the last 30 days?**
    Intent: "intent + ability" double-signal cohort — permit filed says
    they're renovating, equity-crossed says they have the headroom to
    finance it.
    Expected skeleton: N rows of `borrower_id`; N ≥ 0 (permit dataset
    may be gap-empty, which is honest — this is a cross-asset JOIN, not
    a single-asset lookup, so zero is acceptable).
    SQL hint: self-JOIN on `evidence_events` filtered to
    `trigger_type IN ('permit-filed','equity-crossed')` grouped by
    borrower_id with `HAVING count(distinct trigger_type) = 2`.
    Source: `mip.gold.evidence_events`.
