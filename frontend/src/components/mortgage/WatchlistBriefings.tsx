import { Link } from 'react-router';
import { Icon } from '../Icon';
import { Chip, SurfaceTitle } from '../Primitives';
import { AsyncState } from '../ui/AsyncState';
import { Timestamp } from '../ui/Timestamp';
import { Sparkline } from './Sparkline';
import { growthAgentWatchlistsApi } from '../../lib/apiClients/growthAgentWatchlists';
import { SCHEDULED_RUN_STATUS } from '../../lib/growthAgentSchedulerCopy';
import { formatCount, formatFixed, signedCount } from '../../lib/formatters';
import { queryKeys } from '../../lib/queryKeys';
import { useWarmingUpRetry } from '../../lib/useWarmingUpRetry';
import type { GrowthAgentWatchlistBriefing, GrowthAgentWatchlistSummaryResponse } from '../../types/growthAgent';
import './WatchlistBriefings.css';

/**
 * WatchlistBriefings — Home's "Watchlist briefings" card (audit 2026-09-21
 * `wow-ai-4`): each saved Growth Agent watchlist as one row with its
 * actionable count, the change since its previous run, the average-score
 * change, a sparkline of its recent runs, its run age and its status.
 *
 * Read-only by construction: one audit-free GET /api/growth-agent/monitors/
 * summary on load (lazy chunk, apiClients/growthAgentWatchlists), never on
 * hover, prefetch or poll, and the card never POSTs, so loading Home never
 * starts a run. The header says honestly whether scheduled runs are on (the
 * scheduler job's real state, SCHEDULED_RUN_STATUS), because a watchlist only
 * refreshes when someone runs it or the scheduler does.
 *
 * deviation:watchlist-briefings: a declared extension in the prototype's
 * RightRail `.surface` / `.surface__hdr` pattern (design_files/Module 0
 * Prototype.html:2193-2201); block `.watchlist-briefings` lays out the rows.
 */

/** Rows the card lists before "+N more watchlists". */
export const WATCHLIST_BRIEFING_ROWS = 4;
const WORKFLOWS_HREF = '/ask-genie?tab=workflows';

/** The average-score change, signed to one decimal ("+2.3", "-0.4", "0.0"), or '—'. */
function signedScore(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return '—';
  const text = formatFixed(Math.abs(value), 1);
  return text === '0.0' ? text : `${value > 0 ? '+' : '-'}${text}`;
}

export default function WatchlistBriefings() {
  const query = useWarmingUpRetry<GrowthAgentWatchlistSummaryResponse>(
    (signal) => growthAgentWatchlistsApi.watchlistSummary(signal),
    { queryKey: queryKeys.growthAgentWatchlistSummary() },
  );
  const scheduler = query.data?.scheduler.state ?? null;
  return (
    <section className="surface watchlist-briefings" aria-labelledby="watchlist-briefings-title">
      <div className="surface__hdr">
        <Icon name="target" size={14} />
        <SurfaceTitle id="watchlist-briefings-title">Watchlist briefings</SurfaceTitle>
        {scheduler && (
          <span className="watchlist-briefings__scheduler">
            <Chip variant={scheduler === 'active' ? 'neutral' : 'warning'}>{SCHEDULED_RUN_STATUS[scheduler]}</Chip>
          </span>
        )}
      </div>
      <div className="surface__body">
        <AsyncState
          query={query}
          subject="Watchlist briefings"
          loading={<div className="skeleton watchlist-briefings__skeleton" aria-hidden="true" />}
          isEmpty={(data) => data.watchlists.length === 0}
          empty={(
            <p className="watchlist-briefings__empty">
              No saved watchlists yet. Save a Growth Agent run as a watchlist in{' '}
              <Link to={WORKFLOWS_HREF}>Ask Genie</Link>.
            </p>
          )}
        >
          {(data) => (
            <>
              <ul className="watchlist-briefings__list">
                {data.watchlists.slice(0, WATCHLIST_BRIEFING_ROWS).map((briefing) => (
                  <BriefingRow key={briefing.monitor_id} briefing={briefing} />
                ))}
              </ul>
              {data.watchlists.length > WATCHLIST_BRIEFING_ROWS && (
                <Link className="watchlist-briefings__more" to={WORKFLOWS_HREF}>
                  +{formatCount(data.watchlists.length - WATCHLIST_BRIEFING_ROWS)} more watchlists
                </Link>
              )}
            </>
          )}
        </AsyncState>
      </div>
    </section>
  );
}

function BriefingRow({ briefing }: { briefing: GrowthAgentWatchlistBriefing }) {
  const points = briefing.recent_actionable_totals;
  return (
    <li className="watchlist-briefings__row" data-testid="watchlist-briefing">
      <div className="watchlist-briefings__head">
        <Link className="watchlist-briefings__name" to={WORKFLOWS_HREF}>{briefing.name}</Link>
        {briefing.status !== 'active' && (
          <Chip variant="warning">{briefing.status === 'paused' ? 'Paused' : 'Disabled'}</Chip>
        )}
      </div>
      <div className="watchlist-briefings__figures">
        <span className="num">{formatCount(briefing.actionable_total)} actionable</span>
        <span className="num">
          {briefing.actionable_delta === null ? 'first run' : `${signedCount(briefing.actionable_delta)} since last run`}
        </span>
        <span className="num">avg score {signedScore(briefing.avg_score_delta)}</span>
        {points.length > 1 && (
          <span className="watchlist-briefings__spark">
            <Sparkline points={points} width={48} height={16} />
            <span className="sr-only">Recent runs: {points.map((point) => formatCount(point)).join(', ')} actionable.</span>
          </span>
        )}
        <span className="watchlist-briefings__age">
          {briefing.last_run_at ? (
            <>Run <Timestamp value={briefing.last_run_at} relativeStyle="narrow" /></>
          ) : (
            'Not run yet'
          )}
        </span>
      </div>
    </li>
  );
}
