import { Fragment, useMemo } from 'react';
import { Link } from 'react-router';
import { EvidenceChip } from '../Primitives';
import { DRAWER_SOURCES } from '../../lib/drawerSources';
import {
  OFFER_MIX_LEGEND_ROWS,
  offerMixSlices,
  offerPercentText,
  type OfferMixSlice,
} from '../../lib/offerMix';
import type { PortfolioPreview } from '../../types';

/**
 * WHAT TO OFFER — the mix of primary offer paths in the addressable book as
 * one stacked bar plus a legend (2026-09-21 audit flow-05). Data is
 * `offer_mix` on the `marketing_eligibility: 'Any'` preview Home already
 * loads: no new request. Widths are exact shares; the printed percents use
 * the largest-remainder method so they add up to 100.
 *
 * The bar's 100% is the "Primary offer paths" KPI (`offers_recommended`):
 * "Monitor for later" recommends no outreach, so it is stated in the note,
 * never drawn as an offer (lib/offerMix.ts has the reconciliation).
 *
 * The largest OFFER_MIX_LEGEND_ROWS offers get a legend row; the rest share
 * one "Also" line, so the answer band keeps its height (and the map its
 * place above the fold) however many offer codes a book carries.
 *
 * Each offer opens the Lead Queue filter for that offer. The queue lists the
 * contactable subset and filters by product (coarser than an offer code), so
 * the note says so rather than implying the queue will show the same count.
 */
export function OfferMixBar({
  preview,
  loading = false,
}: {
  preview: PortfolioPreview | null;
  loading?: boolean;
}) {
  const mix = useMemo(() => offerMixSlices(preview?.offer_mix), [preview]);
  const summary = mix.slices
    .map((slice) => `${slice.label} ${slice.percent === 0 ? 'under 1' : slice.percent}%`)
    .join(', ');
  const rows = mix.slices.slice(0, OFFER_MIX_LEGEND_ROWS);
  const more = mix.slices.slice(OFFER_MIX_LEGEND_ROWS);
  // The KPI's own number when the preview carries it: both count the same
  // statement's `offer_recommended` rows (backend databricks_portfolio.py).
  const offerPaths =
    typeof preview?.offers_recommended === 'number' && Number.isFinite(preview.offers_recommended)
      ? preview.offers_recommended
      : mix.total;

  return (
    <section
      className="home-answer__col home-answer__col--offer"
      aria-labelledby="home-answer-offer"
      aria-busy={loading || undefined}
    >
      <div className="home-answer__col-hdr">
        <h3 className="h-4" id="home-answer-offer">What to offer</h3>
        <EvidenceChip source={DRAWER_SOURCES.nbo}>Offer rules</EvidenceChip>
      </div>
      {loading ? (
        <span className="skeleton offer-mix__skeleton" aria-hidden="true" />
      ) : mix.slices.length === 0 ? (
        <p className="home-answer__empty" role="status">
          {!preview
            ? 'The offer mix could not be loaded right now.'
            : mix.monitorCount > 0
              ? `No borrower has a primary offer path in this snapshot; ${mix.monitorCount.toLocaleString()} are on Monitor for later.`
              : 'This snapshot carries no recommended-offer mix.'}
        </p>
      ) : (
        <>
          <div className="offer-mix" role="img" aria-label={`Primary offer mix: ${summary}.`}>
            {mix.slices.map((slice) => (
              <span
                key={slice.code}
                className="offer-mix__seg"
                data-offer={slice.code}
                data-share={slice.share.toFixed(4)}
                style={{ inlineSize: `${slice.share}%` }}
              />
            ))}
          </div>
          <ul className="offer-mix__legend">
            {rows.map((slice) => (
              <li key={slice.code} className="offer-mix__item">
                <span className="offer-mix__swatch" data-offer={slice.code} aria-hidden="true" />
                <OfferLink slice={slice} className="offer-mix__label" />
                <OfferPercent slice={slice} />
              </li>
            ))}
          </ul>
          {more.length > 0 && (
            <p className="offer-mix__more">
              Also:{' '}
              {more.map((slice, index) => (
                <Fragment key={slice.code}>
                  {index > 0 && ', '}
                  <span className="offer-mix__more-item">
                    <span className="offer-mix__swatch" data-offer={slice.code} aria-hidden="true" />
                    <OfferLink slice={slice} className="offer-mix__more-label" />{' '}
                    <OfferPercent slice={slice} />
                  </span>
                </Fragment>
              ))}
            </p>
          )}
          <p className="home-answer__note">
            Share of the {offerPaths.toLocaleString()} borrowers with a primary offer path
            {mix.monitorCount > 0
              ? `; ${mix.monitorCount.toLocaleString()} more are on Monitor for later.`
              : '.'}{' '}
            Links open the contactable subset.
          </p>
        </>
      )}
    </section>
  );
}

function OfferLink({ slice, className }: { slice: OfferMixSlice; className: string }) {
  return (
    <Link className={className} to={slice.href} title={`Opens ${slice.queueFilter} in the Lead Queue`}>
      {slice.label}
    </Link>
  );
}

function OfferPercent({ slice }: { slice: OfferMixSlice }) {
  return (
    <span className="offer-mix__pct num" data-percent={slice.percent}>
      {offerPercentText(slice.percent)}
    </span>
  );
}
