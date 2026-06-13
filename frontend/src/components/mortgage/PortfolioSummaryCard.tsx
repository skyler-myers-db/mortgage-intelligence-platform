import { useMemo } from 'react';
import type { PortfolioPreview } from '../../types';
import { Icon } from '../Icon';
import { useApp } from '../AppContext';
import { buildPortfolioStory, type PortfolioClaimSourceKey } from '../../lib/portfolioStory';
import { DRAWER_SOURCES } from '../../lib/drawerSources';

/**
 * "Your book today" — a current-state portfolio summary on Home. A plain-
 * English orientation of the lender's book RIGHT NOW, composed deterministically
 * from the PortfolioPreview already loaded on the page (no new request, no live
 * AI call → instant, never fails at the booth). Every figure is routed through
 * the same numeric-claims verifier as the borrower "Tell the story" narrative,
 * so the card only badges itself "traces to the gold snapshot" when each number
 * is grounded — it is NOT, and is not labelled as, a live AI generation. Each
 * grounded figure links to its gold-table source drawer.
 */

const SOURCE_FOR: Record<PortfolioClaimSourceKey, (typeof DRAWER_SOURCES)[keyof typeof DRAWER_SOURCES]> = {
  population: DRAWER_SOURCES.population,
  itm: DRAWER_SOURCES.itm,
  leadScore: DRAWER_SOURCES.leadScore,
  nbo: DRAWER_SOURCES.nbo,
};

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
  const { setDrawer } = useApp();
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
              Plain-English summary of your current gold snapshot{asOf ? ` · as of ${asOf}` : ''}.
            </div>
          </div>
        </div>
      </div>
      <div className="surface__body">
        <p className="portfolio-summary__narrative">{story.sentences.join(' ')}</p>

        <div className="portfolio-summary__claims" aria-label="Figures grounded in the gold snapshot">
          {story.claims.map((claim) => {
            const src = SOURCE_FOR[claim.sourceKey];
            return (
              <button
                key={`${claim.label}-${claim.token}`}
                type="button"
                className={`portfolio-summary__claim${
                  claim.verified ? '' : ' portfolio-summary__claim--unverified'
                }`}
                onClick={() => setDrawer(src)}
                title={`${claim.label}: ${claim.token} — open ${src.title} evidence`}
              >
                <Icon name={claim.verified ? 'check' : 'cross'} size={9} />
                <span className="portfolio-summary__claim-label">{claim.label}</span>
                <span className="portfolio-summary__claim-token mono">{claim.token}</span>
              </button>
            );
          })}
        </div>

        <div
          className={`portfolio-summary__verdict${
            story.allVerified ? ' portfolio-summary__verdict--ok' : ' portfolio-summary__verdict--warn'
          }`}
          role="status"
        >
          <Icon name={story.allVerified ? 'shield' : 'info'} size={11} />
          {story.allVerified
            ? 'Every figure traces to the current gold snapshot.'
            : 'Some figures could not be verified against the snapshot — review before presenting.'}
        </div>
      </div>
    </section>
  );
}
