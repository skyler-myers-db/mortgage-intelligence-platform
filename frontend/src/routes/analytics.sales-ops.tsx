// React Compiler memo caches add enough code here to breach the strict bundle
// budget; the analytics route family opts out. Keep this section explicit.
'use no memo';

import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router';
import { api } from '../lib/api';
import { queryKeys } from '../lib/queryKeys';
import { Chip } from '../components/Primitives';
import type {
  SalesAgingLead,
  SalesConversionResponse,
  SalesOutcomeSummaryResponse,
  SalesStandupResponse,
  SalesTeamMember,
} from '../types';

/**
 * Sales ops snapshot — relocated from the Lead Queue (2026-07-10) into its own
 * Analytics tab so the LO flow (queue filters -> ranked table) is not
 * interrupted by shift-capacity / stale-approval / outcome chrome. Queries live
 * inside the component so they fire only when the tab mounts, matching the lazy
 * per-tab analytics pattern. Reuses the global `sales-ops-*` classes unchanged.
 */

function isoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function relativeIsoDate(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() + days);
  return isoDate(date);
}

function weekStartIsoDate(): string {
  const date = new Date();
  const day = date.getDay();
  const diff = day === 0 ? -6 : 1 - day;
  date.setDate(date.getDate() + diff);
  return isoDate(date);
}

export function SalesOpsSection() {
  const yesterday = relativeIsoDate(-1);
  const weekStart = weekStartIsoDate();
  const today = isoDate(new Date());

  const salesTeamQuery = useQuery<SalesTeamMember[]>({
    queryKey: queryKeys.salesTeam(),
    queryFn: ({ signal }) => api.salesTeam(signal).then((team) => team.filter((member) => member.role === 'loan_officer')),
    staleTime: 60_000,
  });
  const salesOpsQuery = useQuery<{
    staleLeads: SalesAgingLead[];
    standup: SalesStandupResponse;
    conversion: SalesConversionResponse;
    outcomes: SalesOutcomeSummaryResponse | null;
    outcomesError: string | null;
  }>({
    queryKey: queryKeys.salesOps(),
    queryFn: async ({ signal }) => {
      const [agingRows, standupRows, conversionRows] = await Promise.all([
        api.salesAging(7, 100, signal),
        api.salesStandup(yesterday, signal),
        api.salesConversion(weekStart, today, 'lo', signal),
      ]);
      const outcomeResult = await api.salesOutcomeSummary(weekStart, today, signal)
        .then((data) => ({ data, error: null as string | null }))
        .catch((error: unknown) => ({
          data: null,
          error: error instanceof Error ? error.message : 'Outcome summary unavailable',
        }));
      return {
        staleLeads: agingRows,
        standup: standupRows,
        conversion: conversionRows,
        outcomes: outcomeResult.data,
        outcomesError: outcomeResult.error,
      };
    },
    staleTime: 30_000,
  });

  const salesTeam = salesTeamQuery.data ?? [];
  const staleLeads = salesOpsQuery.data?.staleLeads ?? [];
  const standup = salesOpsQuery.data?.standup ?? null;
  const conversion = salesOpsQuery.data?.conversion ?? null;
  const outcomes = salesOpsQuery.data?.outcomes ?? null;
  const outcomesError = salesOpsQuery.data?.outcomesError ?? null;
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
  const salesTeamError = salesTeamQuery.error instanceof Error ? salesTeamQuery.error.message : null;
  const salesOpsError = salesOpsQuery.error instanceof Error ? salesOpsQuery.error.message : null;
  const outcomeDistribution = outcomes
    ? [
      { label: 'submitted', value: outcomes.applications_submitted },
      { label: 'funded', value: outcomes.closed_funded },
      { label: 'lost elsewhere', value: outcomes.lost_to_competitor },
      { label: 'withdrawn', value: outcomes.withdrawn },
      { label: 'not qualified', value: outcomes.not_qualified },
    ]
    : [];

  return (
    <div className="surface">
      <div className="surface__hdr surface__hdr--split">
        <div className="surface__hdr-main">
          <div className="h-4">Sales ops snapshot</div>
          <div className="muted fs-12">
            Shift capacity, stale approvals, yesterday's activity, and imported customer-system outcomes.
          </div>
        </div>
        <div className="chip-row">
          <Chip variant="neutral">{salesTeam.length} active LOs</Chip>
          <Chip variant="neutral">
            {salesTeam.reduce((sum, member) => sum + member.capacity_per_day, 0)} daily capacity
          </Chip>
          {salesTeamError && <Chip variant="warning">Team unavailable</Chip>}
        </div>
      </div>
      <div className="surface__body">
        {salesTeamError && (
          <div role="alert" className="status-callout status-callout--warning mb-3">
            Sales team unavailable: {salesTeamError}
          </div>
        )}
        {salesOpsError && (
          <div role="alert" className="status-callout status-callout--warning mb-3">
            Sales operations metrics unavailable: {salesOpsError}
          </div>
        )}
        <div className="sales-ops-grid">
          <div className="sales-ops-card">
            <div className="eyebrow">Stale approved</div>
            <div className="kpi__value">{staleLeads.length >= 100 ? '100+' : staleLeads.length.toLocaleString()}</div>
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
          <div className="sales-ops-card">
            <div className="eyebrow">Yesterday standup</div>
            <div className="kpi__value">{(standup?.calls_logged ?? 0).toLocaleString()}</div>
            <div className="muted fs-12">
              {(standup?.contacts_reached ?? 0).toLocaleString()} reached · {(standup?.callbacks_scheduled ?? 0).toLocaleString()} callbacks · {(standup?.applications_started ?? 0).toLocaleString()} apps.
            </div>
          </div>
          <div className="sales-ops-card">
            <div className="eyebrow">Week-to-date conversion</div>
            <div className="sales-ops-list">
              {(conversion?.rows ?? []).slice(0, 3).map((row) => (
                <div key={row.group_key} className="split-row">
                  <span className="mono fs-12">{row.group_key}</span>
                  <span className="mono num">{Math.round(row.application_start_rate * 100)}%</span>
                </div>
              ))}
              {(conversion?.rows ?? []).length === 0 && (
                <div className="muted fs-12">No LO dispositions logged this week.</div>
              )}
            </div>
          </div>
          <div className="sales-ops-card">
            <div className="eyebrow">
              {hasDryRunOutcomeRows ? 'Outcome ledger · includes dry run' : 'Closed-loop outcomes'}
            </div>
            <div className="kpi__value">{outcomesError ? '--' : (outcomes?.closed_funded ?? 0).toLocaleString()}</div>
            <div className="muted fs-12">
              {outcomesError
                ? 'Customer-system outcome counts are unavailable.'
                : `${(outcomes?.applications_submitted ?? 0).toLocaleString()} submitted · ${(outcomes?.lost_to_competitor ?? 0).toLocaleString()} lost elsewhere · ${(outcomes?.withdrawn ?? 0).toLocaleString()} withdrawn · ${(outcomes?.not_qualified ?? 0).toLocaleString()} not qualified this week.`}
            </div>
            <div className="muted fs-12 mt-1">
              Imported, read-only outcome ledger; no write-back to customer systems.
              {hasDryRunOutcomeRows
                ? ' Dry-run connector rows are included for reconciliation only; connected feeds are the live counts.'
                : ''}
            </div>
            {outcomesError ? (
              <div className="muted fs-12 mt-2">
                Outcome summary unavailable: {outcomesError}
              </div>
            ) : null}
            {!outcomesError && (outcomes?.total_outcomes ?? 0) > 0 ? (
              <div className="sales-ops-list mt-2">
                {outcomeDistribution.map((row) => (
                  <div key={row.label} className="split-row">
                    <span className="mono fs-12">{row.label}</span>
                    <span className="mono num">{row.value.toLocaleString()}</span>
                  </div>
                ))}
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
                  ? 'Connected outcome feeds are live; no funded or lost loans have been reported this week.'
                  : dryRunOutcomeSources.length > 0
                    ? 'Outcome feeds are configured in dry run; no customer-system outcomes are being counted as live yet.'
                  : 'Customer CRM/LOS/POS outcome feeds are not configured yet; manual imports remain available for governed backfill.'}
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}
