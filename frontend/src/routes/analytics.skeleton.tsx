import { useIsOnline } from '../lib/connectivity';
import './analytics.skeleton.css';

/**
 * Loading placeholders for the Analytics dashboards (audit 2026-09-21
 * `states-10`).
 *
 * The old placeholder was three text-height lines in one card (~120px) for
 * every dashboard; the loaded dashboard is a KPI row and several full-height
 * chart panels, so the page jumped by about a thousand pixels when data
 * landed. Each dashboard now declares its shape: the KPI cards it opens with
 * and its panel rows, laid out with the SAME grid classes the loaded view
 * uses (`.kpi-row`, `.layoutA-grid.analytics-grid`, `--wide-left`), each panel
 * reserving the chart height (`.analytics-skeleton__chart`).
 *
 * While the browser is offline the query is paused, not loading: the first
 * panel says it is waiting for the connection and the shimmer stops.
 */

export type AnalyticsSkeletonRow = 'full' | 'split' | 'split-wide-left';

export interface AnalyticsSkeletonShape {
  /** KPI cards above the panels (the Executive dashboard opens with four). */
  kpis?: number;
  /** Panel rows, top to bottom: one full-width panel or a two-panel grid row. */
  rows: readonly AnalyticsSkeletonRow[];
}

/** One shape per LoadState caller; `panel` is the single-panel default. */
export const ANALYTICS_SKELETONS = {
  panel: { rows: ['full'] },
  executive: { kpis: 4, rows: ['full', 'split'] },
  geography: { rows: ['split', 'full'] },
  economics: { rows: ['split-wide-left'] },
  segments: { rows: ['full', 'split', 'full'] },
  signals: { rows: ['full', 'full', 'full'] },
} as const satisfies Record<string, AnalyticsSkeletonShape>;

function SkeletonPanel({
  title,
  online,
  className,
}: {
  /** Only the first panel carries the dashboard's title (and the offline copy). */
  title: string | null;
  online: boolean;
  className: string;
}) {
  return (
    <section className={className}>
      <div className="surface__hdr">
        {title ? <h2 className="h-3">{title}</h2> : <span className="skeleton skeleton--heading" aria-hidden="true" />}
      </div>
      <div className="surface__body">
        {title && !online ? (
          <p className="muted fs-12 analytics-skeleton__chart">
            Waiting for a connection. This loads as soon as you are back online.
          </p>
        ) : (
          <span className="skeleton analytics-skeleton__chart" aria-hidden="true" />
        )}
      </div>
    </section>
  );
}

export function AnalyticsSkeleton({ title, shape }: { title: string; shape: AnalyticsSkeletonShape }) {
  const online = useIsOnline();
  // The dashboard's title goes on the first panel of the first row.
  const panel = (key: string, className: string, first: boolean) => (
    <SkeletonPanel key={key} title={first ? title : null} online={online} className={className} />
  );
  return (
    <div className="analytics-skeleton" role="status" aria-busy={online} data-analytics-skeleton="">
      {shape.kpis ? (
        <div className="kpi-row" aria-hidden="true">
          {Array.from({ length: shape.kpis }, (_, index) => (
            <div key={index} className="kpi is-loading">
              <span className="skeleton analytics-skeleton__label" />
              <span className="skeleton kpi__value-skeleton" />
              <span className="skeleton kpi__delta-skeleton" />
            </div>
          ))}
        </div>
      ) : null}
      {shape.rows.map((row, index) =>
        row === 'full' ? (
          // Like the loaded views: a leading panel sits flush, later ones
          // take `.analytics-section`'s top gap.
          panel(`row-${index}`, index === 0 && !shape.kpis ? 'surface' : 'surface analytics-section', index === 0)
        ) : (
          <div
            key={`row-${index}`}
            className={`layoutA-grid analytics-grid${row === 'split-wide-left' ? ' analytics-grid--wide-left' : ''}`}
          >
            {panel(`row-${index}-a`, 'surface', index === 0)}
            {panel(`row-${index}-b`, 'surface', false)}
          </div>
        ),
      )}
    </div>
  );
}
