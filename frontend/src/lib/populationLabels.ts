/**
 * Canonical copy for the two borrower-population cuts Module 0 counts.
 *
 * They are NOT synonyms and the difference is ~5.16M vs ~76K:
 *
 *   ADDRESSABLE  — the whole reachable book. `COUNT(*)` over the headline
 *                  metric view with `marketing_eligibility: 'Any'` (Home's
 *                  `HOME_PORTFOLIO_PREVIEW_CRITERIA`), i.e. NO contactability
 *                  gate. Suppressed and DNC borrowers are still counted.
 *   MARKETABLE   — the contact-eligible subset: the same count with
 *                  `marketing_eligibility: 'Eligible only'` pushed down
 *                  (Portfolio Builder's default), so DNC / suppressed
 *                  borrowers are excluded.
 *
 * The 2026-08-07 data audit renamed the Home + Portfolio surfaces after the
 * addressable count shipped under the marketable word; the Analytics
 * approval-funnel tab was missed and kept headlining 5,156,184 as
 * "Marketable population". Pin the strings HERE and import them so the next
 * surface can't drift on its own — the API field is still
 * `marketable_population` in both cases, so the field name is no guide.
 */

/** No contactability gate — the whole reachable book. */
export const ADDRESSABLE_POPULATION_KPI_LABEL = 'Addressable population';

/** Contactability gate applied — the contact-eligible subset. */
export const MARKETABLE_POPULATION_KPI_LABEL = 'Marketable population';

/**
 * Which population a count represents, given the CONTACTABILITY criterion
 * that produced it. Anything other than "Eligible only" (Any / Suppressed
 * only) leaves ineligible borrowers in the count, so it is addressable.
 */
export function populationKpiLabel(marketingEligibility: string | undefined): string {
  return marketingEligibility === 'Eligible only'
    ? MARKETABLE_POPULATION_KPI_LABEL
    : ADDRESSABLE_POPULATION_KPI_LABEL;
}
