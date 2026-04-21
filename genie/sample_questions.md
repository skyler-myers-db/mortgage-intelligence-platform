# Sample Questions — Mortgage Lead Intelligence Genie Space

These questions anchor the Module 0 DAIS talk track and are wired into the
Genie Space's "suggested questions" via `tools/databricks/provision_genie_space.py`.
Each one is a realistic ask from a Head of Growth, VP Mortgage Lending,
Marketing Leader, or Sales Manager, and is answerable from the trusted
assets listed in `trusted_assets.md`.

1. **How many borrowers are currently in-the-money, and what is the average rate spread?**
   Intent: size the top-of-funnel opportunity for a rate-term refi campaign.
   Source: `mip_demo.gold.lead_scores`, `mip_demo.semantics.lead_generation_metric_view`.

2. **Which five ZIP codes have the highest count of HELOC/cash-out candidates?**
   Intent: territory planning for equity-based offers.
   Source: `mip_demo.gold.lead_segment_membership`, `mip_demo.semantics.segment_performance_metric_view`.

3. **Show me the top 20 borrowers by lead score who also had a permit filed in the last 90 days.**
   Intent: permit-triggered outreach list for cash-out renovation offers.
   Source: `mip_demo.gold.lead_scores`, `mip_demo.gold.evidence_events`.

4. **What is the conversion rate from recommended-offer to approved-offer by segment this quarter?**
   Intent: segment-level funnel health and where to invest next.
   Source: `mip_demo.gold.recommended_offers`, `mip_demo.semantics.segment_performance_metric_view`.

5. **Which borrowers on our retention list have a competitor lien filed in the last 30 days?**
   Intent: recapture — catch refinance-to-competitor before it closes.
   Source: `mip_demo.gold.lead_segment_membership`, `mip_demo.gold.evidence_events`.

6. **Break down the Listed-for-Sale segment by loan product and average current rate.**
   Intent: purchase-mortgage opportunity sizing by product mix.
   Source: `mip_demo.gold.lead_population`, `mip_demo.gold.lead_segment_membership`.

7. **For investors with three or more properties, which have the strongest cash-out potential?**
   Intent: investor/multi-property segment prioritization.
   Source: `mip_demo.gold.borrower_360`, `mip_demo.gold.recommended_offers`.

8. **Compare mean lead score by MSA for our top five markets.**
   Intent: geographic heatmap for executive dashboard and campaign budget.
   Source: `mip_demo.semantics.borrower_opportunity_metric_view`.

9. **How many evidence events were recorded yesterday, grouped by trigger type?**
   Intent: operational sanity check — signal freshness and data ingestion health.
   Source: `mip_demo.gold.evidence_events`.

10. **Of the offers approved last week, what was the average projected monthly savings?**
    Intent: demonstrable ROI figure for the Head of Growth readout.
    Source: `mip_demo.gold.recommended_offers`, approval log in Lakebase (`mip_app.approvals`).
