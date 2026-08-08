---
name: population-labels-are-not-synonyms
description: "Addressable" and "marketable" name two different counts (~5.16M vs ~76K) that share one API field name — check the CONTACTABILITY criterion before labelling either.
metadata:
  type: project
---

`PortfolioPreview.marketable_population` is the field name for BOTH counts, so
the payload is no guide to which one a surface is showing. The predicate is:

- **Addressable** — `marketing_eligibility: 'Any'` (Home's
  `HOME_PORTFOLIO_PREVIEW_CRITERIA`). No contactability gate; suppressed and DNC
  borrowers included. Live ~5,156,184.
- **Marketable** — `marketing_eligibility: 'Eligible only'` (Portfolio Builder's
  default). The governed gate is applied: `marketing_eligible = TRUE`,
  `consent_status = 'opt_in'`, no `suppression_reason`, not `dnc`, past the
  recontact date, outside the frequency cap (`backend/services/eligibility.py::
  eligible_sql_predicate`). Live ~76,487.

**Why:** the 2026-08-07 data audit renamed Home and Portfolio Builder after the
addressable count shipped under the marketable word, and the 2026-08-08 UX walk
found two surfaces still mismatched — the Analytics approval-funnel tab
(headline) and the Portfolio Builder KPI (evidence chip said "Addressable" on a
gated number). A 68x difference described by the wrong word is a credibility
defect, not a copy nit.

**How to apply:** import the strings from `frontend/src/lib/populationLabels.ts`
(`ADDRESSABLE_POPULATION_KPI_LABEL`, `MARKETABLE_POPULATION_KPI_LABEL`,
`populationKpiLabel(criterion)`); never retype them. Evidence chips have two
drawer sources — `DRAWER_SOURCES.population` (addressable) and
`DRAWER_SOURCES.populationMarketable` (gated) — and the headline and chip must be
resolved from the SAME criterion. The backend still labels the funnel's first
stage "Marketable population"; the frontend copy deliberately wins, via
`funnelStageDisplayLabel` in `lib/approvalFunnelDrawerSource.ts`.
