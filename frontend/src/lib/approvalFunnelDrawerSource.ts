import type { DrawerSource } from '../components/AppContext';
import { DRAWER_SOURCES, enrichAsset } from './drawerSources';
import { HIGH_OPPORTUNITY_KPI_LABEL } from './opportunityScore';
import { ADDRESSABLE_POPULATION_KPI_LABEL } from './populationLabels';

/**
 * Display copy for a funnel stage. The two UC stages carry pinned frontend
 * KPI copy so the tab reads the same words as Home / Portfolio Builder; the
 * Lakebase workflow stages use the server label as-is. Shared with the KPI
 * cards so a stage's headline and its evidence drawer can never disagree.
 */
export function funnelStageDisplayLabel(stage: { stage: string; label: string }): string {
  if (stage.stage === 'high_opportunity') return HIGH_OPPORTUNITY_KPI_LABEL;
  if (stage.stage === 'population') return ADDRESSABLE_POPULATION_KPI_LABEL;
  return stage.label;
}

/** Evidence for a live approval-funnel stage count. */
export function approvalFunnelStageDrawer(stage: {
  stage: string;
  label: string;
  borrower_count: number;
  source: string;
}): DrawerSource {
  const displayLabel = funnelStageDisplayLabel(stage);
  const liveCount = {
    label: `${displayLabel} (live)`,
    source: stage.source,
    value: stage.borrower_count.toLocaleString(),
  };
  if (stage.stage === 'population' || stage.stage === 'high_opportunity') {
    const base = DRAWER_SOURCES.portfolioHeadlineView;
    return enrichAsset({
      ...base,
      title: `${displayLabel} — funnel stage`,
      description:
        stage.stage === 'population'
          ? 'COUNT(*) over the S1 headline metric view with no contactability gate — the addressable borrower book every funnel stage narrows from. The contact-eligible marketable subset is smaller.'
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
      signals: [liveCount],
    };
  }
  if (stage.stage === 'actioned') {
    return {
      title: 'Actioned — funnel stage',
      short: 'mip_app.lead_assignments',
      description:
        'Distinct borrowers whose active loan-officer assignment reached the actioned lifecycle stage (or beyond). Transitions are one-step-forward and server-enforced; each writes an audit row in the same transaction.',
      signals: [liveCount],
    };
  }
  return {
    title: 'Outcome recorded — funnel stage',
    short: 'mip_app.lead_assignments + mip_app.feedback',
    description:
      'Distinct borrowers whose assignment reached the terminal outcome_recorded stage. The recorded outcome (success / no response / declined) is a mip_app.feedback row written in the SAME transaction as the status change and its LEAD_OUTCOME_RECORDED audit event.',
    signals: [liveCount],
  };
}
