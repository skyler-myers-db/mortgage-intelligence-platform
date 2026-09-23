import type { IconName } from '../Icon';
import type { LeadSummary } from '../../types';
import {
  assignmentStatusLabel,
  assignmentStatusVariant,
  dispositionLabel,
  dispositionVariant,
  outreachLabel,
  outreachVariant,
  relationshipLabel,
  relationshipVariant,
} from './LeadTable.logic';

export type LeadChipVariant = 'success' | 'warning' | 'neutral' | 'danger';

/**
 * One non-default workflow state of a lead, in the order the merged Status
 * cell ranks them (audit visual-01 / tables-04). The cell shows the first
 * entry as its one primary chip and folds the rest into a `+n` overflow whose
 * evidence hover-card lists them. Default values (relationship "Other",
 * unassigned, outreach "none", untouched) are NOT states: a lead with none
 * renders an em dash, never "Other / Unassigned / None / Untouched" chips.
 */
export interface LeadStatusEntry {
  key: 'last_touch' | 'outreach' | 'assignment' | 'assignment_stage' | 'relationship' | 'aging' | 'multi_owner';
  /** Field name used in the hover card and accessible names ("Outreach"). */
  field: string;
  /** Chip text ("Sent"). */
  label: string;
  /**
   * A fact the chip text leaves out for width: the assignment's lifecycle
   * stage ("Contact drafted"). Screen readers hear it with the chip, and a
   * `+n` that folds the entry lists it with the label.
   */
  qualifier?: string;
  variant: LeadChipVariant;
  /** Full sentence for `title` and screen readers. */
  detail: string;
  /** The table or view the value is read from, for the `+n` hover card. */
  source: string;
}

/** Where each workflow value comes from (the `/api/leads` read and its Lakebase overlay). */
const BORROWER_360 = 'mip.gold.borrower_360';
const LIFECYCLE_STATE = 'mip.gold.borrower_lifecycle_state';
const LEAD_ASSIGNMENTS = 'mip_app.lead_assignments';
const CALL_DISPOSITIONS = 'mip_app.call_dispositions';

/**
 * Priority is lifecycle recency: the latest human contact outcome, then what
 * went out, then who owns the lead, then first-party relationship context,
 * then the aging warning and owner-count caveat.
 */
export function leadWorkflowStates(lead: LeadSummary): LeadStatusEntry[] {
  const states: LeadStatusEntry[] = [];
  if (lead.latest_disposition_outcome) {
    const label = dispositionLabel(lead.latest_disposition_outcome);
    states.push({
      key: 'last_touch',
      field: 'Last touch',
      label,
      variant: dispositionVariant(lead.latest_disposition_outcome),
      detail: `Last touch: ${label}`,
      source: CALL_DISPOSITIONS,
    });
  }
  if (lead.outreach_status && lead.outreach_status !== 'none') {
    const label = outreachLabel(lead.outreach_status);
    states.push({
      key: 'outreach',
      field: 'Outreach',
      label,
      variant: outreachVariant(lead.outreach_status),
      detail: `Outreach: ${label}`,
      source: LIFECYCLE_STATE,
    });
  }
  if (lead.assigned_to_email) {
    const who = lead.assigned_to_label ?? lead.assigned_to_email;
    const stage = assignmentStatusLabel(lead.assignment_status);
    states.push({
      key: 'assignment',
      field: 'Assigned to',
      label: who,
      qualifier: stage || undefined,
      variant: stage ? assignmentStatusVariant(lead.assignment_status) : 'success',
      detail: stage ? `Assigned to ${who} · ${stage}` : `Assigned to ${who}`,
      source: LEAD_ASSIGNMENTS,
    });
  }
  const relationship = relationshipLabel(lead);
  if (relationship !== 'Other') {
    states.push({
      key: 'relationship',
      field: 'Relationship',
      label: relationship,
      variant: relationshipVariant(lead),
      detail: `Relationship: ${relationship}`,
      source: BORROWER_360,
    });
  }
  if (typeof lead.aging_days === 'number' && lead.aging_days > 7) {
    states.push({
      key: 'aging',
      field: 'Aging',
      label: `${lead.aging_days}d aging`,
      variant: 'warning',
      detail: `Aging: ${lead.aging_days} days`,
      source: LIFECYCLE_STATE,
    });
  }
  if (typeof lead.owner_count === 'number' && lead.owner_count > 1) {
    states.push({
      key: 'multi_owner',
      field: 'Owners',
      label: `Multi-owner (${lead.owner_count})`,
      variant: 'neutral',
      detail: `Multi-owner (${lead.owner_count})`,
      source: BORROWER_360,
    });
  }
  return states;
}

/**
 * The Sales ops Assigned-to cell (audit tables-05): the assignee is the one
 * primary chip and the lifecycle stage is its `+n`, so the stage the old
 * stacked column showed is still named in the row (hover card and
 * accessible name) without a second chip on the line.
 */
export function leadAssignmentEntries(lead: LeadSummary): LeadStatusEntry[] {
  const assignment = leadWorkflowStates(lead).find((entry) => entry.key === 'assignment');
  if (!assignment) return [];
  const { qualifier: stage, ...assignee } = assignment;
  if (!stage) return [assignee];
  return [
    assignee,
    {
      key: 'assignment_stage',
      field: 'Stage',
      label: stage,
      variant: assignment.variant,
      detail: `Assignment stage: ${stage}`,
      source: LEAD_ASSIGNMENTS,
    },
  ];
}

/**
 * Contactability caveats. These are compliance signals: they always render
 * in-row, in full, and are never folded into a `+n` overflow.
 */
export interface LeadComplianceFlag {
  key: 'owner_unresolved' | 'dnc' | 'suppressed';
  label: string;
  variant: LeadChipVariant;
  icon?: IconName;
  title: string;
}

export function leadComplianceFlags(lead: LeadSummary): LeadComplianceFlag[] {
  const flags: LeadComplianceFlag[] = [];
  const source = lead.eligibility_source ?? 'synthetic_seed';
  if (lead.has_unresolved_owner === true) {
    flags.push({
      key: 'owner_unresolved',
      label: 'Owner unresolved',
      variant: 'warning',
      icon: 'shield',
      title: 'Owner Link could not resolve the owner of record; suppressed from marketing (unresolved_owner).',
    });
  }
  if (lead.dnc === true) {
    flags.push({ key: 'dnc', label: 'DNC', variant: 'danger', title: `DNC source: ${source}` });
  }
  // Every non-DNC row that is not marketing-eligible reads "Suppressed" in
  // its visible text, including the unresolved-owner rows gold stamps
  // suppression_reason=unresolved_owner: "Owner unresolved" names the cause,
  // not the suppression, and the CONTACTABILITY "Suppressed only" filter
  // returns these rows. A DNC row keeps its DNC flag (as before this lane).
  if (lead.marketing_eligible === false && lead.dnc !== true) {
    flags.push({
      key: 'suppressed',
      label: 'Suppressed',
      variant: 'warning',
      title: lead.suppression_reason
        ? `Suppressed: ${lead.suppression_reason} (eligibility source: ${source})`
        : `Eligibility source: ${source}`,
    });
  }
  return flags;
}
