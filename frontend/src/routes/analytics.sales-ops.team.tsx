import { KpiCard } from '../components/mortgage/KpiCard';
import { DRAWER_SOURCES } from '../lib/drawerSources';
import { formatCount, formatPercent, signedCount, signedPct } from '../lib/formatters';
import type { SalesConversionResponse } from '../types';

/**
 * The sales manager's team comparison (2026-09-21 audit critic-10 item 6b,
 * W5d w5-sales-loop CUT 1; deviation:sales-manager-comparison-workload). The
 * period read also asks GET /sales/conversion?groupBy=cohort for the period
 * and for the same number of days before it: per-officer unique counts are
 * NOT additive (a borrower two officers reached counts once for the team),
 * so the team totals come only from the cohort rows, never a sum of LO rows.
 * Audit-free, no polling, no focus refetch.
 */

/** The team's period totals, or null when the read failed. */
export interface TeamTotals {
  calls: number;
  contacts: number;
  starts: number;
  /** Application starts per contact reached, or null with no contact. */
  rate: number | null;
}

/** The cohort read's one row (zeros for a window with no disposition). */
export function teamTotals(response: SalesConversionResponse | null): TeamTotals | null {
  if (response === null) return null;
  const row = response.rows.find((candidate) => candidate.group_key === 'all_cohorts') ?? response.rows[0];
  const calls = row?.calls_attempted ?? 0;
  const contacts = row?.unique_contacts_reached ?? 0;
  const starts = row?.unique_application_starts ?? 0;
  return { calls, contacts, starts, rate: contacts > 0 ? starts / contacts : null };
}

export interface TeamDelta {
  text: string;
  dir: 'up' | 'down' | 'flat';
}

function versus(days: number): string {
  return days === 1 ? 'vs previous day' : `vs previous ${formatCount(days)} days`;
}

/** A count's change: signed through signedCount, "new" from zero, "no change". */
export function countDelta(current: number, previous: number, days: number): TeamDelta {
  if (current === previous) return { text: `no change ${versus(days)}`, dir: 'flat' };
  if (previous === 0) return { text: `new ${versus(days)}`, dir: 'up' };
  return { text: `${signedCount(current - previous)} ${versus(days)}`, dir: current > previous ? 'up' : 'down' };
}

/** A rate's change in percentage points ("+1.2 pts"). */
export function rateDelta(current: number | null, previous: number | null, days: number): TeamDelta | null {
  if (current === null) return null;
  if (previous === null) return { text: `new ${versus(days)}`, dir: 'up' };
  const points = (current - previous) * 100;
  if (Math.abs(points) < 0.05) return { text: `no change ${versus(days)}`, dir: 'flat' };
  return { text: `${signedPct(points, 1).slice(0, -1)} pts ${versus(days)}`, dir: points > 0 ? 'up' : 'down' };
}

interface TeamComparisonProps {
  current: TeamTotals | null;
  previous: TeamTotals | null;
  days: number;
  loading: boolean;
}

/** The team row: four cards, skeletons (never zeros) while loading, an em-dash on failure. */
export function SalesTeamComparison({ current, previous, days, loading }: TeamComparisonProps) {
  const source = DRAWER_SOURCES.callDispositions;
  const card = (label: string, value: number | null, delta: TeamDelta | null, format: (n: number) => string = formatCount) => (
    <KpiCard
      label={label}
      valueAnimated={current === null ? null : value}
      format={format}
      delta={delta?.text}
      deltaDir={delta?.dir ?? 'flat'}
      loading={loading}
      source={source}
    />
  );
  const both = current !== null && previous !== null;
  return (
    <div className="kpi-row mb-3" role="group" data-testid="sales-team-comparison" aria-label="Team totals for this period">
      {card('Calls attempted', current?.calls ?? null, both ? countDelta(current.calls, previous.calls, days) : null)}
      {card('Contacts reached', current?.contacts ?? null, both ? countDelta(current.contacts, previous.contacts, days) : null)}
      {card('Application starts', current?.starts ?? null, both ? countDelta(current.starts, previous.starts, days) : null)}
      {card('Start rate', current?.rate ?? null, both ? rateDelta(current.rate, previous.rate, days) : null, (n) => formatPercent(n, 1))}
    </div>
  );
}
