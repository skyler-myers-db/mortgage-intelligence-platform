// React Compiler memo caches add enough code here to breach the strict bundle
// budget; the analytics route family opts out. Keep this section explicit.
'use no memo';

import { useId, useMemo, useState, type CSSProperties } from 'react';
import { EvidenceChip } from '../components/Primitives';
import { api } from '../lib/api';
import { DRAWER_SOURCES } from '../lib/drawerSources';
import { queryKeys } from '../lib/queryKeys';
import { formatTimestamp } from '../lib/time';
import { useWarmingUpRetry } from '../lib/useWarmingUpRetry';
import type { RateWindowResponse } from '../types';
import { LoadState } from './analytics.charts';
import {
  buildRateWindowModel,
  formatRatePct,
  itmY,
  rateY,
  RATE_WINDOW_TITLE,
  type RateWindowModel,
} from './analytics.rate-window.lib';

/**
 * The rate-window read. The Analytics route calls this too, beside the
 * executive query, so the request starts in parallel instead of waiting for
 * the Executive tab to render; both observers share one React Query entry.
 * The GET writes no audit row, so starting it early has no side effect.
 */
export function useRateWindowQuery(enabled = true) {
  return useWarmingUpRetry<RateWindowResponse>(
    (signal) => api.analyticsRateWindow(signal),
    ['analytics', 'rate-window'],
    {
      enabled,
      queryKey: queryKeys.analytics('rate-window'),
      keepPreviousData: true,
      staleTime: 60_000,
    },
  );
}

/**
 * "Why now" (2026-09-21 audit, dataviz-08 / dataviz-06): two stacked panels on
 * ONE shared x-axis, never a dual axis. Top: the weekly 30-year fixed print
 * against the current book's note-rate band (p25-p75 shaded, median dashed)
 * with the current print annotated and the lender's spread screen drawn as a
 * labelled reference line, so the chart says something. Bottom: liens in the
 * money at that week's rate. Hand-rolled SVG in the analytics chart idiom
 * (0-100 viewBox for marks, HTML for text), tokens only, no brush or zoom.
 *
 * `.rate-window` is an app-extension BEM block: the prototype has no time
 * series chart, so it composes prototype primitives (.surface, .btn,
 * .analytics-table) with the existing analytics chart chrome.
 */
export function RateWindowSection({ filtersActive = false }: { filtersActive?: boolean }) {
  const query = useRateWindowQuery();
  return (
    <LoadState query={query} title={RATE_WINDOW_TITLE}>
      {(data) => <RateWindowPanel data={data} filtersActive={filtersActive} />}
    </LoadState>
  );
}

export function RateWindowPanel({ data, filtersActive = false }: { data: RateWindowResponse; filtersActive?: boolean }) {
  const [showTable, setShowTable] = useState(false);
  const model = useMemo(() => buildRateWindowModel(data), [data]);
  const bookAsOf = data.book_as_of ? formatTimestamp(data.book_as_of) : null;
  return (
    <section className="surface analytics-section rate-window" data-testid="rate-window" aria-labelledby="rate-window-title">
      <div className="surface__hdr surface__hdr--split">
        <div>
          <h2 className="h-3" id="rate-window-title">{RATE_WINDOW_TITLE}</h2>
          {model ? (
            <p className="analytics-panel-note rate-window__summary" data-testid="rate-window-summary">
              <strong>{model.spreadSentence}</strong> {model.itmSentence}
            </p>
          ) : (
            <p className="analytics-panel-note rate-window__summary" data-testid="rate-window-summary">
              No weeks in the rate window yet: the gold refresh job (mip_refresh_scores) builds it on its next run.
            </p>
          )}
        </div>
        <div className="rate-window__actions">
          {filtersActive && (
            <span className="chip chip--warning" data-testid="rate-window-unfiltered">
              All states: filters not applied
            </span>
          )}
          <EvidenceChip source={DRAWER_SOURCES.rateWindow}>Weekly rates + today&#39;s book</EvidenceChip>
          {model && (
            <button
              type="button"
              className="btn btn--sm"
              data-testid="rate-window-view-toggle"
              onClick={() => setShowTable((value) => !value)}
            >
              {showTable ? 'View as chart' : 'View as table'}
            </button>
          )}
        </div>
      </div>
      <div className="surface__body">
        {model && (showTable ? <RateWindowTable model={model} /> : <RateWindowCharts model={model} />)}
        <p className="muted fs-12 rate-window__asof" data-testid="rate-window-asof">
          Today&#39;s book{bookAsOf ? ` (as of ${bookAsOf})` : ''} against the historical rate: the band and the
          in-the-money count are measured over the current fixed-rate book and applied to every week, not the book as
          it stood that week. Fixed-rate first liens only, across every state and segment: the filters above do not
          change this panel.
        </p>
      </div>
    </section>
  );
}

function tickStyle(pos: number): CSSProperties {
  return { '--tick-pos': `${pos}%` } as CSSProperties;
}

function RateWindowCharts({ model }: { model: RateWindowModel }) {
  const clipId = useId();
  const marketPoints = model.points.map((p) => `${p.x.toFixed(2)},${rateY(model, p.marketPct).toFixed(2)}`).join(' ');
  const bandPoints = model.points.filter((p) => p.p25Pct !== null && p.p75Pct !== null);
  const band = bandPoints.length > 0
    ? [
        ...bandPoints.map((p) => `${p.x.toFixed(2)},${rateY(model, p.p75Pct as number).toFixed(2)}`),
        ...[...bandPoints].reverse().map((p) => `${p.x.toFixed(2)},${rateY(model, p.p25Pct as number).toFixed(2)}`),
      ].join(' ')
    : null;
  const medianPoints = model.points
    .filter((p) => p.medianPct !== null)
    .map((p) => `${p.x.toFixed(2)},${rateY(model, p.medianPct as number).toFixed(2)}`)
    .join(' ');
  const itmLine = model.points.map((p) => `${p.x.toFixed(2)},${itmY(model, p.itmCount).toFixed(2)}`).join(' ');
  const itmArea = `${model.points[0].x.toFixed(2)},100 ${itmLine} ${model.points[model.points.length - 1].x.toFixed(2)},100`;
  const currentY = rateY(model, model.current.marketPct);
  const thresholdY = model.threshold ? rateY(model, model.threshold.ratePct) : null;
  return (
    <div className="rate-window__panels" role="img" aria-label={model.ariaLabel}>
      <div className="rate-window__panel rate-window__panel--rates" data-testid="rate-window-rates">
        <p className="rate-window__panel-title">30-year fixed vs. the book&#39;s note rates</p>
        <div className="analytics-chart">
          <div className="analytics-chart__plot">
            <div className="analytics-chart__y-ticks" aria-hidden="true">
              {[...model.rate.ticks].reverse().map((tick) => (
                <span key={tick} className="analytics-chart__tick analytics-chart__tick--y" style={tickStyle(rateY(model, tick))}>
                  {formatRatePct(tick)}
                </span>
              ))}
            </div>
            <div className="analytics-chart__canvas">
              <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="analytics-line-chart rate-window__svg">
                <defs>
                  <clipPath id={clipId}>
                    <rect x="0" y="0" width="100" height="100" />
                  </clipPath>
                </defs>
                {model.rate.ticks.map((tick) => (
                  <line key={`grid-${tick}`} x1="0" x2="100" y1={rateY(model, tick)} y2={rateY(model, tick)} className="analytics-chart__grid" vectorEffect="non-scaling-stroke" />
                ))}
                {band && <polygon points={band} className="rate-window__band" clipPath={`url(#${clipId})`} />}
                {medianPoints && <polyline points={medianPoints} className="rate-window__median" vectorEffect="non-scaling-stroke" />}
                {thresholdY !== null && (
                  <line x1="0" x2="100" y1={thresholdY} y2={thresholdY} className="rate-window__threshold" vectorEffect="non-scaling-stroke" />
                )}
                <polyline points={marketPoints} className="rate-window__market" clipPath={`url(#${clipId})`} vectorEffect="non-scaling-stroke" />
              </svg>
              {model.threshold && thresholdY !== null && (
                // textContent always equals model.threshold.label; a narrow
                // plot hides the detail span and keeps name + rate.
                <span className="rate-window__ref-label" style={tickStyle(thresholdY)} data-testid="rate-window-threshold">
                  Spread screen
                  <span className="rate-window__ref-detail">: {model.threshold.minSpreadBps} bps below the book median</span>
                  {` (${formatRatePct(model.threshold.ratePct)})`}
                </span>
              )}
              <span
                className="rate-window__current-dot"
                style={{ '--dot-x': `${model.current.x}%`, '--dot-y': `${currentY}%` } as CSSProperties}
                aria-hidden="true"
              />
              <span className="rate-window__current" style={tickStyle(currentY)} data-testid="rate-window-current">
                {formatRatePct(model.current.marketPct)} now
              </span>
            </div>
          </div>
        </div>
        <div className="rate-window__legend" aria-hidden="true">
          <span className="rate-window__legend-item"><span className="rate-window__swatch" /> 30-year fixed (FRED)</span>
          <span className="rate-window__legend-item"><span className="rate-window__swatch rate-window__swatch--median" /> Book median note rate</span>
          <span className="rate-window__legend-item"><span className="rate-window__swatch rate-window__swatch--band" /> Book p25&ndash;p75</span>
          {model.threshold && (
            <span className="rate-window__legend-item"><span className="rate-window__swatch rate-window__swatch--threshold" /> Spread screen (median)</span>
          )}
        </div>
      </div>
      <div className="rate-window__panel rate-window__panel--itm" data-testid="rate-window-itm">
        <p className="rate-window__panel-title">Liens in the money at that week&#39;s rate</p>
        <div className="analytics-chart">
          <div className="analytics-chart__plot">
            <div className="analytics-chart__y-ticks" aria-hidden="true">
              {[...model.itm.ticks].reverse().map((tick) => (
                <span key={tick} className="analytics-chart__tick analytics-chart__tick--y" style={tickStyle(itmY(model, tick))}>
                  {tick.toLocaleString('en-US')}
                </span>
              ))}
            </div>
            <div className="analytics-chart__canvas">
              <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="analytics-line-chart rate-window__svg">
                {model.itm.ticks.map((tick) => (
                  <line key={`grid-${tick}`} x1="0" x2="100" y1={itmY(model, tick)} y2={itmY(model, tick)} className="analytics-chart__grid" vectorEffect="non-scaling-stroke" />
                ))}
                <polygon points={itmArea} className="rate-window__itm-area" />
                <polyline points={itmLine} className="rate-window__itm-line" vectorEffect="non-scaling-stroke" />
              </svg>
              <div className="analytics-chart__x-ticks" aria-hidden="true">
                {model.xTicks.map((tick) => (
                  <span
                    key={tick.label + tick.x}
                    className={`analytics-chart__tick analytics-chart__tick--x rate-window__xtick rate-window__xtick--${tick.anchor}${tick.minor ? ' rate-window__xtick--minor' : ''}`}
                    style={tickStyle(tick.x)}
                  >
                    {tick.label}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function RateWindowTable({ model }: { model: RateWindowModel }) {
  const rate = (value: number | null) => (value === null ? '—' : formatRatePct(value));
  return (
    <div className="analytics-table-wrap rate-window__table-wrap">
      <table className="analytics-table" data-testid="rate-window-table">
        <caption className="sr-only">{RATE_WINDOW_TITLE}: one row per week, oldest first.</caption>
        <thead>
          <tr>
            <th scope="col">Week</th>
            <th scope="col">30-year fixed</th>
            <th scope="col">Book p25</th>
            <th scope="col">Book median</th>
            <th scope="col">Book p75</th>
            <th scope="col">In the money</th>
          </tr>
        </thead>
        <tbody>
          {model.points.map((p) => (
            <tr key={p.week}>
              <td className="mono">{p.week}{p.isLatest ? ' (current)' : ''}</td>
              <td className="mono num">{formatRatePct(p.marketPct)}</td>
              <td className="mono num">{rate(p.p25Pct)}</td>
              <td className="mono num">{rate(p.medianPct)}</td>
              <td className="mono num">{rate(p.p75Pct)}</td>
              <td className="mono num">{p.itmCount.toLocaleString('en-US')}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
