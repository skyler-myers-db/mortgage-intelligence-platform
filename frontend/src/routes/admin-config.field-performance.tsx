import { useState, type ReactElement, type ReactNode } from 'react';
import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { Chip, SurfaceTitle } from '../components/Primitives';
import { AsyncStatus, type AsyncQuery } from '../components/ui/AsyncState';
import { EmptyState } from '../components/ui/EmptyState';
import { FetchedAt } from '../components/ui/FetchedAt';
import {
  fetchFieldPerformance,
  type FieldPerformanceCell,
  type FieldPerformanceDays,
  type FieldPerformanceResponse,
} from '../lib/apiClients/fieldPerformance';
import { useConfigOptionsQuery } from '../lib/configOptionsQuery';
import { formatCount, formatFixed } from '../lib/formatters';
import { queryKeys } from '../lib/queryKeys';

/**
 * Administration -> Field performance (D-platform-process-d2 step 12; audit
 * 2026-09-21 runtime-09, quality-07): p75 Core Web Vitals per route template
 * from the browser RUM day aggregates, INP by interaction target, and
 * client-error counts. Lazy inside admin-config.tsx, whose Suspense fallback
 * reserves this panel's block size.
 *
 * Reads only on mount, a 7/28-day change or an explicit Refresh: no polling,
 * no focus refetch, no prefetch. The Browser telemetry chip comes from the
 * config options the shell already holds (no request of its own); with RUM
 * off the panel says so and reads nothing.
 *
 * Declared prototype extension: design_files has no telemetry panel. It
 * reuses `.surface` / `.surface__hdr`, `.tbl` and the rating chips;
 * admin-config.field-performance.css only lays them out.
 * deviation:field-performance-panel
 */

export const FIELD_PERFORMANCE_TITLE_ID = 'admin-field-performance-title';
const SPANS: readonly FieldPerformanceDays[] = [7, 28];
const FLOOR_LABEL = '<20 samples';

type Rating = NonNullable<FieldPerformanceCell['rating']>;
const CHIP_VARIANT: Readonly<Record<Rating, 'success' | 'warning' | 'danger'>> = {
  good: 'success',
  needs_improvement: 'warning',
  poor: 'danger',
};
const RATING_LABEL: Readonly<Record<Rating, string>> = {
  good: 'good',
  needs_improvement: 'needs improvement',
  poor: 'poor',
};

type Formatter = (value: number) => string;
const seconds: Formatter = (ms) => `${formatFixed(ms / 1000, 2)} s`;
const milliseconds: Formatter = (ms) => `${formatCount(ms)} ms`;
const unitless: Formatter = (value) => formatFixed(value, 3);

/** RUM's state in this deployment, from the config options the shell already read. */
type Telemetry = 'on' | 'off' | 'pending';

function useTelemetry(): Telemetry {
  const config = useConfigOptionsQuery();
  const enabled = config.data?.rum_enabled;
  if (enabled === true) return 'on';
  if (enabled === false) return 'off';
  // Without the options read the panel still asks: the response says itself.
  return config.isError ? 'on' : 'pending';
}

function asyncQuery(query: UseQueryResult<FieldPerformanceResponse>): AsyncQuery<FieldPerformanceResponse> {
  return {
    data: query.data ?? null,
    warmingUp: null,
    error: query.error,
    manualRetry: () => void query.refetch(),
    isFetching: query.isFetching,
    isPlaceholderData: query.isPlaceholderData,
    errorUpdatedAt: query.errorUpdatedAt || null,
  };
}

function P75({ cell, format }: { cell: FieldPerformanceCell; format: Formatter }): ReactElement {
  if (cell.floor_applied || cell.p75 === null || cell.rating === null) {
    return <span className="field-perf__floor">{FLOOR_LABEL}</span>;
  }
  return (
    <span className="field-perf__cell">
      <Chip variant={CHIP_VARIANT[cell.rating]}>{format(cell.p75)}</Chip>
      <span className="sr-only">{`, ${RATING_LABEL[cell.rating]}, `}</span>
      <span className="field-perf__samples">{formatCount(cell.samples)}</span>
    </span>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }): ReactElement {
  return (
    <section className="field-perf__section" aria-label={title}>
      <SurfaceTitle level={3}>{title}</SurfaceTitle>
      {children}
    </section>
  );
}

function VitalsTable({ data }: { data: FieldPerformanceResponse }): ReactElement {
  return (
    <table className="tbl tbl--static field-perf__tbl">
      <thead>
        <tr>
          <th scope="col">Route</th>
          <th scope="col">LCP p75 · samples</th>
          <th scope="col">INP p75 · samples</th>
          <th scope="col">CLS p75 · samples</th>
        </tr>
      </thead>
      <tbody>
        {data.vitals.map((row) => (
          <tr key={row.route}>
            <th scope="row" className="field-perf__route">{row.route}</th>
            <td><P75 cell={row.lcp} format={seconds} /></td>
            <td><P75 cell={row.inp} format={milliseconds} /></td>
            <td><P75 cell={row.cls} format={unitless} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function InteractionsTable({ data }: { data: FieldPerformanceResponse }): ReactElement {
  return (
    <table className="tbl tbl--static field-perf__tbl">
      <thead>
        <tr>
          <th scope="col">Route</th>
          <th scope="col">Target</th>
          <th scope="col">INP p75 · samples</th>
          <th scope="col">Input delay</th>
          <th scope="col">Processing</th>
          <th scope="col">Presentation</th>
        </tr>
      </thead>
      <tbody>
        {data.interactions.map((row) => (
          <tr key={`${row.route}|${row.interaction_target}`}>
            <th scope="row" className="field-perf__route">{row.route}</th>
            <td className="mono">{row.interaction_target}</td>
            <td><P75 cell={row.inp} format={milliseconds} /></td>
            <td><P75 cell={row.input_delay} format={milliseconds} /></td>
            <td><P75 cell={row.processing} format={milliseconds} /></td>
            <td><P75 cell={row.presentation} format={milliseconds} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ClientErrorsTable({ data }: { data: FieldPerformanceResponse }): ReactElement {
  if (data.client_errors.length === 0) {
    return <p className="field-perf__none">No client errors reported in this window.</p>;
  }
  return (
    <table className="tbl tbl--static field-perf__tbl">
      <thead>
        <tr>
          <th scope="col">Route</th>
          <th scope="col">Error</th>
          <th scope="col">Kind</th>
          <th scope="col">Boundary</th>
          <th scope="col" className="num">Count</th>
        </tr>
      </thead>
      <tbody>
        {data.client_errors.map((row) => (
          <tr key={`${row.route}|${row.error_name}|${row.error_kind}|${row.boundary ?? '-'}`}>
            <th scope="row" className="field-perf__route">{row.route}</th>
            <td className="mono">{row.error_name}</td>
            <td>{row.error_kind}</td>
            <td>{row.boundary ?? '—'}</td>
            <td className="num">{formatCount(row.count)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Loading(): ReactElement {
  return <div className="skeleton field-perf__skeleton" data-testid="field-performance-loading" aria-hidden="true" />;
}

function Readings({ query }: { query: UseQueryResult<FieldPerformanceResponse> }): ReactElement {
  const { data } = query;
  if (!data) {
    if (query.error) return <AsyncStatus query={asyncQuery(query)} subject="Field performance" />;
    return <Loading />;
  }
  if (data.vitals.length + data.interactions.length + data.client_errors.length === 0) {
    return (
      <EmptyState
        cause="day-zero"
        title="No field measurements in this window yet."
        secondary="Rows appear about a minute after people use the app with browser telemetry on."
      />
    );
  }
  return (
    <>
      <Section title="p75 by route"><VitalsTable data={data} /></Section>
      <Section title="INP by interaction"><InteractionsTable data={data} /></Section>
      <Section title="Client errors"><ClientErrorsTable data={data} /></Section>
    </>
  );
}

function TelemetryOff(): ReactElement {
  return (
    <EmptyState
      cause="day-zero"
      title="Browser telemetry is off for this deployment."
      secondary="It is on by default for every scripts/deploy.sh deploy. To turn it on, remove MIP_RUM_ENABLED=0 from .env.local and redeploy with scripts/deploy.sh."
    />
  );
}

export default function FieldPerformancePanel(): ReactElement {
  const [days, setDays] = useState<FieldPerformanceDays>(7);
  const telemetry = useTelemetry();
  const query = useQuery({
    queryKey: queryKeys.adminFieldPerformance(days),
    queryFn: ({ signal }) => fetchFieldPerformance(days, signal),
    enabled: telemetry === 'on',
    retry: false,
    refetchOnWindowFocus: false,
    refetchInterval: false,
  });

  let body: ReactElement = <Loading />;
  if (telemetry === 'off') body = <TelemetryOff />;
  else if (telemetry === 'on') body = <Readings query={query} />;

  return (
    <>
      <div className="surface__hdr surface__hdr--split field-perf__hdr">
        <div className="field-perf__title">
          <SurfaceTitle id={FIELD_PERFORMANCE_TITLE_ID}>Field performance</SurfaceTitle>
          {telemetry !== 'pending' && (
            <Chip variant={telemetry === 'on' ? 'success' : 'neutral'}>
              {telemetry === 'on' ? 'Browser telemetry On' : 'Browser telemetry Off'}
            </Chip>
          )}
        </div>
        {telemetry === 'on' && (
          <div className="field-perf__controls">
            <FetchedAt
              at={query.dataUpdatedAt || null}
              subject="field performance"
              isFetching={query.isFetching}
              onRefresh={() => void query.refetch()}
            />
            <div className="segmented" role="group" aria-label="Field performance window">
              {SPANS.map((span) => (
                <button
                  key={span}
                  type="button"
                  className={span === days ? 'is-active' : ''}
                  aria-pressed={span === days}
                  onClick={() => setDays(span)}
                >
                  {`${span} days`}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
      <div className="surface__body field-perf__body" aria-busy={telemetry === 'on' && query.isFetching}>
        {body}
      </div>
    </>
  );
}
