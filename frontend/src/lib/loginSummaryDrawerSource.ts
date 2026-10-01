import type { DrawerSource } from '../components/AppContext';
import type { HomeSummaryHighlight } from '../types';
import { DRAWER_SOURCES } from './drawerSourceRegistry';
import { formatCount, ratePct, signedBpsLabel } from './formatters';
import type { RateMoveSinceVisit } from './homeAnswer';

const HOME_SUMMARY_LINEAGE_FAMILY: Record<string, string> = {
  marketable_population: 'marketable_population',
  high_opportunity: 'opportunity_score',
  refi_economics_screen: 'in_the_money',
  offers_available: 'next_best_offer',
  offers_recommended: 'next_best_offer',
  listed_for_sale: 'listing_activity',
};

/** Evidence for one "since your last login" number, citing both snapshots. */
export function loginSummaryDrawerSource(
  highlight: Pick<
    HomeSummaryHighlight,
    'measure' | 'label' | 'display' | 'current' | 'baseline' | 'delta' | 'delta_pct'
  >,
  opts: { previousVisitAt: string | null } = { previousVisitAt: null },
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
  return {
    title: hasBaseline
      ? `Since your last login — ${highlight.label}`
      : `Today's briefing — ${highlight.label}`,
    short: `portfolio_headline_metric_view.${highlight.measure}`,
    assetKey: 'portfolio_headline_metric_view',
    assetPath: 'mip.semantics.portfolio_headline_metric_view',
    ...(lineageFamily ? { lineageFamily } : {}),
    description: hasBaseline
      ? 'Signed movement between the daily headline-KPI snapshot nearest your ' +
        'previous visit (mip_app.kpi_snapshots) and the live unfiltered headline ' +
        'metric view. Both sides aggregate the same headline set, so the ' +
        'comparison is apples-to-apples.'
      : 'Live reading from the unfiltered portfolio headline metric view. ' +
        'Last-login deltas appear once a previous visit and a baseline snapshot exist.',
    signals,
    ...(opts.previousVisitAt ? { eventDate: opts.previousVisitAt } : {}),
  };
}

/**
 * Evidence for WHY NOW's rate move (flow-05): the rate window's source with
 * the two weekly prints the move subtracts (FRED MORTGAGE30US, read from
 * mip.gold.rate_window_weekly) leading its signals.
 */
export function rateMoveDrawerSource(
  move: RateMoveSinceVisit,
  opts: { previousVisitAt: string | null } = { previousVisitAt: null },
): DrawerSource {
  const base = DRAWER_SOURCES.rateWindow;
  return {
    ...base,
    title: '30-year par rate since your last visit',
    description:
      `The weekly FRED MORTGAGE30US print for the week of ${move.toWeek} against the print for the week of your ` +
      `previous visit (${move.fromWeek}), both read as-is from mip.gold.rate_window_weekly. ${base.description ?? ''}`.trim(),
    signals: [
      { label: `Week of ${move.fromWeek}`, source: 'mip.gold.rate_window_weekly.market_rate_pct (MORTGAGE30US)', value: ratePct(move.fromPct) },
      { label: `Week of ${move.toWeek} (latest)`, source: 'mip.gold.rate_window_weekly.market_rate_pct (MORTGAGE30US)', value: ratePct(move.toPct) },
      { label: 'Since your last visit', source: 'latest - visit week', value: signedBpsLabel(move.deltaBps) },
      ...(base.signals ?? []),
    ],
    ...(opts.previousVisitAt ? { eventDate: opts.previousVisitAt } : {}),
  };
}
