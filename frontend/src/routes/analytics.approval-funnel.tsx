// React Compiler memo caches add enough code here to breach the strict bundle
// budget; the analytics route family opts out. Keep this section explicit.
'use no memo';

import { Fragment, useState } from 'react';
import { Link } from 'react-router';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { queryKeys } from '../lib/queryKeys';
import { useWarmingUpRetry } from '../lib/useWarmingUpRetry';
import { KpiCard } from '../components/mortgage/KpiCard';
import { Chip } from '../components/Primitives';
import {
  assignmentStatusLabel,
  assignmentStatusVariant,
} from '../components/mortgage/LeadTable.logic';
import { approvalFunnelStageDrawer } from '../lib/approvalFunnelDrawerSource';
import { HIGH_OPPORTUNITY_KPI_LABEL } from '../lib/opportunityScore';
import { formatTimestamp } from '../lib/time';
import { offerDisplayLabel } from '../lib/offerLanguage';
import type {
  ApprovalFunnelResponse,
  ApprovalFunnelStage,
  LoanOfficerFunnelDetailResponse,
  LoanOfficerFunnelRow,
} from '../types';
import { formatConversionPct } from './analytics.lib';
import { DataTable, LoadState } from './analytics.charts';

/**
 * S6 approval-funnel tab — live counts only, spanning both data planes:
 * population + high-opportunity from the S1 headline metric view, and
 * approved / actioned / outcome_recorded from live Lakebase workflow state.
 * Every stage number carries an evidence source that opens the drawer with
 * the exact objects the count was computed from.
 */

function stageDisplayLabel(stage: ApprovalFunnelStage): string {
  // Keep the S1 canonical high-opportunity KPI copy (threshold text comes
  // from the pinned frontend constant, never restated here).
  return stage.stage === 'high_opportunity' ? HIGH_OPPORTUNITY_KPI_LABEL : stage.label;
}

function FunnelStages({ stages }: { stages: ApprovalFunnelStage[] }) {
  const ordered = [...stages].sort((a, b) => a.stage_order - b.stage_order);
  return (
    <div className="kpi-row">
      {ordered.map((stage, idx) => {
        const prev = idx > 0 ? ordered[idx - 1] : null;
        const conversion = prev && prev.borrower_count > 0
          ? formatConversionPct(stage.borrower_count / prev.borrower_count)
          : null;
        return (
          <KpiCard
            key={stage.stage}
            label={stageDisplayLabel(stage)}
            valueAnimated={stage.borrower_count}
            delta={conversion ? `${conversion} of ${stageDisplayLabel(prev!).toLowerCase()}` : undefined}
            deltaDir="flat"
            source={approvalFunnelStageDrawer(stage)}
          />
        );
      })}
    </div>
  );
}

function WhoApprovedWhat({ data }: { data: ApprovalFunnelResponse }) {
  return (
    <section className="surface analytics-section">
      <div className="surface__hdr surface__hdr--split">
        <div>
          <h2 className="h-3">Who approved what</h2>
          <p className="analytics-panel-note">
            Every approve decision from the Lakebase approvals ledger, attributed to the
            edge-authenticated approver. Rejections and holds never enter the funnel.
          </p>
        </div>
        <Chip variant="neutral">{data.approvals.length} recent</Chip>
      </div>
      <div className="surface__body">
        {data.approvals.length === 0 ? (
          <div className="analytics-empty" role="status">
            No approvals recorded yet. Approve a lead from the Lead Queue or Offer
            Orchestrator and it appears here with the approver identity.
          </div>
        ) : (
          <DataTable
            rows={data.approvals}
            getKey={(row) => row.approval_id}
            columns={[
              { key: 'approver', label: 'Approver', render: (row) => <span className="mono fs-12">{row.actor_email}</span> },
              {
                key: 'borrower',
                label: 'Borrower',
                render: (row) => (
                  <Link className="mono" to={`/borrower-360/${row.borrower_id}`}>{row.borrower_id}</Link>
                ),
              },
              { key: 'offer', label: 'Offer', render: (row) => offerDisplayLabel(row.offer_code) },
              {
                key: 'routed',
                label: 'Routed to',
                render: (row) => row.assigned_to_email
                  ? <span className="mono fs-12">{row.assigned_to_email}</span>
                  : <span className="muted">—</span>,
              },
              { key: 'decided', label: 'Decided', render: (row) => formatTimestamp(row.decided_at) },
            ]}
          />
        )}
      </div>
    </section>
  );
}

function LoanOfficerDrill({ loanOfficerId }: { loanOfficerId: string }) {
  const detailQuery = useQuery<LoanOfficerFunnelDetailResponse>({
    queryKey: queryKeys.analytics('funnel-loan-officer', [loanOfficerId]),
    queryFn: ({ signal }) => api.approvalFunnelLoanOfficer(loanOfficerId, signal),
    staleTime: 15_000,
  });
  if (detailQuery.isPending) {
    return <div className="analytics-empty" role="status">Loading officer detail…</div>;
  }
  if (detailQuery.isError || !detailQuery.data) {
    return (
      <div className="status-callout status-callout--warning" role="alert">
        Officer drill-down unavailable: {detailQuery.error instanceof Error ? detailQuery.error.message : 'request failed'}
      </div>
    );
  }
  const detail = detailQuery.data;
  return (
    <div className="tbl__expand-inner" data-testid="funnel-lo-drill">
      <div className="chip-row mb-2">
        <Chip variant="success">{detail.outcome_counts.success} success</Chip>
        <Chip variant="warning">{detail.outcome_counts.no_response} no response</Chip>
        <Chip variant="danger">{detail.outcome_counts.declined} declined</Chip>
      </div>
      {detail.assignments.length === 0 ? (
        <div className="analytics-empty" role="status">
          No active assignments for {detail.officer.display_name} yet. Assign a lead
          from the Lead Queue to start their pipeline.
        </div>
      ) : (
        <DataTable
          rows={detail.assignments}
          getKey={(row) => row.assignment_id}
          columns={[
            {
              key: 'borrower',
              label: 'Borrower',
              render: (row) => (
                <Link className="mono" to={`/borrower-360/${row.borrower_id}`}>{row.borrower_id}</Link>
              ),
            },
            {
              key: 'status',
              label: 'Lifecycle stage',
              render: (row) => (
                <Chip variant={assignmentStatusVariant(row.status)}>
                  {assignmentStatusLabel(row.status)}
                </Chip>
              ),
            },
            { key: 'assigned_by', label: 'Assigned by', render: (row) => <span className="mono fs-12">{row.assigned_by}</span> },
            {
              key: 'updated',
              label: 'Last update',
              render: (row) => formatTimestamp(row.status_updated_at ?? row.assigned_at),
            },
          ]}
        />
      )}
    </div>
  );
}

function PerLoanOfficerFunnel({ officers }: { officers: LoanOfficerFunnelRow[] }) {
  const [selected, setSelected] = useState<string | null>(null);
  return (
    <section className="surface analytics-section">
      <div className="surface__hdr surface__hdr--split">
        <div>
          <h2 className="h-3">Per-loan-officer funnel</h2>
          <p className="analytics-panel-note">
            Active assignments by lifecycle stage for every active loan officer.
            Officers without assignments stay visible with honest zeros.
          </p>
        </div>
      </div>
      <div className="surface__body">
        {officers.length === 0 ? (
          <div className="analytics-empty" role="status">
            No active loan officers in the roster yet. Seed the sales team to
            enable assignment routing.
          </div>
        ) : (
          <div className="analytics-table-wrap">
            <table className="analytics-table">
              <thead>
                <tr>
                  <th>Loan officer</th>
                  <th>Assigned</th>
                  <th>Contact drafted</th>
                  <th>Approved</th>
                  <th>Actioned</th>
                  <th>Outcome recorded</th>
                  <th>Total active</th>
                  <th aria-label="Drill down" />
                </tr>
              </thead>
              <tbody>
                {officers.map((officer) => (
                  <Fragment key={officer.loan_officer_id}>
                    <tr>
                      <td>
                        {officer.display_name}
                        <div className="muted mono fs-11">{officer.email}</div>
                      </td>
                      <td className="mono num">{officer.assigned.toLocaleString()}</td>
                      <td className="mono num">{officer.contact_drafted.toLocaleString()}</td>
                      <td className="mono num">{officer.approved.toLocaleString()}</td>
                      <td className="mono num">{officer.actioned.toLocaleString()}</td>
                      <td className="mono num">{officer.outcome_recorded.toLocaleString()}</td>
                      <td className="mono num">{officer.total_active.toLocaleString()}</td>
                      <td>
                        <button
                          type="button"
                          className="btn btn--ghost btn--sm"
                          aria-expanded={selected === officer.loan_officer_id}
                          onClick={() => setSelected(
                            selected === officer.loan_officer_id ? null : officer.loan_officer_id,
                          )}
                        >
                          {selected === officer.loan_officer_id ? 'Hide' : 'Drill'}
                        </button>
                      </td>
                    </tr>
                    {selected === officer.loan_officer_id && (
                      <tr className="tbl__expand">
                        <td colSpan={8}>
                          <LoanOfficerDrill loanOfficerId={officer.loan_officer_id} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}

export function ApprovalFunnelSection() {
  const funnel = useWarmingUpRetry<ApprovalFunnelResponse>(
    (signal) => api.approvalFunnel(signal),
    ['analytics', 'funnel'],
    {
      queryKey: queryKeys.analytics('funnel'),
      keepPreviousData: true,
      // Short stale time: the backend invalidates its own funnel cache on
      // every approval/lifecycle write, so a refetch after an approval
      // reflects the change within one cache TTL.
      staleTime: 15_000,
      refetchOnWindowFocus: true,
    },
  );
  return (
    <LoadState query={funnel} title="Approval funnel">
      {(data) => (
        <>
          <section className="surface analytics-section">
            <div className="surface__hdr surface__hdr--split">
              <div>
                <h2 className="h-3">Approval funnel — live</h2>
                <p className="analytics-panel-note">
                  Population and {HIGH_OPPORTUNITY_KPI_LABEL.toLowerCase()} aggregate over the
                  governed headline metric view; approved, actioned, and outcome recorded read
                  live Lakebase workflow state. Open any number&apos;s source chip for its evidence.
                </p>
              </div>
              <Link className="btn btn--sm" to="/lead-queue">Open queue</Link>
            </div>
            <div className="surface__body">
              <FunnelStages stages={data.stages} />
            </div>
          </section>
          <WhoApprovedWhat data={data} />
          <PerLoanOfficerFunnel officers={data.loan_officers} />
        </>
      )}
    </LoadState>
  );
}
