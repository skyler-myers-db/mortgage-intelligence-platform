import { Link } from 'react-router-dom';
import type { PortfolioPreview } from '../../types';
import { Icon } from '../Icon';
import { buildBriefing, formatBriefingDelta } from '../../lib/morningBriefing';

/**
 * MorningBriefing (re-audit Buyer-Wow #6) — the daily standing artifact on
 * Home. Summarizes the day-over-day movement the portfolio preview already
 * carries (preview.trends), with each metric's direction and any governance
 * caveat (a rules/step-change note) surfaced honestly. Pure presentational;
 * no network of its own.
 */
export function MorningBriefing({ preview, loading }: { preview: PortfolioPreview | null; loading?: boolean }) {
  if (loading) {
    return (
      <section className="briefing" aria-busy="true" aria-label="Morning briefing loading">
        <div className="briefing__hdr">
          <div className="briefing__ico"><Icon name="bell" size={15} /></div>
          <div className="skeleton briefing__headline-skeleton" aria-hidden="true" />
        </div>
      </section>
    );
  }

  const briefing = buildBriefing(preview);

  if (!briefing.available) {
    return (
      <section className="briefing" role="status" aria-label="Morning briefing">
        <div className="briefing__hdr">
          <div className="briefing__ico"><Icon name="bell" size={15} /></div>
          <div>
            <div className="briefing__eyebrow">Morning briefing</div>
            <div className="briefing__headline muted">{briefing.headline}</div>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="briefing" aria-label="Morning briefing">
      <div className="briefing__hdr">
        <div className="briefing__ico"><Icon name="bell" size={15} /></div>
        <div className="briefing__hdr-main">
          <div className="briefing__eyebrow">Morning briefing</div>
          <div className="briefing__headline">{briefing.headline}</div>
        </div>
        <Link className="briefing__cta" to="/lead-queue">
          Review queue
          <Icon name="chevright" size={13} />
        </Link>
      </div>
      <div className="briefing__grid">
        {briefing.movements.map((mv) => (
          <div key={mv.key} className="briefing__item">
            <div className="briefing__item-label">{mv.label}</div>
            <div className="briefing__item-row">
              <span className="briefing__item-value num">
                {mv.value === null ? '—' : mv.value.toLocaleString()}
              </span>
              <span className={`briefing__delta briefing__delta--${mv.direction}`}>
                <Icon
                  name={mv.direction === 'up' ? 'up' : mv.direction === 'down' ? 'down' : 'chevright'}
                  size={10}
                />
                {formatBriefingDelta(mv.deltaPct)}
              </span>
            </div>
            {mv.note && (
              <div className="briefing__note" role="note">
                <Icon name="shield" size={9} /> {mv.note}
              </div>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
