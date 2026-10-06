/**
 * SpreadHistoryChart — "Crossed the line" (audit wow-stage-4), mounted ONLY in
 * the proof drawer's Math tab, so it ships in the lazy proof chunk.
 *
 * Two reads, and neither may write an audit row:
 *
 * 1. The Borrower360 comes from the TanStack cache ONLY. It never fetches:
 *    every GET /borrowers/{id} writes a VIEW_BORROWER row, so a drawer opened
 *    from the Lead Queue preview (dossier not cached) shows the "open the
 *    dossier" note instead. The read subscribes to the query CACHE, with no
 *    query observer at all, rather than `useQuery({ queryFn: skipToken })`:
 *    an observer's options overwrite the shared query's, so the dossier's own
 *    refetch after an approve's invalidation (query.fetch with no options)
 *    would invoke skipToken and fail; an observer with no queryFn logs a dev
 *    error on every render. A cache subscription can never fetch.
 * 2. The weekly series is the shared audit-free GET /analytics/rate-window
 *    (the key and queryFn routeDataPrefetch and the Analytics route use),
 *    enabled only while the Math tab shows the chart for an eligible borrower
 *    (an open drawer, a FIX first lien, an active note rate).
 *
 * The crossing marker is the server's first_itm_week; the client never
 * decides in-the-money for any week. Captions are history, never a forecast.
 *
 * Declared extension (deviation:spread-history-chart): a hand-rolled,
 * aria-hidden SVG with a text legend, an sr-only summary and a table
 * alternative. No DOM title and no SVG <title>.
 */
import { useCallback, useId, useMemo, useState, useSyncExternalStore } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import { formatDate } from '../../lib/time';
import { ratePct } from '../../lib/formatters';
import { useWarmingUpRetry, type UseWarmingUpRetryResult } from '../../lib/useWarmingUpRetry';
import type { Borrower360, RateWindowResponse } from '../../types';
import { Button, Chip } from '../Primitives';
import { Skeleton } from '../ui/Skeleton';
import { SPREAD_HISTORY_COPY as COPY } from './spreadHistory.copy';
import {
  buildSpreadHistoryModel,
  marketPath,
  spreadPolygon,
  type SpreadHistoryModel,
} from './spreadHistory.model';
import './SpreadHistoryChart.css';

const VIEW_W = 320;
const VIEW_H = 140;
const SX = VIEW_W / 100;
const SY = VIEW_H / 100;

export type SpreadHistoryGate = 'needs-dossier' | 'fixed-only' | 'no-note-rate' | 'eligible';

/** Which state the chart is in before any series read. */
export function spreadHistoryGate(borrower: Borrower360 | undefined): SpreadHistoryGate {
  if (!borrower) return 'needs-dossier';
  if (borrower.first_pos_rate_type !== 'FIX') return 'fixed-only';
  if (!(borrower.current_rate > 0)) return 'no-note-rate';
  return 'eligible';
}

function weekLabel(isoWeek: string): string {
  return formatDate(isoWeek, { withYear: true });
}

export function spreadHistoryCaption(model: SpreadHistoryModel): string {
  if (!model.crossing) return COPY.notInTheMoney;
  const week = weekLabel(model.crossing.week);
  return model.crossing.leftCensored ? COPY.sinceAtLeast(week) : COPY.sinceWeek(week);
}

function srSummary(model: SpreadHistoryModel): string {
  return (
    `Note rate ${ratePct(model.noteRatePct)} against the weekly 30-year fixed rate from ` +
    `${weekLabel(model.domainStart)} to ${weekLabel(model.domainEnd)}, latest ${ratePct(model.latestMarketPct)}; ` +
    `spread screen ${ratePct(model.screenPct)}. ${spreadHistoryCaption(model)}.`
  );
}

interface SpreadHistoryChartProps {
  borrowerId: string;
  /** The drawer is open on the Math tab: the only time the series may be read. */
  active: boolean;
}

/** The cached Borrower360, or undefined; re-renders when the cache changes, never fetches. */
function useCachedBorrower(borrowerId: string): Borrower360 | undefined {
  const client = useQueryClient();
  const subscribe = useCallback((onChange: () => void) => client.getQueryCache().subscribe(onChange), [client]);
  const snapshot = useCallback(
    () => client.getQueryData<Borrower360>(queryKeys.borrower(borrowerId)),
    [client, borrowerId],
  );
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

export function SpreadHistoryChart({ borrowerId, active }: SpreadHistoryChartProps) {
  const titleId = useId();
  const borrower = useCachedBorrower(borrowerId);
  const gate = spreadHistoryGate(borrower);
  const series = useWarmingUpRetry<RateWindowResponse>((signal) => api.analyticsRateWindow(signal), {
    queryKey: queryKeys.analytics('rate-window'),
    enabled: active && gate === 'eligible',
    staleTime: 60_000,
  });

  return (
    <section className="spread-history" data-testid="spread-history" aria-labelledby={titleId}>
      <div className="eyebrow" id={titleId}>{COPY.title}</div>
      <SpreadHistoryBody gate={gate} borrower={borrower} series={series} />
    </section>
  );
}

const EMPTY: Record<Exclude<SpreadHistoryGate, 'eligible'>, string> = {
  'needs-dossier': COPY.needsDossier,
  'fixed-only': COPY.fixedOnly,
  'no-note-rate': COPY.noNoteRate,
};

function EmptyNote({ text }: { text: string }) {
  return <p className="spread-history__note" data-testid="spread-history-empty">{text}</p>;
}

function SpreadHistoryBody({
  gate,
  borrower,
  series,
}: {
  gate: SpreadHistoryGate;
  borrower: Borrower360 | undefined;
  series: UseWarmingUpRetryResult<RateWindowResponse>;
}) {
  if (gate !== 'eligible' || !borrower) return <EmptyNote text={EMPTY[gate === 'eligible' ? 'no-note-rate' : gate]} />;
  if (series.error && !series.data) {
    return (
      <div className="proof-callout proof-callout--warning" data-testid="spread-history-error">
        <div className="eyebrow">{COPY.unavailableTitle}</div>
        <p className="body flush">{COPY.unavailableBody}</p>
        <Button size="sm" className="mt-2" disabled={series.isFetching} onClick={series.manualRetry}>
          {COPY.retry}
        </Button>
      </div>
    );
  }
  if (!series.data) {
    return (
      <div className="stack-sm" role="status" aria-busy="true" data-testid="spread-history-loading">
        <span className="sr-only">{COPY.loading}</span>
        <Skeleton width="100%" height={96} rounded="sm" />
      </div>
    );
  }
  return <SpreadHistoryFigure borrower={borrower} weeks={series.data} />;
}

function SpreadHistoryFigure({ borrower, weeks }: { borrower: Borrower360; weeks: RateWindowResponse }) {
  const [showTable, setShowTable] = useState(false);
  const tableId = useId();
  const model = useMemo(
    () =>
      buildSpreadHistoryModel({
        weeks: weeks.weeks,
        noteRatePct: borrower.current_rate,
        minSpreadBps: borrower.why_panel.min_spread_bps,
        firstItmWeek: borrower.first_itm_week,
        firstPosDate: borrower.first_pos_date,
      }),
    [borrower, weeks],
  );
  if (!model) return <EmptyNote text={COPY.noWeeks} />;
  const crossingX = model.crossing ? model.crossing.x * SX : null;
  const legend: Array<[string, string, string?]> = [
    ['note', COPY.legendNote, ratePct(model.noteRatePct)],
    ['market', COPY.legendMarket],
    ['screen', COPY.legendScreen, ratePct(model.screenPct)],
    ['spread', COPY.legendSpread],
  ];
  if (model.crossing) legend.push(['crossing', COPY.legendCrossing]);
  return (
    <>
      <p className="spread-history__caption" data-testid="spread-history-caption">{spreadHistoryCaption(model)}</p>
      <div className="spread-history__plot">
        <svg
          className="spread-history__svg"
          viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
          aria-hidden="true"
          data-testid="spread-history-svg"
        >
          <polygon className="spread-history__spread" points={spreadPolygon(model, SX, SY)} />
          <line className="spread-history__note-line" x1={0} x2={VIEW_W} y1={model.noteY * SY} y2={model.noteY * SY} />
          <line className="spread-history__screen" x1={0} x2={VIEW_W} y1={model.screenY * SY} y2={model.screenY * SY} />
          <path className="spread-history__market" d={marketPath(model, SX, SY)} pathLength={1} />
          {crossingX !== null && (
            <line
              className="spread-history__crossing"
              x1={crossingX}
              x2={crossingX}
              y1={0}
              y2={VIEW_H}
              data-testid="spread-history-crossing"
            />
          )}
        </svg>
        <div className="spread-history__axis" aria-hidden="true">
          <span>{weekLabel(model.domainStart)}</span>
          <span>{weekLabel(model.domainEnd)}</span>
        </div>
      </div>
      <p className="sr-only" data-testid="spread-history-summary">{srSummary(model)}</p>
      <ul className="spread-history__legend" aria-label="Chart legend">
        {legend.map(([key, label, value]) => (
          <li key={key} className="spread-history__legend-item">
            <span className={`spread-history__swatch spread-history__swatch--${key}`} aria-hidden="true" />
            {label}
            {value && <span className="spread-history__legend-value"> {value}</span>}
          </li>
        ))}
      </ul>
      <p className="spread-history__note" data-testid="spread-history-note">{COPY.historyNote}</p>
      <div className="spread-history__actions">
        <Button
          size="sm"
          aria-expanded={showTable}
          aria-controls={tableId}
          onClick={() => setShowTable((value) => !value)}
        >
          {showTable ? COPY.hideTable : COPY.showTable}
        </Button>
      </div>
      {/* Not its own scroll container: a scroller needs a focusable element
          (axe scrollable-region-focusable) and a tabindex on a plain div is a
          new jsx-a11y hit, so the table scrolls with the drawer body. */}
      <div id={tableId} className="spread-history__table" hidden={!showTable}>
        {showTable && <SpreadHistoryTable model={model} />}
      </div>
    </>
  );
}

function SpreadHistoryTable({ model }: { model: SpreadHistoryModel }) {
  return (
    <table className="tbl" aria-label={COPY.tableLabel} data-testid="spread-history-table">
      <thead>
        <tr>
          <th scope="col">{COPY.tableWeek}</th>
          <th scope="col" className="tbl-cell--right">{COPY.tableRate}</th>
        </tr>
      </thead>
      <tbody>
        {model.rows.map((row) => (
          <tr key={row.week} className={row.crossing ? 'spread-history__crossing-row' : undefined} data-crossing={row.crossing || undefined}>
            <td>
              {weekLabel(row.week)}
              {row.crossing && (
                <>
                  {' '}
                  <Chip variant="success">{COPY.crossingChip}</Chip>
                  <span className="spread-history__crossing-text">{COPY.crossingText}</span>
                </>
              )}
            </td>
            <td className="num tbl-cell--right">{ratePct(row.marketPct)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
