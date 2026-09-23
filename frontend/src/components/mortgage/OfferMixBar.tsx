import { useMemo } from 'react';
import { Link } from 'react-router';
import { EvidenceChip } from '../Primitives';
import { DRAWER_SOURCES } from '../../lib/drawerSources';
import { offerMixSlices } from '../../lib/offerMix';
import type { PortfolioPreview } from '../../types';

/**
 * WHAT TO OFFER — the recommended-offer mix of the addressable book as one
 * stacked bar plus a legend (2026-09-21 audit flow-05). Data is `offer_mix`
 * on the `marketing_eligibility: 'Any'` preview Home already loads: no new
 * request. Widths are exact shares; the printed percents use the
 * largest-remainder method so they add up to 100.
 *
 * Each legend entry opens the Lead Queue filter for that offer. The queue
 * lists the contactable subset and filters by product (coarser than an offer
 * code), so the note under the legend says so rather than implying the
 * queue will show the same count (lib/offerMix.ts has the mapping).
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
          {preview
            ? 'This snapshot carries no recommended-offer mix.'
            : 'The offer mix could not be loaded right now.'}
        </p>
      ) : (
        <>
          <div className="offer-mix" role="img" aria-label={`Recommended-offer mix: ${summary}.`}>
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
            {mix.slices.map((slice) => (
              <li key={slice.code} className="offer-mix__item">
                <span className="offer-mix__swatch" data-offer={slice.code} aria-hidden="true" />
                {slice.href ? (
                  <Link
                    className="offer-mix__label"
                    to={slice.href}
                    title={`Opens ${slice.queueFilter} in the Lead Queue`}
                  >
                    {slice.label}
                  </Link>
                ) : (
                  <span className="offer-mix__label">{slice.label}</span>
                )}
                {/* A real but sub-1% share reads "<1%", never "0%". */}
                <span className="offer-mix__pct num" data-percent={slice.percent}>
                  {slice.percent === 0 ? '<1%' : `${slice.percent}%`}
                </span>
              </li>
            ))}
          </ul>
          <p className="home-answer__note">
            Share of the {mix.total.toLocaleString()} borrowers in the whole book with an offer
            decision. Each offer opens its Lead Queue filter, contactable borrowers only.
          </p>
        </>
      )}
    </section>
  );
}
