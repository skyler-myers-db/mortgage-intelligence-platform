# Sample Questions — Mortgage Lead Intelligence Genie Space

These questions anchor the Module 0 talk track and are wired into the
Genie Space's "suggested questions" via `tools/databricks/provision_genie_space.py`.
Each one is a realistic ask from a Head of Growth, VP Mortgage Lending,
Marketing Leader, or Sales Manager, and is answerable from the trusted
assets listed in `trusted_assets.md`.

The corpus is scoped to the current Cotality data coverage discovered from
trusted gold assets. Questions should use "current Cotality data coverage" or
"current coverage states" rather than hardcoding county/state counts; the
coverage can expand or contract as Cotality shares are connected.

The 30 prompts below are grouped into 8 categories that mirror the
shapes a lender will actually throw at the space:

1. **Population sizing** — absolute counts over borrower coverage and the lead queue.
2. **Ranked queries** — top-N borrowers / ZIPs / investors.
3. **Segment drill-downs** — segment-level aggregates and breakdowns.
4. **Temporal queries** — "today / yesterday / last 30 days / vs. last week".
5. **Offer + next-best-offer** — product mix + projected savings + NBO recs.
6. **Lock-in cohort** — the sub-3% 2020-2022 retention cohort.
7. **Cross-asset joins** — evidence × segment × scores / lender retention.
8. **Sales operations** — LO assignment, aging, dispositions, and conversion.

Each entry has:

- an expected-answer **skeleton** (shape, not exact numbers — live gold
  drifts with every refresh),
- a **SQL hint** the grader can use to validate the answer's shape
  without depending on an exact value.

---

## Category 1 — Population sizing

1. **How many borrowers across the current Cotality data coverage are currently in-the-money, and what is the average rate spread?**
   Intent: size the top-of-funnel opportunity for a rate-term refi campaign at national scale.
   Expected skeleton: one row `{count, avg_spread_bps}`. `count` within
   `[1, 4_000_000]`; `avg_spread_bps` within `[1, 500]`.
   SQL hint: `SELECT count(*), avg(rate_spread_bps) FROM mip.gold.lead_scores WHERE in_the_money = true`.
   Source: `mip.gold.lead_scores`, `mip.semantics.lead_generation_metric_view`.

2. **Break down in-the-money borrowers by current coverage state; which state leads?**
   Intent: state-level sizing without assuming a fixed footprint.
   Expected skeleton: one row per refreshed state, ordered by borrower count descending.
   SQL hint: `WHERE in_the_money = true GROUP BY state ORDER BY count(*) DESC`.
   Source: `mip.gold.lead_scores`.

3. **How many borrowers have at least 35% modeled equity across the current Cotality data coverage?**
   Intent: size the equity-capacity pool before applying HELOC intent or campaign filters.
   Expected skeleton: one integer ≥ 0, ≤ `borrower_360` row count.
   (The count is over the current Cotality data coverage — `borrower_360` — not the
   score-filtered `lead_population` subset. Equity-segment membership
   is orthogonal to the `opportunity_score >= 50` gate.)
   SQL hint: `SELECT count(*) FROM mip.gold.borrower_360 WHERE equity_pct >= 35`.
   Source: `mip.gold.borrower_360`.

4. **What is the addressable market size — how many eligible borrowers across the current Cotality data coverage?**
   Intent: Portfolio Builder denominator for the default marketable borrower population.
   Expected skeleton: one integer ≥ 0 and ≤ `borrower_360` row count.
   SQL hint: `SELECT count(*) FROM mip.gold.borrower_360 WHERE marketing_eligible = TRUE AND is_owner_occupied = TRUE AND current_lien_balance > 0 AND COALESCE(second_pos_amount, 0) = 0 AND equity_pct >= 15`.
   Source: `mip.gold.borrower_360`.
   Note: `mip.gold.lead_population` is the narrower ranked Lead Queue subset,
   not the Portfolio Builder addressable-market denominator.

---

## Category 2 — Ranked queries

5. **Show the top 10 borrowers by lead score across the current Cotality data coverage.**
   Intent: Lead Queue prioritization across the active coverage scope.
   Expected skeleton: 10 rows, `borrower_id` matches `^B-[0-9A-Z]{13}$`,
   `opportunity_score` descending, values in `[0, 100]`. No PII columns.
   SQL hint: `ORDER BY opportunity_score DESC LIMIT 10`.
   Source: `mip.gold.lead_scores`, `mip.gold.borrower_360`.

6. **Which 5 ZIP codes have the most in-the-money borrowers across the current Cotality data coverage?**
   Intent: geographic heatmap anchor; feeds territory planning.
   Expected skeleton: 5 rows, each `{zip, count}`, zip is 5-digit text.
   SQL hint: `SELECT zip, count(*) AS borrowers FROM mip.gold.borrower_360 WHERE in_the_money = true AND zip IS NOT NULL AND LENGTH(zip) = 5 GROUP BY zip ORDER BY borrowers DESC LIMIT 5`.
   Source: `mip.gold.borrower_360`.

7. **Show the top 10 cash-out candidates by estimated equity across the current Cotality data coverage.**
   Intent: HELOC / cash-out prioritization in the active coverage scope.
   Expected skeleton: 10 rows with `borrower_id` and `equity_estimate` (USD); ordered by `equity_estimate DESC`.
   (Uses the USD column — `equity_estimate` — to match "estimated
   equity" in the prompt. `equity_pct` is the percent alternative and
   would produce a different top-10 ranking.)
   SQL hint: `SELECT borrower_id, equity_estimate, recommended_offer FROM mip.gold.borrower_360 WHERE recommended_offer_code IN ('cash_out','heloc','refi_plus_heloc') ORDER BY equity_estimate DESC LIMIT 10`.
   Source: `mip.gold.borrower_360`.

8. **Show the top 20 masked borrower IDs in the Investor/Multi-Property segment by related property count.**
   Intent: investor / multi-property prioritization (Owner Link surfaces multi-property).
   Expected skeleton: 20 rows `{borrower_id, related_property_count}`,
   related_property_count ≥ 2.
   SQL hint: `SELECT borrower_id, related_property_count FROM mip.gold.borrower_360 WHERE array_contains(segment_codes, 'investor') ORDER BY related_property_count DESC LIMIT 20`.
   Source: `mip.gold.borrower_360`.

---

## Category 3 — Segment drill-downs

9. **Break down the In-the-Money segment by state.**
   Intent: where is the ITM opportunity concentrated?
   Expected skeleton: one row per refreshed source state with non-zero ITM count; no fixed state list.
   SQL hint: `SELECT state, count FROM mip.semantics.segment_performance_metric_view WHERE segment_code = 'itm' AND state <> '_ALL' AND count > 0 ORDER BY count DESC`.
   Source: `mip.semantics.segment_performance_metric_view`.

10. **What is the mean rate spread by segment across the current Cotality data coverage?**
    Intent: is the ITM segment actually above-market on rate?
    Expected skeleton: one row per live emitted segment; mean rate spread a
    signed float in `[-200, 500]` bps. Pending-source segments may be absent
    until their source predicates can emit rows.
    SQL hint: `SELECT sc AS segment_code, avg(rate_spread_bps) AS avg_rate_spread_bps FROM mip.gold.borrower_360 LATERAL VIEW explode(segment_codes) s AS sc GROUP BY sc`.
    Source: `mip.gold.borrower_360`.

11. **Which segments have the highest approval rate?**
    Intent: where is the operational team converting recommendations
    into approved outreach fastest?
    Expected skeleton: segments ranked by approval_rate descending,
    approval_rate as a percent in `[0, 100]`.
    SQL hint: `WHERE state = '_ALL' ORDER BY approval_rate DESC` over
    `mip.semantics.segment_performance_metric_view`.
    Source: `mip.semantics.segment_performance_metric_view`.

12. **Compare mean lead score by current coverage state.**
    Intent: geographic heatmap for executive dashboard and campaign
    budget allocation across the refreshed coverage states.
    Expected skeleton: one row per refreshed state; mean score in `[0, 100]`.
    SQL hint: `FROM mip.semantics.borrower_opportunity_metric_view GROUP BY state`.
    Source: `mip.semantics.borrower_opportunity_metric_view`.

---

## Category 4 — Temporal queries

13. **How many evidence events were recorded yesterday, grouped by trigger type?**
    Intent: operational sanity check — signal freshness and data ingestion health.
    Expected skeleton: one row per live trigger type currently emitted by
    `mip.gold.evidence_events`; listing, HELOC propensity, and refi propensity
    rows are live when present. Do not expect filed-permit rows until source
    readiness marks Building Permits live.
    SQL hint: ``WHERE to_date(`timestamp`) = current_date - interval '1 day' GROUP BY signal_type``.
    Source: `mip.gold.evidence_events`.

14. **Compare this week's lead score distribution to last week's.**
    Intent: week-over-week trend detection (is scoring drifting?).
    Expected skeleton: week buckets with average opportunity score from
    the daily funnel snapshots. If fewer than two weeks exist, say the
    historical snapshot window is not long enough yet; do not invent p50/p90.
    SQL hint: `SELECT date_trunc('week', snapshot_date) AS week_start, AVG(avg_opportunity_score) AS avg_opportunity_score FROM mip.gold.funnel_snapshot_daily WHERE state = '_ALL' AND segment_code = '_ALL' GROUP BY week_start ORDER BY week_start`.
    Source: `mip.gold.funnel_snapshot_daily`.

15. **What is the approval trend over the last 30 days?**
    Intent: are approvers keeping up with the recommendation fire hose?
    Expected skeleton: ≤ 30 daily rows `{date, approvals}` OR a single
    summary with a slope / direction label; all counts ≥ 0.
    SQL hint: `SELECT snapshot_date, approved_borrowers AS approvals
    FROM mip.gold.funnel_snapshot_daily WHERE state = '_ALL' AND segment_code = '_ALL'
    AND snapshot_date >= current_date() - INTERVAL 30 DAYS
    ORDER BY snapshot_date`.
    Source: `mip.gold.funnel_snapshot_daily`.

16. **How many new evidence events have fired this quarter, grouped by trigger type?**
    Intent: quarter-to-date funnel visibility; what triggered the book
    this quarter?
    Expected skeleton: one row per trigger type; counts ≥ 0.
    SQL hint: ``WHERE to_timestamp(`timestamp`) >= date_trunc('quarter', current_date) GROUP BY signal_type``.
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

18. **Which trusted asset contains projected monthly savings for approved refis?**
    Intent: marketing tag-line — "average member saves $X/month on the
    refi we recommended and they approved". Note: a projected-savings
    column is on the roadmap and NOT in any trusted asset today. Genie
    should acknowledge the missing measure and must not substitute
    approval_rate or any other proxy as projected savings.
    Expected skeleton: acknowledgment that `projected_monthly_savings_usd`
    is not a trusted column today; no fabricated savings value.
    SQL hint: none; this is a metadata/data-gap answer.
    Source: trusted asset inventory / governed schema.

19. **Which borrowers got a HELOC recommendation across the current Cotality data coverage?**
    Intent: surface the HELOC queue for the sales lead without assuming a fixed state.
    Expected skeleton: N rows with `borrower_id` matching `^B-[0-9A-Z]{13}$`.
    No PII columns. Count bounded by current HELOC recommendation volume.
    SQL hint: `SELECT borrower_id, recommended_offer FROM mip.gold.borrower_360 WHERE recommended_offer_code IN ('heloc','refi_plus_heloc')`.
    Source: `mip.gold.borrower_360`.

20. **Break down the Listed-for-Sale segment by loan product and average current rate.**
    Intent: purchase-mortgage opportunity sizing by product mix from live MLS listing rows.
    Expected skeleton: grouped rows for listed borrowers with loan product/current-rate context.
    SQL hint: `SELECT first_pos_loan_type, COUNT(*) AS listed_borrowers, ROUND(AVG(current_rate), 2) AS avg_current_rate FROM mip.gold.borrower_360 WHERE listed_for_sale = TRUE GROUP BY first_pos_loan_type ORDER BY listed_borrowers DESC`.
    Source: `mip.gold.borrower_360`.

---

## Category 6 — Lock-in cohort

21. **How big is the 2020–2022 sub-3% lock-in cohort across the current Cotality data coverage?**
    Intent: size the retention + cash-out pool from the current refreshed
    `mip.gold.lockin_cohort` materialization, the cohort that will *not* refi
    but is highly HELOC-shoppable.
    Expected skeleton: one non-negative integer equal to the canonical SQL
    count at answer time; no stale snapshot counts.
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
    Expected skeleton: one row per refreshed source state; counts ≥ 0; sum
    equals the total cohort size from Q21.
    SQL hint: `GROUP BY state` over `mip.gold.lockin_cohort`.
    Source: `mip.gold.lockin_cohort`.

---

## Category 7 — Cross-asset joins

24. **Which borrowers on our retention list have a competitor lien filed in the last 30 days?**
    Intent: recapture — inspect current competitor-lien evidence when that
    source is live; if it is not live, return a source-readiness data-gap
    answer instead of a zero-demand answer.
    Expected skeleton: N rows of `borrower_id` matching `^B-[0-9A-Z]{13}$`; no
    PII columns; every row must have a matching evidence event with
    `signal_type='competitor_lien'`.
    SQL hint: ``SELECT DISTINCT b.borrower_id FROM mip.gold.borrower_360 b JOIN mip.gold.evidence_events e USING (clip) WHERE array_contains(b.segment_codes, 'retention') AND e.signal_type='competitor_lien' AND to_timestamp(e.`timestamp`) >= current_timestamp() - interval 30 days``.
    (`evidence_events.timestamp` is an ISO-8601 STRING per the DDL, not a
    TIMESTAMP. `to_timestamp(...)` parses it before comparing; otherwise
    Spark implicitly casts the STRING to DATE and yields NULL on any
    `YYYY-MM-DDTHH:MM:SSZ` value, silently returning zero rows.)
    Source: `mip.gold.borrower_360`, `mip.gold.evidence_events`.

25. **Which borrowers have both a permit signal and an equity-crossing event in the last 30 days?**
    Intent: "intent + ability" double-signal cohort — permit filed says
    they're renovating, equity-crossed says they have the headroom to
    finance it.
    Expected skeleton: explicit acknowledgment that permit data is not
    yet live; no SQL answer that treats blocked-false permit flags as
    zero renovation demand.
    SQL hint: none until `mip.gold.source_readiness` reports Building
    Permits live.
    Source: `mip.gold.source_readiness`.

---

## Category 8 — Sales operations

Sales operations questions are routed through the governed backend Sales Ops
adapter because LO assignment and call dispositions live in Lakebase
`mip_app.*`, which the Databricks Genie space itself must not query directly.
The response still includes executable SQL and proof, but the source is
reported as governed Sales Ops state rather than a Unity Catalog asset.

26. **Which LO had the highest application-start rate this week?**
    Intent: Sam's Friday close-out — identify the strongest converter and
    coaching benchmark.
    Expected skeleton: one top LO plus a table of `{lo_email, calls_attempted,
    applications_started, application_start_rate}`. If no dispositions are
    logged this week, say so instead of inventing a conversion rate.
    SQL hint: aggregate `mip_app.call_dispositions` from week start by
    `lo_email`, using `outcome = 'application_started'` as the numerator.
    Source: governed Sales Ops adapter over `mip_app.call_dispositions`.

27. **How many calls did each LO make yesterday?**
    Intent: 8:30 standup activity readout.
    Expected skeleton: one row per LO/outcome or LO-level rollup with nonnegative
    counts. No borrower identifiers.
    SQL hint: `WHERE occurred_at >= current_date - interval '1 day' AND occurred_at < current_date GROUP BY lo_email, outcome`.
    Source: governed Sales Ops adapter over `mip_app.call_dispositions`.

28. **Show approved leads that have not been touched in 7 days.**
    Intent: stale-lead triage for manager follow-up.
    Expected skeleton: up to 10 masked borrower IDs with `age_days`, plus a link
    hint to Lead Queue filters `approval_status=approved&outreach_status=queued&aged_days=7`.
    SQL hint: latest approval is `approve`, no LO disposition, decided at least
    7 days ago.
    Source: governed Sales Ops adapter over `mip_app.approvals` and
    `mip_app.call_dispositions`.

29. **Top borrowers in an LO queue ranked by aging and score.**
    Intent: daily call-list prioritization for a named LO.
    Expected skeleton: route to Lead Queue with the selected loan officer,
    approved status, and queued outreach filters; otherwise ask which loan
    officer to review. No names, phone numbers, or street addresses.
    SQL hint: operational assignment comes from `mip_app.lead_assignments`;
    borrower score comes from `mip.gold.borrower_360` through the app API.
    Source: governed Sales Ops adapter plus Lead Queue.

30. **How many leads went from approved to application started this week?**
    Intent: bridge Vera's approval gate to Sam's LO execution funnel.
    Expected skeleton: count of `application_started` dispositions this week,
    optionally grouped by LO.
    SQL hint: `COUNT(*) FILTER (WHERE outcome = 'application_started')` over
    `mip_app.call_dispositions` for the current week.
    Source: governed Sales Ops adapter over `mip_app.call_dispositions`.
