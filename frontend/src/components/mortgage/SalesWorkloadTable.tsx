import { useState, type ReactNode } from 'react';
import { Link } from 'react-router';
import { Icon } from '../Icon';
import { DescribedErrorBody } from '../ui/DescribedError';
import type { SalesWorkloadResponse, SalesWorkloadRow } from '../../lib/apiClients/salesWorkload';
import { formatCount, formatPercent, signedCount, UNKNOWN_VALUE } from '../../lib/formatters';
import type { SalesConversionResponse } from '../../types';
import './SalesWorkloadTable.css';

/**
 * The per-officer workload (2026-09-21 audit critic-10 item 6c, W5d
 * w5-sales-loop CUT 2; deviation:sales-manager-comparison-workload): EVERY
 * in-scope loan officer with their open and stale assignments, what they were
 * assigned and worked in the period (with the change against the same span
 * before it), and their application starts from the period's per-officer
 * conversion rows. The prototype's `.tbl` (design_files/index.html:595) with
 * its `.tbl__sort` headers.
 *
 * One audit-free GET /api/sales/workload (Lakebase only), owned by the Sales
 * ops tab so it starts beside this chunk's download, not after it: staleTime
 * 30 s, no focus refetch, no interval (the Distribution Board refetches this
 * key after a commit). Lazy: its own chunk, loaded with the Sales ops tab,
 * importing no transport module (see lib/apiClients/salesWorkload.ts). The
 * officer cell is a link into the Lead Queue's assigned_to filter, labelled
 * with the roster's display label; the email is never visible text, a title
 * or an accessible name.
 */

type SortKey = 'capacity' | 'open' | 'stale' | 'assigned' | 'worked' | 'starts' | 'rate';
type SortDir = 'asc' | 'desc';

type ConversionRow = SalesConversionResponse['rows'][number];

interface WorkloadLine {
  row: SalesWorkloadRow;
  starts: number | null;
  rate: number | null;
}

const COLUMNS: ReadonlyArray<{ key: SortKey; label: string }> = [
  { key: 'capacity', label: 'Capacity/day' },
  { key: 'open', label: 'Open' },
  { key: 'stale', label: 'Stale' },
  { key: 'assigned', label: 'Assigned' },
  { key: 'worked', label: 'Worked' },
  { key: 'starts', label: 'App starts' },
  { key: 'rate', label: 'Start rate' },
];

function sortValue(line: WorkloadLine, key: SortKey): number {
  switch (key) {
    case 'capacity':
      return line.row.capacity_per_day;
    case 'open':
      return line.row.open_count;
    case 'stale':
      return line.row.stale_count;
    case 'assigned':
      return line.row.assigned_count;
    case 'worked':
      return line.row.worked_count;
    case 'starts':
      return line.starts ?? -1;
    default:
      return line.rate ?? -1;
  }
}

/** Sorted by the column, ties by the officer's label (then the roster key, never shown). */
function sortWorkload(lines: readonly WorkloadLine[], key: SortKey, dir: SortDir): WorkloadLine[] {
  const sign = dir === 'desc' ? -1 : 1;
  return [...lines].sort((a, b) =>
    (sortValue(a, key) - sortValue(b, key)) * sign
    || a.row.display_label.localeCompare(b.row.display_label)
    || a.row.email.localeCompare(b.row.email),
  );
}

function withConversion(rows: readonly SalesWorkloadRow[], conversion: readonly ConversionRow[] | null): WorkloadLine[] {
  const byEmail = new Map((conversion ?? []).map((line) => [line.group_key.trim().toLowerCase(), line]));
  return rows.map((row) => {
    const match = byEmail.get(row.email.toLowerCase());
    return {
      row,
      starts: match ? (match.unique_application_starts ?? match.applications_started) : null,
      rate: match ? match.application_start_rate : null,
    };
  });
}

function Change({ current, previous, versus }: { current: number; previous: number; versus: string }) {
  const diff = current - previous;
  return (
    <>
      <span className="num">{formatCount(current)}</span>{' '}
      <span className={`sales-workload__delta${diff > 0 ? ' is-up' : diff < 0 ? ' is-down' : ''}`}>
        {signedCount(diff)}
        <span className="sr-only"> {versus}</span>
      </span>
    </>
  );
}

/** The tab's workload read; a TanStack query result satisfies it. */
export interface SalesWorkloadState {
  isError: boolean;
  error: unknown;
  /** Undefined until the first response lands (rendered as skeletons). */
  data: SalesWorkloadResponse | undefined;
}

export interface SalesWorkloadTableProps {
  workload: SalesWorkloadState;
  /** The period's length, for "vs previous N days". */
  days: number;
  /** The period's per-officer conversion rows (null while that read is pending or failed). */
  conversion: readonly ConversionRow[] | null;
}

export default function SalesWorkloadTable({ workload, days, conversion }: SalesWorkloadTableProps) {
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>({ key: 'open', dir: 'desc' });
  const data = workload.data;
  const versus = days === 1 ? 'vs previous day' : `vs previous ${formatCount(days)} days`;
  const choose = (key: SortKey) =>
    setSort((current) => ({ key, dir: current.key === key && current.dir === 'desc' ? 'asc' : 'desc' }));

  let body: ReactNode;
  if (workload.isError) {
    body = (
      <tr>
        <td colSpan={COLUMNS.length + 2}>
          <span role="alert" className="status-callout status-callout--warning sales-workload__status">
            Workload unavailable: <DescribedErrorBody error={workload.error} subject="the officer workload" />
          </span>
        </td>
      </tr>
    );
  } else if (data === undefined) {
    // Skeleton rows, never a zero, before the read settles (the row and its
    // one text-free cell are both hidden from assistive technology).
    body = [0, 1, 2].map((index) => (
      <tr key={index} aria-hidden="true">
        <td colSpan={COLUMNS.length + 2} aria-hidden="true"><span className="skeleton kpi__delta-skeleton" /></td>
      </tr>
    ));
  } else if (data.rows.length === 0) {
    body = (
      <tr>
        <td colSpan={COLUMNS.length + 2} className="muted">No active loan officers in your scope.</td>
      </tr>
    );
  } else {
    body = sortWorkload(withConversion(data.rows, conversion), sort.key, sort.dir).map(({ row, starts, rate }) => (
      <tr key={row.email}>
        <td className="is-primary">
          <Link to={`/lead-queue?assigned_to=${encodeURIComponent(row.email)}`}>{row.display_label}</Link>
        </td>
        <td>{row.region ?? UNKNOWN_VALUE}</td>
        <td className="num">{formatCount(row.capacity_per_day)}</td>
        <td className="num">{formatCount(row.open_count)}</td>
        <td className="num">{formatCount(row.stale_count)}</td>
        <td><Change current={row.assigned_count} previous={row.previous_assigned_count} versus={versus} /></td>
        <td><Change current={row.worked_count} previous={row.previous_worked_count} versus={versus} /></td>
        <td className="num">{starts === null ? UNKNOWN_VALUE : formatCount(starts)}</td>
        <td className="num">{rate === null ? UNKNOWN_VALUE : formatPercent(rate, 1)}</td>
      </tr>
    ));
  }

  return (
    <section className="sales-workload" aria-labelledby="sales-workload-title" data-testid="sales-workload">
      <h3 className="eyebrow" id="sales-workload-title">Workload by loan officer</h3>
      <p className="muted fs-12 sales-workload__note">
        Open and stale are now (stale: assigned at least {formatCount(data?.stale_days ?? 7)} days ago with no call since);
        assigned and worked are this period, with the change {versus}.
      </p>
      <div className="sales-workload__wrap">
        <table className="tbl sales-workload__tbl">
          <thead>
            <tr>
              <th scope="col">Loan officer</th>
              <th scope="col">Region</th>
              {COLUMNS.map((column) => {
                const active = sort.key === column.key;
                return (
                  <th
                    key={column.key}
                    scope="col"
                    className="num"
                    aria-sort={active ? (sort.dir === 'desc' ? 'descending' : 'ascending') : 'none'}
                  >
                    <button
                      type="button"
                      className="tbl__sort"
                      onClick={() => choose(column.key)}
                      aria-label={`Sort by ${column.label}`}
                      aria-pressed={active}
                    >
                      <span>{column.label}</span>
                      {active && <Icon name={sort.dir === 'desc' ? 'down' : 'up'} size={10} />}
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>{body}</tbody>
        </table>
      </div>
    </section>
  );
}
