import type { DrawerSource } from '../components/AppContext';
import type { KpiProofKey } from './apiTypes';
import { DRAWER_SOURCES, drawerForAsset } from './drawerSources';

/**
 * Evidence for the four Analytics Executive KPI cards (2026-09-21 audit
 * flow-06 phase 1-2, D-audit-reads-c1; W5e w5-analytics item 8): mirrors
 * lib/approvalFunnelDrawerSource. Each card's source spreads the existing
 * registry entry for the asset its number is computed from and adds a
 * one-sentence `definition`, shown under "How we got {value}". The cards
 * carried no source chip before, so "the evidence drawer opens from every
 * KPI" was unmet on this tab.
 *
 * `proofKey` is the server's reproduce statement for the UNFILTERED book
 * (GET /api/kpi-proof, audit-free fixed text). A card with any tab filter
 * active gets no evidence prop at all (KpiCard contract: a filtered count
 * never borrows an unfiltered proof).
 */
export type ExecutiveKpi = 'addressable' | 'inTheMoney' | 'primaryOfferPaths' | 'approved';

/** Approved Outreach reads the gold lifecycle mirror (the funnel snapshot's lineage today). */
const LIFECYCLE_MIRROR = drawerForAsset('mip.gold.borrower_lifecycle_state') ?? DRAWER_SOURCES.funnelSnapshot;

export const EXECUTIVE_KPI_SOURCES: Readonly<Record<ExecutiveKpi, DrawerSource>> = {
  addressable: {
    ...DRAWER_SOURCES.population,
    definition: 'Every borrower in the addressable book at this refresh, before the contact-eligibility gate.',
  },
  inTheMoney: {
    ...DRAWER_SOURCES.itm,
    definition: 'Borrowers whose rate spread and equity clear the governed In the Money rule (mip.gold.fn_in_the_money).',
  },
  primaryOfferPaths: {
    ...DRAWER_SOURCES.nbo,
    definition: 'Borrowers whose governed next-best-offer path is a primary offer rather than nurture.',
  },
  approved: {
    ...LIFECYCLE_MIRROR,
    definition: 'Borrowers with an approved outreach decision in the gold lifecycle mirror of the Lakebase approvals.',
  },
};

/** The server reproduce key of each card's number (the unfiltered book only). */
export const EXECUTIVE_KPI_PROOF_KEYS: Readonly<Record<ExecutiveKpi, KpiProofKey>> = {
  addressable: 'analytics.addressable',
  inTheMoney: 'analytics.in_the_money',
  primaryOfferPaths: 'analytics.primary_offer_paths',
  approved: 'analytics.approved',
};

/**
 * A card's `evidence` prop: the as-of and proof of the unfiltered book, or
 * none at all while a tab filter narrows the count.
 */
export function executiveKpiEvidence(
  kpi: ExecutiveKpi,
  asOf: string | null,
  filtersActive: boolean,
): { asOf: string | null; proofKey: KpiProofKey } | undefined {
  return filtersActive ? undefined : { asOf, proofKey: EXECUTIVE_KPI_PROOF_KEYS[kpi] };
}
