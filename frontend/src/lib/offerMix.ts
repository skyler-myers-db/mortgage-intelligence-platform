import type { PortfolioPreview } from '../types';
import { leadQueueHref } from './homeAnswer';
import { offerDisplayLabel } from './offerLanguage';

/**
 * Home's WHAT OFFER column: the share of each ACTIONABLE recommended offer
 * code in the addressable book (`offer_mix` on the `marketing_eligibility:
 * 'Any'` preview Home already loads), plus the Lead Queue filter that opens
 * each offer.
 *
 * Reconciliation with the rest of Home: the bar's 100% is the population the
 * "Primary offer paths" KPI and the briefing count, `offers_recommended`,
 * which excludes "Monitor for later" (`nurture`;
 * sql/metric_views/portfolio_headline_metric_view.sql `offer_recommended`).
 * `nurture` is no offer, it is the decision NOT to reach out, and on a live
 * book it is well over 90% of the offer decisions: left in the bar it would
 * answer "with what offer?" with "none" and total a number no KPI shows. It
 * is returned separately (`monitorCount`) so the note can state it.
 *
 * The 2026-09-21 audit's ~23x addressable-vs-contactable gap: the bar is the
 * WHOLE book; the queue it links to lists only the contactable subset, and
 * its `product` filter is coarser than an offer code. So every link names the
 * queue filter it applies instead of implying the count.
 */

export type OfferMixRow = NonNullable<PortfolioPreview['offer_mix']>[number];
export type OfferCode = OfferMixRow['offer_code'];
/** The offer decision that recommends no outreach; never a bar segment. */
export const MONITOR_OFFER_CODE = 'nurture' satisfies OfferCode;
export type ActionableOfferCode = Exclude<OfferCode, typeof MONITOR_OFFER_CODE>;

/** Legend rows before the remaining offers fold into one "Also" line, so the
 * column's height stays bounded whatever the book's offer vocabulary holds. */
export const OFFER_MIX_LEGEND_ROWS = 4;

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
 * and Retention map one-to-one. There is no investor product, so Investor
 * opens its segment.
 */
const QUEUE_TARGET: Record<ActionableOfferCode, QueueTarget> = {
  refi: product('Refi', 'Refi offers, including refinance + home-equity'),
  refi_plus_heloc: product('Refi', 'Refi offers, including refinance-only'),
  heloc: product('HELOC', 'HELOC offers, including refinance + home-equity'),
  cash_out: product('Cash-out', 'Cash-out offers'),
  purchase: product('Purchase', 'Purchase offers'),
  retention: product('Retention', 'Retention offers'),
  investor: { href: leadQueueHref({ segment: 'investor' }), filter: 'the Investor / Multi-Property segment' },
};

function isActionable(code: OfferCode): code is ActionableOfferCode {
  return code !== MONITOR_OFFER_CODE && Object.prototype.hasOwnProperty.call(QUEUE_TARGET, code);
}

export interface OfferMixSlice {
  code: ActionableOfferCode;
  label: string;
  count: number;
  /** Exact share of the mix total, 0-100 (drives the bar width). */
  share: number;
  /** Whole-number percent; the slices' percents sum to exactly 100. */
  percent: number;
  href: string;
  queueFilter: string;
}

export interface OfferMix {
  slices: OfferMixSlice[];
  /** Borrowers with an actionable offer (a primary offer path): the bar's 100%. */
  total: number;
  /** Borrowers on "Monitor for later": an offer decision, but no offer. */
  monitorCount: number;
}

const counted = (row: OfferMixRow): boolean =>
  Number.isFinite(row.borrower_count) && row.borrower_count > 0;

/**
 * Actionable slices ordered by size (ties by code, so the order is stable),
 * zero-count codes dropped, "Monitor for later" counted apart. Percents use
 * the largest-remainder method so the labels a reader adds up always make 100.
 */
export function offerMixSlices(mix: readonly OfferMixRow[] | null | undefined): OfferMix {
  const source: readonly OfferMixRow[] = mix ?? [];
  const monitorCount = source
    .filter((row) => row.offer_code === MONITOR_OFFER_CODE && counted(row))
    .reduce((sum, row) => sum + row.borrower_count, 0);
  const rows = source.filter(
    (row): row is OfferMixRow & { offer_code: ActionableOfferCode } =>
      isActionable(row.offer_code) && counted(row),
  );
  const total = rows.reduce((sum, row) => sum + row.borrower_count, 0);
  if (total <= 0) return { slices: [], total: 0, monitorCount };

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
    const target = QUEUE_TARGET[row.offer_code];
    return {
      code: row.offer_code,
      label: offerDisplayLabel(row.offer_code),
      count: row.borrower_count,
      share: exact[index],
      percent: percents[index],
      href: target.href,
      queueFilter: target.filter,
    };
  });
  return { slices, total, monitorCount };
}

/** A whole percent as printed: a real but sub-1% share reads "<1%", never "0%". */
export function offerPercentText(percent: number): string {
  return percent === 0 ? '<1%' : `${percent}%`;
}
