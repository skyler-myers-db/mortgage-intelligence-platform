import type { DrawerSource } from '../components/AppContext';
import { DRAWER_SOURCES, enrichAsset } from './drawerSources';

/** Evidence for a live approval-funnel stage count. */
export function approvalFunnelStageDrawer(stage: {
  stage: string;
  label: string;
  borrower_count: number;
  source: string;
}): DrawerSource {
  const liveCount = {
    label: `${stage.label} (live)`,
    source: stage.source,
    value: stage.borrower_count.toLocaleString(),
  };
  if (stage.stage === 'population' || stage.stage === 'high_opportunity') {
    const base = DRAWER_SOURCES.portfolioHeadlineView;
    return enrichAsset({
      ...base,
      title: `${stage.label} — funnel stage`,
      description:
        stage.stage === 'population'
          ? 'COUNT(*) over the S1 headline metric view — the marketable borrower population every funnel stage narrows from.'
          : 'SUM(is_high_opportunity) over the S1 headline metric view. The predicate is mip.gold.fn_high_opportunity — the canonical governed threshold, never a hardcoded literal.',
      signals: [liveCount, ...(base.signals ?? [])],
    });
  }
  if (stage.stage === 'approved') {
    return {
      title: 'Approved — funnel stage',
      short: 'mip_app.approvals + mip_app.lead_assignments',
      description:
        'Distinct borrowers with a human approve decision in the Lakebase approvals ledger or an active assignment at-or-past the approved lifecycle stage. Every decision row carries the approver identity and its audit event.',
      lineage: [
        { layer: 'LAKEBASE', name: 'mip_app.approvals', meta: "action = 'approve' · actor_email is the approver" },
        { layer: 'LAKEBASE', name: 'mip_app.lead_assignments', meta: "status ≥ 'approved' (S2 lifecycle)" },
        { layer: 'LAKEBASE', name: 'mip_app.action_audit', meta: 'APPROVE / LEAD_ASSIGNMENT_STATUS events' },
      ],
      signals: [liveCount],
    };
  }
  if (stage.stage === 'actioned') {
    return {
      title: 'Actioned — funnel stage',
      short: 'mip_app.lead_assignments',
      description:
        'Distinct borrowers whose active loan-officer assignment reached the actioned lifecycle stage (or beyond). Transitions are one-step-forward and server-enforced; each writes an audit row in the same transaction.',
      lineage: [
        { layer: 'LAKEBASE', name: 'mip_app.lead_assignments', meta: "status ≥ 'actioned' (S2 lifecycle)" },
        { layer: 'LAKEBASE', name: 'mip_app.action_audit', meta: 'LEAD_ASSIGNMENT_STATUS events' },
      ],
      signals: [liveCount],
    };
  }
  return {
    title: 'Outcome recorded — funnel stage',
    short: 'mip_app.lead_assignments + mip_app.feedback',
    description:
      'Distinct borrowers whose assignment reached the terminal outcome_recorded stage. The recorded outcome (success / no response / declined) is a mip_app.feedback row written in the SAME transaction as the status change and its LEAD_OUTCOME_RECORDED audit event.',
    lineage: [
      { layer: 'LAKEBASE', name: 'mip_app.lead_assignments', meta: "status = 'outcome_recorded'" },
      { layer: 'LAKEBASE', name: 'mip_app.feedback', meta: 'event_type = assignment_outcome_*' },
      { layer: 'LAKEBASE', name: 'mip_app.action_audit', meta: 'LEAD_OUTCOME_RECORDED events' },
    ],
    signals: [liveCount],
  };
}
