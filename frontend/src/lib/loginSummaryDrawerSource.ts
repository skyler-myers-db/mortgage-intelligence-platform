import type { DrawerSource } from '../components/AppContext';
import type { HomeSummaryHighlight } from '../types';

const HOME_SUMMARY_LINEAGE_FAMILY: Record<string, string> = {
  marketable_population: 'marketable_population',
  high_opportunity: 'opportunity_score',
  refi_economics_screen: 'in_the_money',
  offers_available: 'next_best_offer',
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
      value: highlight.current.toLocaleString(),
    },
  ];
  if (hasBaseline) {
    signals.push(
      {
        label: 'Baseline',
        source: `kpi_snapshots.${highlight.measure}`,
        value: (highlight.baseline as number).toLocaleString(),
      },
      {
        label: 'Since last login',
        source: 'current - baseline',
        value:
          highlight.delta_pct !== null
            ? `${highlight.display} (${(highlight.delta as number).toLocaleString()})`
            : highlight.display,
      },
    );
  }
  return {
    title: hasBaseline
      ? `Since your last login — ${highlight.label}`
      : `Your book today — ${highlight.label}`,
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
