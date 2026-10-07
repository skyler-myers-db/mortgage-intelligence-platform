import type { DrawerSource } from '../components/AppContext';
import type { HomeSummaryHighlight } from '../types';
import { isDeltaExplainerMeasure } from './deltaExplainerSource';
import { DRAWER_SOURCES } from './drawerSourceRegistry';
import { formatCount, ratePct, signedBpsLabel } from './formatters';
import type { RateMoveSinceVisit } from './homeAnswer';

/** The UTC calendar date (YYYY-MM-DD) of an instant, or null. */
function utcDate(instant: string | null | undefined): string | null {
  const at = instant ? Date.parse(instant) : Number.NaN;
  return Number.isFinite(at) ? new Date(at).toISOString().slice(0, 10) : null;
}

const HOME_SUMMARY_LINEAGE_FAMILY: Record<string, string> = {
  marketable_population: 'marketable_population',
  high_opportunity: 'opportunity_score',
  refi_economics_screen: 'in_the_money',
  offers_available: 'next_best_offer',
  offers_recommended: 'next_best_offer',
  listed_for_sale: 'listing_activity',
};

/**
 * Evidence for one "since your last login" number, citing both snapshots.
 * Its `value` is the number the chip shows, so the drawer leads with "How we
 * got {value}" and its first sentence as the definition (audit flow-06). A
 * delta of a Delta Explainer measure (wow-ai-3) also carries the explainer,
 * anchored on the baseline KPI snapshot's date; whether the funnel snapshot
 * attributes that measure per state is the route's answer (`snapshotted`).
 */
export function loginSummaryDrawerSource(
  highlight: Pick<
    HomeSummaryHighlight,
    'measure' | 'label' | 'display' | 'current' | 'baseline' | 'delta' | 'delta_pct'
  >,
  opts: { previousVisitAt: string | null; baselineSnapshotAt?: string | null; status?: string } = {
    previousVisitAt: null,
  },
): DrawerSource {
  const hasBaseline = highlight.baseline !== null && highlight.delta !== null;
  const lineageFamily = HOME_SUMMARY_LINEAGE_FAMILY[highlight.measure];
  const signals: NonNullable<DrawerSource['signals']> = [
    {
      label: 'Current',
      source: `portfolio_headline_metric_view.${highlight.measure}`,
      value: formatCount(highlight.current),
    },
  ];
  if (hasBaseline) {
    signals.push(
      {
        label: 'Baseline',
        source: `kpi_snapshots.${highlight.measure}`,
        value: formatCount(highlight.baseline as number),
      },
      {
        label: 'Since last login',
        source: 'current - baseline',
        value:
          highlight.delta_pct !== null
            ? `${highlight.display} (${formatCount(highlight.delta as number)})`
            : highlight.display,
      },
    );
  }
  const baselineDate = opts.status === 'delta' && hasBaseline ? utcDate(opts.baselineSnapshotAt) : null;
  const explainer = baselineDate && isDeltaExplainerMeasure(highlight.measure)
    ? { deltaExplainer: { measure: highlight.measure, baselineDate } }
    : {};
  return {
    ...explainer,
    title: hasBaseline
      ? `Since your last login — ${highlight.label}`
      : `Today's briefing — ${highlight.label}`,
    short: `portfolio_headline_metric_view.${highlight.measure}`,
    assetKey: 'portfolio_headline_metric_view',
    assetPath: 'mip.semantics.portfolio_headline_metric_view',
    ...(lineageFamily ? { lineageFamily } : {}),
    value: highlight.display,
    definition: hasBaseline
      ? 'Signed movement between the daily headline-KPI snapshot nearest your ' +
        'previous visit (mip_app.kpi_snapshots) and the live unfiltered headline ' +
        'metric view.'
      : 'Live reading from the unfiltered portfolio headline metric view.',
    description: hasBaseline
      ? 'Both sides aggregate the same headline set, so the comparison is apples-to-apples.'
      : 'Last-login deltas appear once a previous visit and a baseline snapshot exist.',
    signals,
    ...(opts.previousVisitAt ? { eventDate: opts.previousVisitAt } : {}),
  };
}

/**
 * Evidence for WHY NOW's rate move (flow-05): the rate window's source with
 * the two weekly prints the move subtracts (FRED MORTGAGE30US, read from
 * mip.gold.rate_window_weekly) leading its signals. Its `value` is the chip's
 * signed basis points and its definition names the two prints (flow-06); the
 * rate window's description and signals follow by registry key.
 */
export function rateMoveDrawerSource(
  move: RateMoveSinceVisit,
  opts: { previousVisitAt: string | null } = { previousVisitAt: null },
): DrawerSource {
  const base = DRAWER_SOURCES.rateWindow;
  return {
    ...base,
    title: '30-year par rate since your last visit',
    value: signedBpsLabel(move.deltaBps),
    definition:
      `The weekly FRED MORTGAGE30US print for the week of ${move.toWeek} against the print for the week of your ` +
      `previous visit (${move.fromWeek}), both read as-is from mip.gold.rate_window_weekly.`,
    signals: [
      { label: `Week of ${move.fromWeek}`, source: 'mip.gold.rate_window_weekly.market_rate_pct (MORTGAGE30US)', value: ratePct(move.fromPct) },
      { label: `Week of ${move.toWeek} (latest)`, source: 'mip.gold.rate_window_weekly.market_rate_pct (MORTGAGE30US)', value: ratePct(move.toPct) },
      { label: 'Since your last visit', source: 'latest - visit week', value: signedBpsLabel(move.deltaBps) },
    ],
    ...(opts.previousVisitAt ? { eventDate: opts.previousVisitAt } : {}),
  };
}
