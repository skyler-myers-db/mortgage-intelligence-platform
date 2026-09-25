import type { ReactNode } from 'react';
import { Link } from 'react-router';
import type { DrawerSource } from '../AppContext';
import { EvidenceChip } from '../Primitives';
import { api } from '../../lib/api';
import {
  HOME_WHO_COUNT,
  borrowerPlace,
  borrowerQueueHref,
  homeTopBorrowers,
  leadQueueHref,
} from '../../lib/homeAnswer';
import { offerDisplayLabel } from '../../lib/offerLanguage';
import { scoreBand } from '../../lib/opportunityScore';
import { queryKeys } from '../../lib/queryKeys';
import { useWarmingUpRetry } from '../../lib/useWarmingUpRetry';
import type { EconomicsAnalyticsResponse } from '../../types';

/**
 * WHO to contact: the first five rows of the governed canonical ranking.
 *
 * Source is `GET /api/analytics/economics` `top_borrowers` — opportunity
 * score >= 50 plus the contact-eligibility predicate, ordered by score, then
 * rate spread, then borrower id (databricks_analytics.py). The Lead Queue
 * breaks ties at the top score differently (`rank_overall` is a DENSE_RANK
 * on score, then CLIP), so the copy says "ranked by opportunity score", not
 * "the queue's top five". It is an audit-free aggregate read. Home does not
 * read `GET /api/leads` instead: that read writes a VIEW_LEADS audit row per
 * call, and Home must never audit on render (TopLeadsQuickPick, which does
 * read /api/leads, stays on the Borrower 360 / Offer empty states where a
 * click-through is the intent).
 *
 * The rows carry no segment codes, so each row's chip is its recommended
 * offer, not a segment.
 */

/** Analytics' unfiltered economics key (routes/analytics.tsx `baseCriteria`),
 * so Home and the Economics tab share one cache entry. */
export const HOME_ECONOMICS_CRITERIA = ['all', 'all', 'All', 'all'] as const;

export function requestHomeEconomics(signal?: AbortSignal): Promise<EconomicsAnalyticsResponse> {
  return api.analyticsEconomics(signal);
}

const WHO_SOURCE: DrawerSource = {
  title: 'Top-ranked contactable borrowers',
  short: 'Governed ranking',
  lineageFamily: 'lead_queue_rank',
  assetKey: 'borrower_360',
  assetPath: 'mip.gold.borrower_360',
  description:
    'The governed canonical ranking: contact-eligible borrowers (eligibility, consent, suppression, '
    + 'do-not-contact and recontact gates) with an opportunity score of 50 or more, ordered by '
    + 'opportunity score, then rate spread.',
  signals: [
    { label: 'Floor', source: 'borrower_360.opportunity_score', value: '>= 50' },
    { label: 'Eligibility gate', source: 'borrower_360.marketing_eligible', value: 'TRUE' },
    { label: 'Consent', source: 'borrower_360.consent_status', value: 'opt_in' },
    { label: 'Order', source: 'opportunity_score, rate_spread_bps', value: 'descending' },
  ],
};

export function HomeAnswerWho() {
  const { data, warmingUp, error } = useWarmingUpRetry<EconomicsAnalyticsResponse>(
    requestHomeEconomics,
    { queryKey: queryKeys.analytics('economics', HOME_ECONOMICS_CRITERIA), staleTime: 60_000 },
  );
  const rows = homeTopBorrowers(data?.top_borrowers);
  const loading = !data && !error;

  let body: ReactNode;
  if (loading) {
    body = (
      <ol className="home-answer__who" aria-hidden="true">
        {Array.from({ length: HOME_WHO_COUNT }, (_, index) => (
          <li key={index}><span className="skeleton home-answer__row-skeleton" /></li>
        ))}
      </ol>
    );
  } else if (rows.length === 0) {
    body = (
      <p className="home-answer__empty" role="status">
        {error
          ? 'The ranked borrowers could not be loaded right now.'
          : 'No contactable borrower clears the opportunity-score floor yet.'}{' '}
        <Link to={leadQueueHref()}>Open the Lead Queue</Link>
      </p>
    );
  } else {
    body = (
      <ol className="home-answer__who">
        {rows.map((row) => {
          const band = scoreBand(row.opportunity_score);
          const offer = offerDisplayLabel(null, row.recommended_offer);
          const place = borrowerPlace(row);
          return (
            <li key={row.borrower_id}>
              <Link className="home-answer__who-row" to={borrowerQueueHref(row.borrower_id)}>
                <span className="home-answer__rank num" aria-hidden="true">{row.rank_overall}</span>
                <span className="home-answer__who-id">{row.borrower_id}</span>
                <span className="home-answer__who-place" title={place}>{place}</span>
                <span className="chip chip--neutral" title={offer}>
                  <span className="chip__label">{offer}</span>
                </span>
                <span className={`score score--${band}`}>
                  <span className="score__dot" />
                  <span className="sr-only">Opportunity score </span>
                  {row.opportunity_score}
                </span>
              </Link>
            </li>
          );
        })}
      </ol>
    );
  }

  return (
    <section
      className="home-answer__col home-answer__col--who"
      aria-labelledby="home-answer-who"
      aria-busy={loading || undefined}
    >
      <div className="home-answer__col-hdr">
        <h3 className="h-4" id="home-answer-who">Who to contact</h3>
        <EvidenceChip source={WHO_SOURCE}>{WHO_SOURCE.short}</EvidenceChip>
      </div>
      {body}
      <p className="home-answer__note">
        {warmingUp
          ? 'Warehouse warming up; the ranking loads on its own.'
          : 'Contactable borrowers only, ranked by opportunity score.'}
      </p>
    </section>
  );
}
