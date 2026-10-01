import { Link } from 'react-router';
import { AsyncState } from '../ui/AsyncState';
import { StaleDataNote } from '../ui/StaleDataNote';
import { homeAttributionApi } from '../../lib/apiClients/homeAttribution';
import type { Fresh } from '../../lib/apiClients/headers';
import type { DeltaExplainer as DeltaExplainerInput } from '../../lib/deltaExplainerSource';
import { formatCount, ratePct, signedCount } from '../../lib/formatters';
import { measureQueueHref } from '../../lib/homeAnswer';
import { queryKeys } from '../../lib/queryKeys';
import { formatDate } from '../../lib/time';
import { useWarmingUpRetry } from '../../lib/useWarmingUpRetry';
import type { HomeSummaryAttributionResponse } from '../../types/homeAttribution';
import {
  ATTRIBUTION_MAX_LOOKBACK_DAYS,
  waterfallDomain,
  waterfallSteps,
  withinAttributionWindow,
  type WaterfallStep,
} from './deltaExplainer.model';
import './DeltaExplainer.css';

/**
 * DeltaExplainer — where one "since your last login" number moved (audit
 * 2026-09-21 `wow-ai-3`), in the evidence drawer's Overview (lazy, inside the
 * drawer body's own lazy chunk). One audit-free GET /api/home/summary/
 * attribution while the drawer is open on Overview: never on hover (the
 * evidence hover card never renders it), prefetch or poll.
 *
 * It shows accounting, not causation: the whole-book state counts of the
 * daily funnel snapshot nearest the baseline and the latest one, as a
 * waterfall (aria-hidden) and as a `.tbl` table (its accessible equivalent,
 * each state linking to its Lead Queue cohort), then what COINCIDED with the
 * change: the two weekly 30-year par prints and the last offer-rules change.
 *
 * deviation:delta-explainer: a declared extension of the drawer body's
 * source card (design_files/index.html:665-681): block `.delta-explainer`;
 * increases paint --accent-data, decreases --text-3 (never an alarm hue).
 */

const ROW_HEIGHT = 22;
const LABEL_WIDTH = 132;
const CHART_WIDTH = 400;
const BAR_HEIGHT = 12;

export default function DeltaExplainer({ explainer }: { explainer: DeltaExplainerInput }) {
  // The route refuses a baseline older than its lookback (a 422): never ask.
  const answerable = withinAttributionWindow(explainer.baselineDate);
  const query = useWarmingUpRetry<Fresh<HomeSummaryAttributionResponse>>(
    (signal) => homeAttributionApi.homeSummaryAttribution(explainer.measure, explainer.baselineDate, signal),
    { queryKey: queryKeys.homeSummaryAttribution(explainer.measure, explainer.baselineDate), enabled: answerable },
  );
  return (
    <section className="source-card delta-explainer" aria-label="Where the change came from" data-testid="delta-explainer">
      <div className="eyebrow mb-2">Where the change came from</div>
      {answerable ? (
        <AsyncState
          query={query}
          subject="The change breakdown"
          loading={<div className="skeleton delta-explainer__skeleton" aria-hidden="true" />}
          isEmpty={(fresh) => fresh.data.total_change === null}
          empty={(
            <p className="body flush">
              No daily funnel snapshot covers this period yet, so the change cannot be broken down by state.
            </p>
          )}
        >
          {(fresh) => <Breakdown response={fresh.data} lastGoodAt={fresh.lastGoodAt} liveDisplay={explainer.liveDisplay} />}
        </AsyncState>
      ) : (
        <p className="body flush" data-testid="delta-explainer-too-old">
          The change is broken down by state for a baseline from the last {ATTRIBUTION_MAX_LOOKBACK_DAYS} days; your
          baseline ({formatDate(explainer.baselineDate)}) is older.
        </p>
      )}
    </section>
  );
}

function Breakdown({
  response,
  lastGoodAt,
  liveDisplay,
}: {
  response: HomeSummaryAttributionResponse;
  lastGoodAt: string | null;
  liveDisplay: string;
}) {
  const steps = waterfallSteps(response) ?? [];
  const { rate } = response;
  return (
    <>
      <StaleDataNote lastGoodAt={lastGoodAt} />
      <p className="body flush">
        Daily funnel snapshots of {formatDate(response.baseline_snapshot_date)} and{' '}
        {formatDate(response.current_snapshot_date)}: {signedCount(response.total_change)} {response.label}.
      </p>
      {response.nearest_snapshot && (
        <p className="muted fs-12 flush" data-testid="delta-explainer-nearest">
          No snapshot was taken on {formatDate(response.requested_baseline_date)}; the nearest one (
          {formatDate(response.baseline_snapshot_date)}) is used.
        </p>
      )}
      <p className="muted fs-12 flush">
        Addressable borrowers (the whole book); the Lead Queue shows the contactable subset.
      </p>
      <Waterfall steps={steps} />
      <table className="tbl delta-explainer__table">
        <caption className="sr-only">Change in {response.label} by state</caption>
        <thead>
          <tr>
            <th scope="col">State</th>
            <th scope="col" className="tbl-cell--right">At baseline</th>
            <th scope="col" className="tbl-cell--right">Now</th>
            <th scope="col" className="tbl-cell--right">Change</th>
          </tr>
        </thead>
        <tbody>
          {steps.filter((step) => step.kind !== 'total').map((step) => {
            const href = step.state ? measureQueueHref(response.measure, step.state) : null;
            return (
              <tr key={step.label} data-step={step.kind}>
                <th scope="row">{href ? <Link to={href}>{step.label}</Link> : step.label}</th>
                <td className="num tbl-cell--right">{formatCount(step.baselineCount)}</td>
                <td className="num tbl-cell--right">{formatCount(step.currentCount)}</td>
                <td className="num tbl-cell--right">{signedCount(step.value)}</td>
              </tr>
            );
          })}
        </tbody>
        <tfoot>
          <tr>
            <th scope="row">Whole book</th>
            <td className="num tbl-cell--right">{formatCount(response.baseline_total)}</td>
            <td className="num tbl-cell--right">{formatCount(response.current_total)}</td>
            <td className="num tbl-cell--right" data-testid="delta-explainer-total">{signedCount(response.total_change)}</td>
          </tr>
        </tfoot>
      </table>
      <div className="delta-explainer__coincided">
        <div className="eyebrow">Coincided with</div>
        <ul>
          <li>
            {rate.baseline_pct !== null && rate.latest_pct !== null
              ? `30-year par ${ratePct(rate.baseline_pct)} (week of ${formatDate(rate.baseline_week)}), now ${ratePct(rate.latest_pct)} (week of ${formatDate(rate.latest_week)})`
              : 'No 30-year par print covers your baseline week'}
          </li>
          <li>
            {response.offer_rules_changed_since_baseline === true
              ? `Offer rules last changed ${formatDate(response.offer_rules_last_updated)}`
              : response.offer_rules_changed_since_baseline === false
                ? 'No offer-rules change since your baseline'
                : 'When the offer rules last changed is not recorded'}
          </li>
        </ul>
        <p className="muted fs-12 flush">{response.note}</p>
      </div>
      <p className="muted fs-12 flush" data-testid="delta-explainer-reconcile">
        The live figure {liveDisplay} compares the live metric view with the KPI snapshot nearest your visit, so it
        can differ from the daily funnel snapshots.
      </p>
    </>
  );
}

/** The waterfall, aria-hidden: the table above is its accessible equivalent. */
function Waterfall({ steps }: { steps: readonly WaterfallStep[] }) {
  if (steps.length === 0) return null;
  const { lo, hi } = waterfallDomain(steps);
  const span = Math.max(1, hi - lo);
  const barWidth = CHART_WIDTH - LABEL_WIDTH;
  const x = (value: number) => LABEL_WIDTH + ((Math.max(lo, value) - lo) / span) * barWidth;
  const height = steps.length * ROW_HEIGHT;
  return (
    <figure className="delta-explainer__chart">
      <svg viewBox={`0 0 ${CHART_WIDTH} ${height}`} aria-hidden="true" focusable="false" data-testid="delta-explainer-waterfall">
        {steps.map((step, index) => {
          const start = step.kind === 'total' ? lo : Math.min(step.from, step.to);
          const end = step.kind === 'total' ? step.to : Math.max(step.from, step.to);
          const tone = step.kind === 'total' ? 'total' : step.value >= 0 ? 'up' : 'down';
          const y = index * ROW_HEIGHT;
          return (
            <g key={step.label} data-kind={step.kind}>
              <text className="delta-explainer__label" x={0} y={y + ROW_HEIGHT / 2 + 4}>
                {step.label}
              </text>
              <rect
                className={`delta-explainer__bar delta-explainer__bar--${tone}`}
                x={x(start)}
                y={y + (ROW_HEIGHT - BAR_HEIGHT) / 2}
                width={Math.max(1, x(end) - x(start))}
                height={BAR_HEIGHT}
              />
            </g>
          );
        })}
      </svg>
      <figcaption className="muted fs-12">
        Bars start at {formatCount(lo)}, not zero, so each state&apos;s change is visible.
      </figcaption>
    </figure>
  );
}
