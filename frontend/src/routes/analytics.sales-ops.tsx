// React Compiler memo caches add enough code here to breach the strict bundle
// budget; the analytics route family opts out. Keep this section explicit.
'use no memo';

import { lazy, Suspense, useEffect, useId, useState, type ReactNode } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router';
import { api } from '../lib/api';
import { queryKeys } from '../lib/queryKeys';
import { loanOfficersOnly, useSalesRoster } from '../lib/salesRoster';
import { Chip, SurfaceTitle } from '../components/Primitives';
import { DescribedErrorBody as ErrorBody } from '../components/ui/DescribedError';
import type {
  SalesAgingLead,
  SalesConversionResponse,
  SalesOutcomeSummaryResponse,
  SalesStandupResponse,
} from '../types';
import { UNKNOWN_VALUE, formatCount } from '../lib/formatters';
import {
  SALES_PERIOD_PRESETS,
  isoDay,
  normalizedParams,
  paramsForPeriod,
  periodEyebrow,
  resolveSalesPeriod,
  type SalesPeriod,
  type SalesPeriodPreset,
} from './analytics.sales-ops.period';
import './analytics.sales-ops.css';

/**
 * Sales ops snapshot — relocated from the Lead Queue (2026-07-10) into its own
 * Analytics tab so the LO flow (queue filters -> ranked table) is not
 * interrupted by shift-capacity / stale-approval / outcome chrome. Queries live
 * inside the component so they fire only when the tab mounts, matching the lazy
 * per-tab analytics pattern. Reuses the global `sales-ops-*` classes unchanged.
 *
 * The sales manager view (2026-09-21 audit critic-10): the conversion and
 * outcome cards follow a period held in the URL (analytics.sales-ops.period),
 * the conversion card lists EVERY loan officer, and the Distribution Board
 * (components/mortgage/DistributionBoard, lazy) is an explicit action. Every
 * read here is audit-free; nothing polls or refetches on focus.
 *
 * deviation:sales-manager-period. The period control is the prototype's
 * `.segmented` (design_files/index.html:926-935) as a role=group of
 * aria-pressed buttons, beside two native date fields for a custom range.
 */

const DistributionBoard = lazy(() => import('../components/mortgage/DistributionBoard'));
// Field and its stylesheet load only when a manager picks Custom.
const SalesPeriodRange = lazy(() => import('../components/mortgage/SalesPeriodRange'));
/** GET /sales/conversion returns at most this many groups (its LIMIT 100). */
const CONVERSION_ROW_CAP = 100;

function relativeIsoDate(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() + days);
  return isoDay(date);
}

/**
 * A snapshot headline number. Reuses the KpiCard skeleton slots
 * (`.skeleton.kpi__value-skeleton`) rather than declaring new CSS, so a
 * loading sales-ops card reads exactly like a loading KPI card elsewhere.
 * `loading` renders the shimmer; `error` renders an em-dash. Neither may
 * render a zero — a zero here is a factual claim about the book.
 */
function CardValue({
  state,
  children,
}: {
  state: 'loading' | 'error' | 'ready';
  children: ReactNode;
}) {
  if (state === 'loading') {
    return <span className="skeleton kpi__value-skeleton" aria-hidden="true" />;
  }
  return <div className="kpi__value">{state === 'error' ? '—' : children}</div>;
}

function SalesPeriodControl({ period, onChange }: {
  period: SalesPeriod;
  onChange: (preset: SalesPeriodPreset, range?: { from: string; to: string }) => void;
}) {
  const labelId = useId();
  const today = isoDay(new Date());
  return (
    <div className="sales-ops-period">
      <span className="eyebrow" id={labelId}>Period</span>
      <div className="segmented" role="group" aria-labelledby={labelId}>
        {SALES_PERIOD_PRESETS.map((preset) => (
          <button
            key={preset.value}
            type="button"
            className={preset.value === period.preset ? 'is-active' : undefined}
            aria-pressed={preset.value === period.preset}
            onClick={() => onChange(preset.value, preset.value === 'custom' ? { from: period.from, to: period.to } : undefined)}
          >
            {preset.label}
          </button>
        ))}
      </div>
      {period.preset === 'custom' && (
        <Suspense fallback={<span className="skeleton kpi__delta-skeleton sales-ops-period__pending" aria-hidden="true" />}>
          <SalesPeriodRange from={period.from} to={period.to} max={today} onApply={(range) => onChange('custom', range)} />
        </Suspense>
      )}
    </div>
  );
}

export function SalesOpsSection() {
  const yesterday = relativeIsoDate(-1);
  const [searchParams, setSearchParams] = useSearchParams();
  const period = resolveSalesPeriod(searchParams);
  const [boardOpen, setBoardOpen] = useState(false);

  // An invalid, future or over-long period reads as week to date; the URL is
  // cleaned with replace so Back never returns to the bad link.
  const cleaned = normalizedParams(searchParams);
  useEffect(() => {
    if (cleaned) setSearchParams(cleaned, { replace: true });
  });

  // A chosen period is a history entry (Back steps through periods); only
  // the normalisation above replaces.
  const choosePeriod = (preset: SalesPeriodPreset, range?: { from: string; to: string }) => {
    setSearchParams((current) => paramsForPeriod(current, preset, range));
  };

  const salesTeamQuery = useSalesRoster(loanOfficersOnly);
  const snapshotQuery = useQuery<{ staleLeads: SalesAgingLead[]; standup: SalesStandupResponse }>({
    queryKey: queryKeys.salesOps(),
    queryFn: async ({ signal }) => {
      const [agingRows, standupRows] = await Promise.all([
        api.salesAging(7, 100, signal),
        api.salesStandup(yesterday, signal),
      ]);
      return { staleLeads: agingRows, standup: standupRows };
    },
    staleTime: 30_000,
  });
  const periodQuery = useQuery<{
    /** The window these numbers are for (the previous one while a new period loads). */
    period: SalesPeriod;
    conversion: SalesConversionResponse;
    outcomes: SalesOutcomeSummaryResponse | null;
    /** The outcome read's own failure, kept apart: it degrades one card only. */
    outcomesError: unknown;
  }>({
    queryKey: [...queryKeys.salesOps(), 'period', period.from, period.to],
    queryFn: async ({ signal }) => {
      const conversion = await api.salesConversion(period.from, period.to, 'lo', signal);
      const outcomeResult = await api.salesOutcomeSummary(period.from, period.to, signal)
        .then((data) => ({ data, error: null as unknown }))
        .catch((error: unknown) => ({ data: null, error }));
      return { period, conversion, outcomes: outcomeResult.data, outcomesError: outcomeResult.error };
    },
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });

  const salesTeam = salesTeamQuery.data ?? [];
  const staleLeads = snapshotQuery.data?.staleLeads ?? [];
  const standup = snapshotQuery.data?.standup ?? null;
  const conversion = periodQuery.data?.conversion ?? null;
  const outcomes = periodQuery.data?.outcomes ?? null;
  const outcomesError = periodQuery.data?.outcomesError ?? null;
  const connectedOutcomeSources = outcomes?.source_statuses
    .filter((source) => source.source_system !== 'manual_import' && source.status === 'connected')
    ?? [];
  const dryRunOutcomeSources = outcomes?.source_statuses
    .filter((source) => source.source_system !== 'manual_import' && source.status === 'dry_run')
    ?? [];
  const dryRunOutcomeCount = dryRunOutcomeSources.reduce(
    (sum, source) => sum + (Number(source.outcome_count) || 0),
    0,
  );
  const hasDryRunOutcomeRows = dryRunOutcomeCount > 0;
  // Failures render in the shared, buyer-safe vocabulary (audit states-04),
  // never the transport message.
  const salesTeamError = salesTeamQuery.error;
  const salesOpsError = snapshotQuery.error ?? periodQuery.error;
  // Until a read resolves the `?? []` / `?? 0` fallbacks are indistinguishable
  // from real counts, and a manager who reads "STALE APPROVED 0" while 100+
  // approvals are aging has been told the opposite of the truth. Render the
  // KpiCard skeletons instead, and an em-dash (never a zero) on a failure.
  const stateOf = (query: { isPending: boolean; error: unknown }): 'loading' | 'error' | 'ready' => (
    query.isPending ? 'loading' : query.error ? 'error' : 'ready'
  );
  const snapshotState = stateOf(snapshotQuery);
  const periodState = stateOf(periodQuery);
  const snapshotLoading = snapshotState === 'loading';
  const periodLoading = periodState === 'loading';
  // keepPreviousData holds the last window's numbers while a new period
  // loads: the eyebrow names the window they are for, and the cards are busy.
  const periodBusy = periodLoading || periodQuery.isPlaceholderData;
  const shownPeriod = periodQuery.data?.period ?? period;
  // The Board is a manager action: offered once the manager-only snapshot
  // read succeeded and someone is in scope (the server enforces RBAC anyway).
  const canDistribute = snapshotQuery.isSuccess && salesTeam.length > 0;
  // Conversion rows are grouped by LO EMAIL (`group_by: 'lo'`), which is an
  // internal identifier -- the queue shows every other LO reference as
  // "Summit LO 01". Resolve through the roster the same page already loads,
  // and fall back to the raw key when the roster has no entry (a manager or a
  // deactivated LO can still own dispositions) so a name is never invented.
  const loanOfficerLabel = (groupKey: string): string => {
    const match = salesTeam.find(
      (member) => member.email.toLowerCase() === groupKey.trim().toLowerCase(),
    );
    return match?.display_label ?? groupKey;
  };
  // Every in-scope loan officer (critic-10 6e): conversion() groups call
  // dispositions, so an officer with no calls in the period has no row. The
  // rows keep the server's order, then each roster officer without one
  // follows by label as an explicit zero, never an invented rate. Past the
  // read's 100-row cap (sales_state_reporting.conversion) a missing officer
  // may have calls, so it reads as unknown instead. A failed read claims
  // nothing: no zeros and no "no dispositions" line.
  const conversionRows = conversion?.rows ?? [];
  const withCalls = new Set(conversionRows.map((row) => row.group_key.trim().toLowerCase()));
  const idleOfficers = conversion === null ? [] : salesTeam
    .filter((member) => !withCalls.has(member.email.toLowerCase()))
    .sort((a, b) => a.display_label.localeCompare(b.display_label));
  const idleValue = conversionRows.length >= CONVERSION_ROW_CAP ? UNKNOWN_VALUE : '0 calls';

  return (
    <div className="surface">
      <div className="surface__hdr surface__hdr--split">
        <div className="surface__hdr-main">
          <SurfaceTitle>Sales ops snapshot</SurfaceTitle>
          <div className="muted fs-12">
            Shift capacity, stale approvals, yesterday's activity, and imported customer-system outcomes.
          </div>
        </div>
        <div className="chip-row">
          {salesTeamQuery.isPending ? (
            <Chip variant="neutral">Loading roster…</Chip>
          ) : (
            <>
              <Chip variant="neutral">{salesTeam.length} active LOs</Chip>
              <Chip variant="neutral">
                {salesTeam.reduce((sum, member) => sum + member.capacity_per_day, 0)} daily capacity
              </Chip>
            </>
          )}
          {salesTeamError && <Chip variant="warning">Team unavailable</Chip>}
          {canDistribute && (
            <button type="button" className="btn btn--primary btn--sm" onClick={() => setBoardOpen(true)}>
              Distribute approved leads…
            </button>
          )}
        </div>
      </div>
      <div className="surface__body">
        <SalesPeriodControl period={period} onChange={choosePeriod} />
        {salesTeamError && (
          <div role="alert" className="status-callout status-callout--warning mb-3">
            Sales team unavailable: <ErrorBody error={salesTeamError} subject="the sales team" />
          </div>
        )}
        {salesOpsError && (
          <div role="alert" className="status-callout status-callout--warning mb-3">
            Sales operations metrics unavailable: <ErrorBody error={salesOpsError} subject="sales operations metrics" />
          </div>
        )}
        <div className="sales-ops-grid">
          <div className="sales-ops-card" aria-busy={snapshotLoading}>
            <div className="eyebrow">Stale approved</div>
            <CardValue state={snapshotState}>
              {staleLeads.length >= 100 ? '100+' : formatCount(staleLeads.length)}
            </CardValue>
            <div className="muted fs-12">
              Approved over 7 days ago with no LO disposition{staleLeads.length >= 100 ? '; showing first 100.' : '.'}
            </div>
            <div className="chip-row mt-2">
              <Link className="btn btn--default btn--sm" to="/lead-queue?approval_status=approved&outreach_status=queued&aged_days=7">
                Open stale queue
              </Link>
              {staleLeads.slice(0, 2).map((lead) => (
                <Link key={lead.borrower_id} className="chip chip--neutral chip--compact" to={`/borrower-360/${lead.borrower_id}`}>
                  {lead.borrower_id} · {lead.age_days}d
                </Link>
              ))}
            </div>
          </div>
          <div className="sales-ops-card" aria-busy={snapshotLoading}>
            <div className="eyebrow">Yesterday standup</div>
            <CardValue state={snapshotState}>{formatCount(standup?.calls_logged ?? 0)}</CardValue>
            {snapshotLoading ? (
              <span className="skeleton kpi__delta-skeleton" aria-hidden="true" />
            ) : (
              <div className="muted fs-12">
                {formatCount(standup?.contacts_reached ?? 0)} reached · {formatCount(standup?.callbacks_scheduled ?? 0)} callbacks · {formatCount(standup?.applications_started ?? 0)} apps.
              </div>
            )}
          </div>
          <div className="sales-ops-card" aria-busy={periodBusy}>
            <div className="eyebrow">{periodEyebrow(shownPeriod)}</div>
            {/* Every loan officer, by roster label; the internal email is never
                put in the DOM (critic-08: no title= carrying an LO address). */}
            <div className="sales-ops-list">
              {periodLoading
                ? [0, 1, 2].map((row) => (
                  <span key={row} className="skeleton kpi__delta-skeleton" aria-hidden="true" />
                ))
                : (
                  <>
                    {conversionRows.map((row) => (
                      <div key={row.group_key} className="split-row">
                        <span className="fs-12">{loanOfficerLabel(row.group_key)}</span>
                        <span className="mono num">{Math.round(row.application_start_rate * 100)}%</span>
                      </div>
                    ))}
                    {idleOfficers.map((member) => (
                      <div key={member.email} className="split-row">
                        <span className="fs-12">{member.display_label}</span>
                        <span className="mono num muted">{idleValue}</span>
                      </div>
                    ))}
                  </>
                )}
              {/* "No dispositions" is a CLAIM about the window, so it may only
                  render once the window has actually been read. */}
              {conversion !== null && conversionRows.length === 0 && (
                <div className="muted fs-12">No LO dispositions logged in this period.</div>
              )}
            </div>
          </div>
          <div className="sales-ops-card" aria-busy={periodBusy}>
            <div className="eyebrow">
              {hasDryRunOutcomeRows ? 'Outcome ledger · includes dry run' : 'Closed-loop outcomes'}
            </div>
            {/* The headline is the WINDOW TOTAL, not the funded count. It used
                to render `closed_funded`, so the card showed a bold 0 above
                its own subline reading "8 submitted" -- a card contradicting
                itself. Funded now leads the breakdown, where it can't be
                mistaken for the total. `total_outcomes` is SUM over every
                outcome type in the window (sales_state.outcome_summary). */}
            <CardValue state={periodState}>
              {outcomesError ? '--' : formatCount(outcomes?.total_outcomes ?? 0)}
            </CardValue>
            {periodLoading ? (
              <span className="skeleton kpi__delta-skeleton" aria-hidden="true" />
            ) : (
              <div className="muted fs-12">
                {outcomesError
                  ? 'Customer-system outcome counts are unavailable.'
                  : `Outcomes recorded in this period: ${formatCount(outcomes?.closed_funded ?? 0)} funded · ${formatCount(outcomes?.applications_submitted ?? 0)} submitted · ${formatCount(outcomes?.lost_to_competitor ?? 0)} lost elsewhere · ${formatCount(outcomes?.withdrawn ?? 0)} withdrawn · ${formatCount(outcomes?.not_qualified ?? 0)} not qualified.`}
              </div>
            )}
            <div className="muted fs-12 mt-1">
              Imported, read-only outcome ledger; no write-back to customer systems.
              {hasDryRunOutcomeRows
                ? ' Dry-run connector rows are included for reconciliation only; connected feeds are the live counts.'
                : ''}
            </div>
            {outcomesError ? (
              <div className="muted fs-12 mt-2">
                Outcome summary unavailable: <ErrorBody error={outcomesError} subject="the outcome summary" />
              </div>
            ) : null}
            {/* The per-type distribution moved into the caption above (funded
                first), so this list carries only what the caption does not:
                which competitors took the lost loans. */}
            {periodLoading ? null : !outcomesError && (outcomes?.total_outcomes ?? 0) > 0 ? (
              <div className="sales-ops-list mt-2">
                {(outcomes?.top_competitors ?? []).slice(0, 2).map((row) => (
                  <div key={row.competitor_lender_label} className="split-row">
                    <span className="mono fs-12">{row.competitor_lender_label}</span>
                    <span className="mono num">{row.lost_to_competitor}</span>
                  </div>
                ))}
              </div>
            ) : !outcomesError ? (
              <div className="muted fs-12 mt-2">
                {connectedOutcomeSources.length > 0
                  ? 'Connected outcome feeds are live; no funded or lost loans have been reported in this period.'
                  : dryRunOutcomeSources.length > 0
                    ? 'Outcome feeds are configured in dry run; no customer-system outcomes are being counted as live yet.'
                  : 'Customer CRM/LOS/POS outcome feeds are not configured yet; manual imports remain available for governed backfill.'}
              </div>
            ) : null}
          </div>
        </div>
      </div>
      {boardOpen && (
        <Suspense fallback={null}>
          <DistributionBoard onClose={() => setBoardOpen(false)} />
        </Suspense>
      )}
    </div>
  );
}
