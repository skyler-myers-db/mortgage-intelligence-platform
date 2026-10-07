import type { DrawerSource } from '../components/AppContext';
import type { HomeAttributionMeasure } from '../types/homeAttribution';

/**
 * The Delta Explainer's drawer source (audit 2026-09-21 `wow-ai-3`): a
 * "since your last login" number whose drawer can explain where the measure
 * moved. DrawerSource itself lives in AppContext.tsx (another lane's file
 * this wave), so the extension is declared here with a type guard; the
 * W5c evidence-drawer rewrite folds it into DrawerSource and keeps the slot.
 */
export interface DeltaExplainer {
  measure: HomeAttributionMeasure;
  /** The baseline the funnel snapshot is chosen near (YYYY-MM-DD, UTC). */
  baselineDate: string;
  /** The server-minted token the chip showed ("+2,250"): the live metric view vs the KPI snapshot. */
  liveDisplay: string;
}

export interface DeltaExplainerDrawerSource extends DrawerSource {
  deltaExplainer: DeltaExplainer;
}

/** The measures gold.funnel_snapshot_daily can attribute per state. */
export const DELTA_EXPLAINER_MEASURES: ReadonlySet<HomeAttributionMeasure> = new Set([
  'refi_economics_screen',
  'high_opportunity',
  'offers_recommended',
  'listed_for_sale',
]);

export function isDeltaExplainerMeasure(measure: string): measure is HomeAttributionMeasure {
  return (DELTA_EXPLAINER_MEASURES as ReadonlySet<string>).has(measure);
}

/** The explainer a drawer source carries, or null for every other source. */
export function deltaExplainerOf(source: DrawerSource | null | undefined): DeltaExplainer | null {
  const explainer = (source as Partial<DeltaExplainerDrawerSource> | null | undefined)?.deltaExplainer;
  if (!explainer || typeof explainer !== 'object') return null;
  const { measure, baselineDate, liveDisplay } = explainer;
  if (typeof measure !== 'string' || !isDeltaExplainerMeasure(measure)) return null;
  if (typeof baselineDate !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(baselineDate)) return null;
  return { measure, baselineDate, liveDisplay: typeof liveDisplay === 'string' ? liveDisplay : '' };
}
