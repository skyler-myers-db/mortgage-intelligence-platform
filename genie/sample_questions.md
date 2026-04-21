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

1. **How many borrowers across the 6-state footprint are currently in-the-money, and what is the average rate spread?**
   Intent: size the top-of-funnel opportunity for a rate-term refi campaign at national scale.
   Source: `mip.gold.lead_scores`, `mip.semantics.lead_generation_metric_view`.

2. **How many borrowers in Chicago are in the money right now, and which ZIPs concentrate them?**
   Intent: metro drill — Chicago is the recommended anchor per `docs/data-contract-module0.md §10`
   (1.86M IL properties, highest avg 1st-position rate at 4.75%).
   Source: `mip.gold.lead_scores`, `mip.gold.borrower_360`.

3. **Which ZIP codes in California have the highest refi pool?**
   Intent: territory planning in the largest AVM-equity state (avg CLTV 30.6%).
   Source: `mip.gold.lead_population`, `mip.semantics.lead_generation_metric_view`.

4. **Show the top 10 cash-out candidates in Florida by estimated equity.**
   Intent: HELOC / cash-out prioritization in the FL book (0.76M properties, avg rate 4.71%).
   Source: `mip.gold.borrower_360`, `mip.gold.recommended_offers`.

5. **How big is the 2020–2022 sub-3% lock-in cohort across all six states?**
   Intent: size the retention + cash-out pool (1.22M borrowers per gap-analysis §1),
   the cohort that will *not* refi but is highly HELOC-shoppable.
   Source: `mip.silver.lien_current`, `mip.semantics.borrower_opportunity_metric_view`.

6. **Which borrowers on our retention list have a competitor lien filed in the last 30 days?**
   Intent: recapture — catch refinance-to-competitor before it closes. Servicer-transferred
   pool is 263K across the share per gap-analysis §1.
   Source: `mip.gold.lead_segment_membership`, `mip.gold.evidence_events`.

7. **Break down the Listed-for-Sale segment by loan product and average current rate.**
   Intent: purchase-mortgage opportunity sizing by product mix. Note: MLS data is on
   the Cotality roadmap — this segment returns zero on real data until the MLS product lands.
   Source: `mip.gold.lead_population`, `mip.gold.lead_segment_membership`.

8. **For investors with two or more properties in Texas, which have the strongest cash-out potential?**
   Intent: investor / multi-property segment prioritization in the absentee-mailings-heavy TX book.
   Source: `mip.gold.borrower_360`, `mip.gold.recommended_offers`.

9. **Compare mean lead score by state across the 6-state share footprint.**
   Intent: geographic heatmap for executive dashboard and campaign budget allocation
   across IL / CA / FL / TX / WA / CO.
   Source: `mip.semantics.borrower_opportunity_metric_view`.

10. **How many evidence events were recorded yesterday, grouped by trigger type?**
    Intent: operational sanity check — signal freshness and data ingestion health.
    Source: `mip.gold.evidence_events`.
