import { useMemo } from 'react';
import type { PortfolioPreview } from '../../types';
import { Icon } from '../Icon';
import { buildPortfolioStory } from '../../lib/portfolioStory';

/**
 * "Your book today" — a current-state portfolio summary on Home. A plain-
 * English orientation of the lender's book RIGHT NOW, composed deterministically
 * from the PortfolioPreview already loaded on the page (no new request, no live
 * AI call → instant, never fails at the booth). The narrative's figures are
 * routed through the same numeric-claims verifier as the borrower "Tell the
 * story" narrative, so the card flags itself when a number can't be grounded.
 * The per-figure evidence links live on the Home KPI cards above (each KpiCard
 * carries its gold-table source drawer), so this summary does not restate them.
 */

function formatAsOf(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export function PortfolioSummaryCard({
  preview,
  loading = false,
}: {
  preview: PortfolioPreview | null;
  loading?: boolean;
}) {
  const story = useMemo(() => buildPortfolioStory(preview), [preview]);

  if (loading) {
    return (
      <section className="surface portfolio-summary" aria-busy="true" aria-label="Portfolio summary loading">
        <div className="surface__hdr surface__hdr--split">
          <div className="surface__hdr-main">
            <div className="surface__icon"><Icon name="sparkle" size={14} /></div>
            <div className="h-4">Your book today</div>
          </div>
        </div>
        <div className="surface__body">
          <span className="skeleton portfolio-summary__narrative-skeleton" aria-hidden="true" />
        </div>
      </section>
    );
  }

  if (!story.available) return null;
  const asOf = formatAsOf(story.asOf);

  return (
    <section className="surface portfolio-summary" aria-label="Portfolio summary">
      <div className="surface__hdr surface__hdr--split">
        <div className="surface__hdr-main">
          <div className="surface__icon"><Icon name="sparkle" size={14} /></div>
          <div>
            <div className="h-4">Your book today</div>
            <div className="muted fs-12">
              Summary of your current gold snapshot{asOf ? ` · as of ${asOf}` : ''}.
            </div>
          </div>
        </div>
      </div>
      <div className="surface__body">
        <p className="portfolio-summary__narrative">{story.sentences.join(' ')}</p>

        {/* Only the honest caveat renders — the ambient "every figure traces…"
            reassurance was noise (the KPI cards above already carry evidence). */}
        {!story.allVerified && (
          <div className="portfolio-summary__verdict portfolio-summary__verdict--warn" role="status">
            <Icon name="info" size={11} />
            Some figures could not be verified against the snapshot — review before presenting.
          </div>
        )}
      </div>
    </section>
  );
}
