import type { PortfolioPreview } from '../types';
import { leadQueueHref } from './homeAnswer';
import { offerDisplayLabel } from './offerLanguage';

/**
 * Home's WHAT OFFER column: the share of each recommended offer code in the
 * addressable book (`offer_mix` on the `marketing_eligibility: 'Any'` preview
 * Home already loads), plus the Lead Queue filter that opens each offer.
 *
 * Reconciliation (2026-09-21 audit, the ~23x addressable-vs-contactable gap):
 * the bar is the WHOLE book; the queue it links to lists only the contactable
 * subset, and its `product` filter is coarser than an offer code. So every
 * link names the queue filter it applies instead of implying the count.
 */

export type OfferMixRow = NonNullable<PortfolioPreview['offer_mix']>[number];
export type OfferCode = OfferMixRow['offer_code'];

interface QueueTarget {
  href: string;
  /** The queue filter the link applies, in the queue's own words. */
  filter: string;
}

const product = (label: string, filter: string): QueueTarget => ({
  href: leadQueueHref({ product: label }),
  filter,
});

/**
 * The Lead Queue's `product` filter compiles a reviewed label to a SET of
 * offer codes (backend `PORTFOLIO_PRODUCT_CODES`, pinned by
 * tests/unit/test_genie_sql_floor_extraction.py): Refi = {refi,
 * refi_plus_heloc}, HELOC = {heloc, refi_plus_heloc}, and Cash-out, Purchase
 * and Retention map one-to-one. There is no investor product and no queue for
 * "Monitor for later", which recommends NO outreach, so it links nowhere.
 */
const QUEUE_TARGET: Record<OfferCode, QueueTarget | null> = {
  refi: product('Refi', 'Refi offers, including refinance + home-equity'),
  refi_plus_heloc: product('Refi', 'Refi offers, including refinance-only'),
  heloc: product('HELOC', 'HELOC offers, including refinance + home-equity'),
  cash_out: product('Cash-out', 'Cash-out offers'),
  purchase: product('Purchase', 'Purchase offers'),
  retention: product('Retention', 'Retention offers'),
  investor: { href: leadQueueHref({ segment: 'investor' }), filter: 'the Investor / Multi-Property segment' },
  nurture: null,
};

export interface OfferMixSlice {
  code: OfferCode;
  label: string;
  count: number;
  /** Exact share of the mix total, 0-100 (drives the bar width). */
  share: number;
  /** Whole-number percent; the slices' percents sum to exactly 100. */
  percent: number;
  href: string | null;
  queueFilter: string | null;
}

export interface OfferMix {
  slices: OfferMixSlice[];
  /** Borrowers with an offer code: the bar's 100%. */
  total: number;
}

/**
 * Slices ordered by size (ties by code, so the order is stable), zero-count
 * codes dropped. Percents use the largest-remainder method so the labels a
 * reader adds up always make 100.
 */
export function offerMixSlices(mix: readonly OfferMixRow[] | null | undefined): OfferMix {
  const source: readonly OfferMixRow[] = mix ?? [];
  const rows = source.filter(
    (row) => Number.isFinite(row.borrower_count) && row.borrower_count > 0,
  );
  const total = rows.reduce((sum, row) => sum + row.borrower_count, 0);
  if (total <= 0) return { slices: [], total: 0 };

  const ordered = [...rows].sort(
    (a, b) => b.borrower_count - a.borrower_count || a.offer_code.localeCompare(b.offer_code),
  );
  const exact = ordered.map((row) => (row.borrower_count / total) * 100);
  const percents = exact.map(Math.floor);
  let remaining = 100 - percents.reduce((sum, value) => sum + value, 0);
  const byRemainder = exact
    .map((value, index) => ({ index, remainder: value - Math.floor(value) }))
    .sort((a, b) => b.remainder - a.remainder || a.index - b.index);
  for (const { index } of byRemainder) {
    if (remaining <= 0) break;
    percents[index] += 1;
    remaining -= 1;
  }

  const slices = ordered.map((row, index) => {
    const target = QUEUE_TARGET[row.offer_code] ?? null;
    return {
      code: row.offer_code,
      label: offerDisplayLabel(row.offer_code),
      count: row.borrower_count,
      share: exact[index],
      percent: percents[index],
      href: target?.href ?? null,
      queueFilter: target?.filter ?? null,
    };
  });
  return { slices, total };
}
