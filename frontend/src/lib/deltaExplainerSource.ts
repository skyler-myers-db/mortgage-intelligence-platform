import type { DrawerSource } from '../components/AppContext';
import type { HomeAttributionMeasure } from '../types/homeAttribution';

/**
 * The Delta Explainer's input (audit 2026-09-21 `wow-ai-3`): a "since your
 * last login" number whose drawer can explain where the measure moved. The
 * W5c evidence-drawer lane folded it into DrawerSource itself
 * (`DrawerSource.deltaExplainer = { measure, baselineDate }`); the live
 * figure the reconcile line quotes is the source's own `value`, the number
 * the user clicked.
 */
export interface DeltaExplainer {
  measure: HomeAttributionMeasure;
  /** The baseline the funnel snapshot is chosen near (YYYY-MM-DD, UTC). */
  baselineDate: string;
  /** The server-minted token the chip showed ("+2,250"): the live metric view vs the KPI snapshot. */
  liveDisplay: string;
}

/**
 * The "since your last login" measures the explainer opens for. Whether the
 * funnel snapshot attributes one per state is the ROUTE's answer
 * (`snapshotted`), never decided here: competitor liens open the explainer,
 * which says the measure is not snapshotted until the gold column lands.
 */
export const DELTA_EXPLAINER_MEASURES: ReadonlySet<HomeAttributionMeasure> = new Set([
  'refi_economics_screen',
  'high_opportunity',
  'offers_recommended',
  'listed_for_sale',
  'competitor_lien',
]);

export function isDeltaExplainerMeasure(measure: string): measure is HomeAttributionMeasure {
  return (DELTA_EXPLAINER_MEASURES as ReadonlySet<string>).has(measure);
}

/** The explainer a drawer source carries, or null for every other source. */
export function deltaExplainerOf(source: DrawerSource | null | undefined): DeltaExplainer | null {
  const explainer = source?.deltaExplainer;
  if (!explainer || typeof explainer !== 'object') return null;
  const { measure, baselineDate } = explainer;
  if (typeof measure !== 'string' || !isDeltaExplainerMeasure(measure)) return null;
  if (typeof baselineDate !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(baselineDate)) return null;
  const liveDisplay = source?.value;
  return { measure, baselineDate, liveDisplay: typeof liveDisplay === 'string' ? liveDisplay : '' };
}
